"""
Reading the shape of a yt-archive-ghost archive.

This tool writes files into an archive it does not own, so what it knows about
that layout is the contract between the two projects and is kept here rather
than scattered through the encoder. The archiver's side of the same contract
lives in its `download` module.

The layout, as much of it as matters:

    <archive root>/
        <Channel Name>/
            channel.json
            videos/
                <video id>/
                    info.json
                    video.webm          the archived original
                    video-1080p.webm    a companion this tool wrote
                    audio.webm

Two rules carry the weight. A companion is named for its shorter side, because
that is how a resolution is named -- a portrait 1080x1920 copy is a 1080p
video, not a 1920p one. And the `video-` prefix is deliberate: the archiver
picks the video it serves by matching the bare `video.*` stem exactly, so the
prefix is what stops a smaller copy being mistaken for the original.
"""

from __future__ import annotations

import json
from pathlib import Path

CHANNEL_MANIFEST = "channel.json"
INFO_JSON = "info.json"

# Containers an archived stream may be written in, playable or not. Media is
# picked out of a directory by matching these rather than by naming the
# sidecars to skip, because the directory also holds a thumbnail, a
# description, and whatever half-finished files a download left behind.
MEDIA_SUFFIXES = frozenset(
    {".mp4", ".webm", ".mkv", ".m4v", ".ogv", ".m4a", ".opus", ".ogg", ".mp3"}
)

RENDITION_PREFIX = "video-"


def rendition_name(height: int, suffix: str) -> str:
    """What a companion of this height is called."""
    return f"{RENDITION_PREFIX}{height}p{suffix}"


def media_files(vdir: Path, stem: str) -> list[Path]:
    """
    The archived media for one stem.

    The stem has to match exactly. yt-dlp leaves per-format intermediates like
    `video.f251.webm` beside the merged `video.webm`, and f251 is audio only,
    so treating it as the video would mean encoding a companion from silence
    over a blank frame. The same check keeps out `video.webp`, which is a
    thumbnail, and the companions this tool writes.
    """
    return [
        path
        for path in sorted(vdir.glob(f"{stem}.*"))
        if path.is_file() and path.stem == stem and path.suffix.lower() in MEDIA_SUFFIXES
    ]


def renditions(vdir: Path) -> list[dict]:
    """The companions already archived beside one video, smallest first."""
    found = []
    for path in sorted(vdir.glob(f"{RENDITION_PREFIX}*")):
        if not path.is_file() or path.suffix.lower() not in MEDIA_SUFFIXES:
            continue
        label = path.stem[len(RENDITION_PREFIX) :]
        if not label.endswith("p") or not label[:-1].isdigit():
            continue
        found.append({"height": int(label[:-1]), "file": path.name})
    return sorted(found, key=lambda r: r["height"])


def video_id_of(vdir: Path) -> str:
    """
    The id of the video in this directory.

    Taken from `info.json` where possible and from the directory name
    otherwise, which is what the archiver names it anyway; the file is only
    more trustworthy if a directory has been renamed by hand.
    """
    info_path = vdir / INFO_JSON
    if info_path.exists():
        try:
            recorded = (json.loads(info_path.read_text(encoding="utf-8")) or {}).get("id")
            if recorded:
                return str(recorded)
        except (OSError, json.JSONDecodeError):
            pass
    return vdir.name


def video_dirs(root: Path, video_ids: set[str] | None = None):
    """
    Every video directory in the archive, in a stable order.

    Channels are found by their manifest rather than by listing directories,
    so anything else sitting in the archive root is passed over rather than
    walked.
    """
    for manifest in sorted(Path(root).glob(f"*/{CHANNEL_MANIFEST}")):
        videos = manifest.parent / "videos"
        if not videos.exists():
            continue
        for vdir in sorted(p for p in videos.iterdir() if p.is_dir()):
            if video_ids is not None and vdir.name not in video_ids:
                continue
            yield vdir


def locate(root: Path, video_id: str) -> Path | None:
    """The directory holding one video, or None if the archive has no such id."""
    for manifest in sorted(Path(root).glob(f"*/{CHANNEL_MANIFEST}")):
        candidate = manifest.parent / "videos" / video_id
        if candidate.is_dir():
            return candidate
    return None


def looks_like_an_archive(root: Path) -> bool:
    """Whether this path has the shape of an archive, for a clear early error."""
    return any(Path(root).glob(f"*/{CHANNEL_MANIFEST}"))
