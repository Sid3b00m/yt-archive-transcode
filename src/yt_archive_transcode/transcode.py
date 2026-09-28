"""
Making a smaller copy of a video too large to play smoothly.

An archive that always takes the best stream YouTube offers fills up with 4K,
and a browser that cannot hand 4K to a hardware decoder falls back to decoding
it in software. When that cannot keep up, the audio is what audibly breaks --
stuttering and dropouts -- even though the audio stream is perfectly sound.
That is the problem this exists to fix: not disk space, but playability.

Two rules shape everything here.

The original is never touched. A companion is written beside it and the player
offers the choice; nothing is replaced, and a failed encode leaves the archive
exactly as it was.

The audio is copied, never re-encoded. Sound is the thing that was wrong in the
first place and the audio stream was never the cause, so it is carried across
bit for bit. That is what decides the container: the companion has to be in a
container the existing audio is legal in. A source whose audio fits nowhere is
declined rather than quietly rebuilt.
"""

from __future__ import annotations

import json
import logging
import os
import shutil
import subprocess
import time
from dataclasses import dataclass, field
from pathlib import Path

from .archive import INFO_JSON, media_files, rendition_name, video_dirs, video_id_of

log = logging.getLogger("yt_archive_transcode")

# The height a companion is made at. 1080 is where hardware decoding becomes
# reliable again on ordinary machines, which is the whole objective.
TARGET_HEIGHT = 1080

# How long a video may be before the batch leaves it alone. A companion for a
# ten-hour upload is still enormous and takes most of a day to encode, which is
# a poor trade for something nobody scrubs through looking for detail.
DEFAULT_MAX_MINUTES = 60.0

# Which audio codecs each container will carry, so the audio can be copied.
#
# Opus in mp4 is standardised and both Chrome and Firefox play it, which is
# what lets the NVIDIA and AMD encoders -- neither of which can produce VP9 --
# still carry an Opus track across untouched instead of rebuilding it as AAC.
# Safari is the exception, which is why a webm encoder remains the default
# wherever there is a choice.
WEBM_AUDIO = frozenset({"opus", "vorbis"})
MP4_AUDIO = frozenset({"aac", "mp3", "opus"})

SKIPPED = "skipped"
MADE = "made"
FAILED = "failed"

# Work happens in a subdirectory so a half-written companion is never visible
# in the video directory, where the archiver would index and offer it.
WORK_DIR = ".transcode"


class TranscodeError(RuntimeError):
    """This companion cannot be made, as opposed to the run being over."""


@dataclass(frozen=True)
class Encoder:
    """One way of producing a companion, and what it implies."""

    name: str
    ffmpeg_name: str
    suffix: str
    args: tuple[str, ...]
    note: str

    @property
    def audio_codecs(self) -> frozenset[str]:
        return WEBM_AUDIO if self.suffix == ".webm" else MP4_AUDIO


ENCODERS: dict[str, Encoder] = {
    "vp9": Encoder(
        "vp9", "libvpx-vp9", ".webm",
        ("-crf", "31", "-b:v", "0", "-row-mt", "1", "-deadline", "good", "-cpu-used", "4"),
        "CPU VP9. Correct everywhere and far the slowest, roughly five times slower than real time.",
    ),
    "vp9-fast": Encoder(
        "vp9-fast", "libvpx-vp9", ".webm",
        ("-crf", "34", "-b:v", "0", "-row-mt", "1", "-deadline", "realtime", "-cpu-used", "8"),
        "CPU VP9 in a hurry. Several times quicker than vp9, and visibly softer.",
    ),
    "x264": Encoder(
        "x264", "libx264", ".mp4",
        ("-crf", "21", "-preset", "veryfast", "-pix_fmt", "yuv420p", "-movflags", "+faststart"),
        "CPU H.264. About four times quicker than vp9, and decoded in hardware by anything.",
    ),
    "nvenc": Encoder(
        "nvenc", "h264_nvenc", ".mp4",
        ("-preset", "p5", "-rc", "vbr", "-cq", "23", "-pix_fmt", "yuv420p",
         "-movflags", "+faststart"),
        "NVIDIA H.264. The fastest option on an NVIDIA card, and needs one present.",
    ),
    "amf": Encoder(
        "amf", "h264_amf", ".mp4",
        ("-quality", "balanced", "-rc", "cqp", "-qp_i", "23", "-qp_p", "23",
         "-pix_fmt", "yuv420p", "-movflags", "+faststart"),
        "AMD H.264, for a Radeon card.",
    ),
    "qsv": Encoder(
        "qsv", "vp9_qsv", ".webm",
        ("-global_quality", "31", "-pix_fmt", "nv12"),
        "Intel Quick Sync VP9. Fast and stays in webm; needs a 10th-generation or newer Intel iGPU.",
    ),
}

# Not an encoder but a choice between them, made per video from the audio it
# already has: an Opus source stays webm and an AAC source stays mp4, so either
# way the sound is copied rather than rebuilt. Naming an encoder explicitly
# overrides this and fixes the container, which is what a run on a machine with
# particular hardware wants.
AUTO = "auto"
DEFAULT_ENCODER = AUTO


def resolve_encoder(choice: str, audio_codec: str) -> str | None:
    """Which encoder to actually use, or None if the audio fits no container."""
    if choice != AUTO:
        return choice
    if audio_codec in WEBM_AUDIO:
        return "vp9"
    if audio_codec in MP4_AUDIO:
        return "x264"
    return None


@dataclass
class Plan:
    video_id: str
    directory: Path
    source: Path
    width: int = 0
    height: int = 0
    duration: float = 0.0
    video_codec: str = ""
    audio_codec: str = ""
    target_width: int = 0
    target_height: int = 0
    target_suffix: str = ""
    encoder: str = DEFAULT_ENCODER
    action: str = SKIPPED
    reason: str = ""

    @property
    def label_height(self) -> int:
        """
        The number the companion is named for: its shorter side.

        A resolution is named for the shorter side, which is why a portrait
        phone video at 1080x1920 is a 1080p video and not a 1920p one. Naming
        the file by its height would label it wrongly in the player, and would
        stop a remembered choice of 1080p from matching the copy that is in
        fact 1080p.
        """
        return min(self.target_width, self.target_height) or self.target_height

    @property
    def target(self) -> Path:
        return self.directory / rendition_name(self.label_height, self.target_suffix)


@dataclass
class Result:
    video_id: str
    directory: Path
    action: str = SKIPPED
    detail: str = ""
    source_bytes: int = 0
    made_bytes: int = 0
    seconds: float = 0.0
    errors: list[str] = field(default_factory=list)


# --------------------------------------------------------------------- sizing


def target_size(width: int, height: int, cap: int = TARGET_HEIGHT) -> tuple[int, int] | None:
    """
    The companion's dimensions, or None when the video is already small enough.

    The cap applies to the shorter side rather than to the height, because a
    portrait video is not oversized for being 1920 tall -- a phone recording at
    1080x1920 is already a 1080p video, and capping its height would shrink it
    to 607 wide for no reason. Treating the shorter side as the measure makes
    both orientations the same picture turned sideways, which is what they are.

    Both dimensions come back even, which every encoder here requires.
    """
    if width <= 0 or height <= 0:
        return None
    shorter = min(width, height)
    if shorter <= cap:
        return None
    scale = cap / shorter
    return (max(2, round(width * scale / 2) * 2), max(2, round(height * scale / 2) * 2))


# -------------------------------------------------------------------- probing


def tool(name: str, tools_dir: Path | None = None) -> str:
    """The bundled binary if there is one, else whatever is on PATH."""
    if tools_dir:
        exe = Path(tools_dir) / (f"{name}.exe" if os.name == "nt" else name)
        if exe.exists():
            return str(exe)
    found = shutil.which(name)
    if not found:
        where = f"{tools_dir} or " if tools_dir else ""
        raise TranscodeError(f"{name} not found in {where}PATH")
    return found


def _run(cmd: list[str]) -> subprocess.CompletedProcess:
    """
    Run a tool, decoding output as UTF-8.

    The encoding is not incidental. ffprobe echoes embedded metadata, which
    carries titles from every script there is, and letting Python decode that
    with the Windows default raises UnicodeDecodeError instead of returning
    the JSON.
    """
    return subprocess.run(
        cmd, capture_output=True, text=True, encoding="utf-8", errors="replace", check=False
    )


def probe(path: Path, tools_dir: Path | None = None) -> dict:
    """Dimensions, codecs and duration of one file."""
    result = _run(
        [
            tool("ffprobe", tools_dir),
            "-v", "quiet",
            "-print_format", "json",
            "-show_streams",
            "-show_format",
            str(path),
        ]
    )
    if result.returncode != 0:
        raise TranscodeError(f"ffprobe failed on {path.name}: {result.stderr.strip()[:200]}")
    try:
        data = json.loads(result.stdout)
    except json.JSONDecodeError as exc:
        raise TranscodeError(f"ffprobe returned no usable JSON for {path.name}") from exc

    streams = data.get("streams") or []
    video = next((s for s in streams if s.get("codec_type") == "video"), {})
    audio = next((s for s in streams if s.get("codec_type") == "audio"), {})

    def number(value, default=0.0):
        try:
            return float(value)
        except (TypeError, ValueError):
            return default

    return {
        "width": int(number(video.get("width"))),
        "height": int(number(video.get("height"))),
        "video_codec": video.get("codec_name") or "",
        "audio_codec": audio.get("codec_name") or "",
        "duration": number((data.get("format") or {}).get("duration")),
    }


# ------------------------------------------------------------------- planning


def plan_video(
    vdir: Path,
    *,
    cap: int = TARGET_HEIGHT,
    max_minutes: float = DEFAULT_MAX_MINUTES,
    encoder: str = DEFAULT_ENCODER,
    tools_dir: Path | None = None,
) -> Plan | None:
    """
    Decide whether one video wants a companion. No writes.

    None means there was nothing to consider. A Plan marked `SKIPPED` means it
    was considered and declined, which the batch reports, so a surprising
    exclusion can be read rather than guessed at.
    """
    source = next(iter(media_files(vdir, "video")), None)
    if source is None:
        return None

    plan = Plan(video_id=video_id_of(vdir), directory=vdir, source=source)
    found = probe(source, tools_dir)
    plan.width = found["width"]
    plan.height = found["height"]
    plan.duration = found["duration"]
    plan.video_codec = found["video_codec"]
    plan.audio_codec = found["audio_codec"]

    size = target_size(plan.width, plan.height, cap)
    if size is None:
        return None
    plan.target_width, plan.target_height = size

    if encoder != AUTO and encoder not in ENCODERS:
        raise TranscodeError(
            f"unknown encoder {encoder!r}; choose one of {AUTO}, {', '.join(ENCODERS)}"
        )
    resolved = resolve_encoder(encoder, plan.audio_codec)
    if resolved is None:
        plan.action = SKIPPED
        plan.reason = (
            f"{plan.audio_codec or 'unknown'} audio fits no container here "
            "without re-encoding it, which is not done"
        )
        return plan

    spec = ENCODERS[resolved]
    plan.encoder = resolved
    plan.target_suffix = spec.suffix
    if plan.audio_codec not in spec.audio_codecs:
        plan.action = SKIPPED
        plan.reason = (
            f"{plan.audio_codec or 'unknown'} audio cannot be copied into "
            f"{spec.suffix}, and re-encoding it is not done here"
        )
        return plan

    if plan.target.exists():
        plan.action = SKIPPED
        plan.reason = f"{plan.target.name} is already archived"
        return plan

    if max_minutes and plan.duration > max_minutes * 60:
        plan.action = SKIPPED
        plan.reason = (
            f"{plan.duration / 60:.0f} minutes long, over the {max_minutes:.0f}-minute limit"
        )
        return plan

    plan.action = MADE
    plan.reason = (
        f"{plan.width}x{plan.height} {plan.video_codec} -> "
        f"{plan.target_width}x{plan.target_height} via {resolved}, "
        f"{plan.audio_codec} audio copied"
    )
    return plan


def survey(
    root: Path,
    *,
    video_ids: set[str] | None = None,
    cap: int = TARGET_HEIGHT,
    max_minutes: float = DEFAULT_MAX_MINUTES,
    encoder: str = DEFAULT_ENCODER,
    tools_dir: Path | None = None,
) -> list[Plan]:
    """Every video the archive holds that wants a companion."""
    plans: list[Plan] = []
    for vdir in video_dirs(root, video_ids):
        try:
            plan = plan_video(
                vdir, cap=cap, max_minutes=max_minutes, encoder=encoder, tools_dir=tools_dir
            )
        except TranscodeError as exc:
            log.warning("cannot inspect %s: %s", vdir.name, exc)
            continue
        if plan is not None:
            plans.append(plan)
    return plans


# ------------------------------------------------------------------- encoding


def encode(plan: Plan, dest: Path, tools_dir: Path | None = None) -> None:
    """Write the companion. Video is re-encoded; audio is copied untouched."""
    spec = ENCODERS[plan.encoder]
    cmd = [
        tool("ffmpeg", tools_dir), "-y", "-nostdin", "-loglevel", "error",
        "-i", str(plan.source),
        "-map", "0:v:0", "-map", "0:a:0",
        "-vf", f"scale={plan.target_width}:{plan.target_height}",
        "-c:a", "copy",
        "-c:v", spec.ffmpeg_name,
        *spec.args,
        str(dest),
    ]
    result = _run(cmd)
    if result.returncode != 0 or not dest.exists():
        # ffmpeg lists every encoder it was built with whether or not the
        # hardware behind one is present, so a hardware encoder that is simply
        # unavailable fails here rather than at startup.
        raise TranscodeError(
            f"{spec.ffmpeg_name} could not write {dest.name}: {result.stderr.strip()[:300]}"
        )


def verify(path: Path, plan: Plan, tools_dir: Path | None = None) -> None:
    """Refuse a companion that lost its sound, or came out the wrong size or length."""
    found = probe(path, tools_dir)
    if not found["audio_codec"]:
        raise TranscodeError("the companion has no audio stream")
    if found["audio_codec"] != plan.audio_codec:
        raise TranscodeError(
            f"audio became {found['audio_codec']}, expected {plan.audio_codec} copied across"
        )
    if found["width"] != plan.target_width or found["height"] != plan.target_height:
        raise TranscodeError(
            f"came out {found['width']}x{found['height']}, "
            f"expected {plan.target_width}x{plan.target_height}"
        )
    if plan.duration and abs(found["duration"] - plan.duration) > 2.0:
        raise TranscodeError(
            f"duration changed from {plan.duration:.1f}s to {found['duration']:.1f}s"
        )


def apply_plan(plan: Plan, tools_dir: Path | None = None) -> Result:
    """
    Make one companion, leaving the archive untouched if anything goes wrong.

    Encoded into a work directory and moved into place only once verified, so
    an interrupted run cannot leave a half-written file for the archiver to
    index and offer.
    """
    result = Result(
        video_id=plan.video_id,
        directory=plan.directory,
        detail=plan.reason,
        source_bytes=plan.source.stat().st_size if plan.source.exists() else 0,
    )
    if plan.action != MADE:
        result.action = SKIPPED
        return result

    work = plan.directory / WORK_DIR
    shutil.rmtree(work, ignore_errors=True)
    work.mkdir(parents=True, exist_ok=True)
    started = time.monotonic()
    try:
        staged = work / plan.target.name
        encode(plan, staged, tools_dir)
        verify(staged, plan, tools_dir)
        os.replace(staged, plan.target)
        result.action = MADE
        result.made_bytes = plan.target.stat().st_size
        result.seconds = time.monotonic() - started
        log.info(
            "made %s for %s: %.0f MB from %.0f MB in %.0fs",
            plan.target.name,
            plan.video_id,
            result.made_bytes / 1_000_000,
            result.source_bytes / 1_000_000,
            result.seconds,
        )
    except (TranscodeError, OSError, ValueError) as exc:
        result.action = FAILED
        result.errors.append(str(exc))
        log.warning("could not make a companion for %s: %s", plan.video_id, exc)
    except Exception as exc:  # noqa: BLE001 - one bad video must not end the batch
        result.action = FAILED
        result.errors.append(f"{type(exc).__name__}: {exc}")
        log.exception("unexpected failure making a companion for %s", plan.video_id)
    finally:
        shutil.rmtree(work, ignore_errors=True)
    return result


def run(
    root: Path,
    *,
    video_ids: set[str] | None = None,
    cap: int = TARGET_HEIGHT,
    max_minutes: float = DEFAULT_MAX_MINUTES,
    encoder: str = DEFAULT_ENCODER,
    limit: int | None = None,
    dry_run: bool = False,
    tools_dir: Path | None = None,
    on_result=None,
) -> list[Result]:
    """
    Make companions for everything that wants one.

    Nothing here touches the network, so there is no pacing and no politeness
    gap. What it spends is time, a great deal of it, so the batch is resumable:
    a companion already on disk is skipped, and stopping the run loses at most
    the file being encoded.
    """
    plans = survey(
        root,
        video_ids=video_ids,
        cap=cap,
        max_minutes=max_minutes,
        encoder=encoder,
        tools_dir=tools_dir,
    )
    if dry_run:
        return [
            Result(
                video_id=p.video_id,
                directory=p.directory,
                action=p.action,
                detail=p.reason,
                source_bytes=p.source.stat().st_size if p.source.exists() else 0,
            )
            for p in plans
        ]

    wanted = [p for p in plans if p.action == MADE]
    if limit is not None:
        wanted = wanted[:limit]

    results: list[Result] = []
    for plan in wanted:
        result = apply_plan(plan, tools_dir)
        results.append(result)
        if on_result is not None:
            on_result(result)
    return results


def transcode_one(
    root: Path,
    video_id: str,
    *,
    cap: int = TARGET_HEIGHT,
    max_minutes: float = 0.0,
    encoder: str = DEFAULT_ENCODER,
    tools_dir: Path | None = None,
) -> Result:
    """
    Make a companion for one named video.

    The length limit defaults off here. It exists to stop an unattended batch
    spending a day on a single file, and someone naming one video has already
    decided it is worth it.
    """
    from .archive import locate

    vdir = locate(root, video_id)
    if vdir is None:
        raise TranscodeError(f"{video_id} is not in the archive")

    plan = plan_video(
        vdir, cap=cap, max_minutes=max_minutes, encoder=encoder, tools_dir=tools_dir
    )
    if plan is None:
        source = next(iter(media_files(vdir, "video")), None)
        return Result(
            video_id=video_id,
            directory=vdir,
            action=SKIPPED,
            detail=(
                "already no larger than the cap, so it plays as it is"
                if source
                else "no video file is archived for this video"
            ),
        )
    return apply_plan(plan, tools_dir)
