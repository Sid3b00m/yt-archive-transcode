"""
The command line.

Deliberately the only interface. This tool writes files into an archive and
then has nothing more to do with them: the archiver discovers companions by
looking in the directory, so there is no database to update, no service to
notify, and nothing to run afterwards. That is what makes it safe to run on a
different machine from the one serving the archive.
"""

from __future__ import annotations

import argparse
import logging
import os
import sys
from pathlib import Path

from . import __version__, transcode
from .archive import looks_like_an_archive

ENV_ROOT = "YTARCHIVE_ROOT"


def _logging(verbose: bool) -> None:
    logging.basicConfig(
        level=logging.DEBUG if verbose else logging.INFO,
        format="%(message)s",
        stream=sys.stderr,
    )


def _gb(n: float) -> str:
    return f"{n / 1_000_000_000:.1f} GB"


def _mb(n: float) -> str:
    return f"{n / 1_000_000:,.0f} MB"


def describe_encoders() -> int:
    print("encoders:\n")
    print(f"  {transcode.AUTO:<9} {'(per video)':<12}")
    print("            Follows each video's own audio so it can be copied: an Opus")
    print("            source stays webm, an AAC source stays mp4. The default.\n")
    for name, spec in transcode.ENCODERS.items():
        print(f"  {name:<9} {spec.ffmpeg_name:<12} -> {spec.suffix}")
        print(f"            {spec.note}")
        print(f"            copies {', '.join(sorted(spec.audio_codecs))} audio; anything else is skipped\n")
    print("ffmpeg lists a hardware encoder whether or not the hardware is present,")
    print("so the only way to know one works here is to try it on a single video:")
    print("  yt-archive-transcode --encoder nvenc --limit 1")
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="yt-archive-transcode",
        description=(
            "Make smaller copies of archived videos that are too large for a browser "
            "to decode in real time. The originals are never touched and the audio is "
            "copied rather than re-encoded."
        ),
    )
    parser.add_argument("--version", action="version", version=f"%(prog)s {__version__}")
    parser.add_argument(
        "--root",
        help=f"the archive root (default: the {ENV_ROOT} environment variable)",
    )
    parser.add_argument("--video", nargs="+", help="only these video ids")
    parser.add_argument("--limit", type=int, help="stop after this many videos")
    parser.add_argument(
        "--height",
        type=int,
        default=transcode.TARGET_HEIGHT,
        help=f"cap the shorter side at this (default {transcode.TARGET_HEIGHT})",
    )
    parser.add_argument(
        "--max-minutes",
        type=float,
        default=transcode.DEFAULT_MAX_MINUTES,
        help="leave videos longer than this alone; 0 for no limit "
        f"(default {transcode.DEFAULT_MAX_MINUTES:.0f})",
    )
    parser.add_argument(
        "--encoder",
        default=transcode.DEFAULT_ENCODER,
        help="which encoder to use; 'list' describes them all",
    )
    parser.add_argument(
        "--tools-dir",
        help="a directory holding ffmpeg and ffprobe, if they are not on PATH",
    )
    parser.add_argument(
        "--dry-run", action="store_true", help="list what would be made and exit"
    )
    parser.add_argument("-v", "--verbose", action="store_true")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    _logging(args.verbose)

    if args.encoder == "list":
        return describe_encoders()
    if args.encoder != transcode.AUTO and args.encoder not in transcode.ENCODERS:
        print(f"unknown encoder {args.encoder!r}. Try --encoder list", file=sys.stderr)
        return 2

    root = args.root or os.environ.get(ENV_ROOT)
    if not root:
        print(
            f"no archive given. Pass --root, or set {ENV_ROOT}.",
            file=sys.stderr,
        )
        return 2
    root = Path(root).expanduser()
    if not root.exists():
        print(f"no such directory: {root}", file=sys.stderr)
        return 2
    if not looks_like_an_archive(root):
        print(
            f"{root} does not look like an archive: no <channel>/channel.json in it.",
            file=sys.stderr,
        )
        return 2

    tools_dir = Path(args.tools_dir).expanduser() if args.tools_dir else None

    def show(result) -> None:
        if result.action == transcode.FAILED:
            print(f"  FAILED  {result.video_id}: {'; '.join(result.errors)}")
            return
        print(
            f"  made    {result.video_id}  "
            f"{_mb(result.source_bytes)} -> {_mb(result.made_bytes)}  "
            f"in {result.seconds:.0f}s"
        )

    try:
        results = transcode.run(
            root,
            video_ids=set(args.video) if args.video else None,
            cap=args.height,
            max_minutes=args.max_minutes,
            encoder=args.encoder,
            limit=args.limit,
            dry_run=args.dry_run,
            tools_dir=tools_dir,
            on_result=None if args.dry_run else show,
        )
    except transcode.TranscodeError as exc:
        print(f"cannot start: {exc}", file=sys.stderr)
        return 2
    except KeyboardInterrupt:
        # Resumable by design: anything finished is on disk, and the file being
        # encoded was in a work directory that is cleaned up on the way out.
        print("\nstopped. Companions already made are kept; run again to carry on.")
        return 130

    if not results:
        print(f"nothing to do: no archived video is larger than {args.height}p")
        return 0

    if args.dry_run:
        wanted = [r for r in results if r.action == transcode.MADE]
        for result in results:
            mark = "make" if result.action == transcode.MADE else "skip"
            print(f"  {mark}  {result.video_id}  {result.detail}")
        total = sum(r.source_bytes for r in wanted)
        print(f"\n{len(wanted)} companion(s) would be made, from {_gb(total)} of source")
        print("run without --dry-run to make them")
        return 0

    made = [r for r in results if r.action == transcode.MADE]
    failed = [r for r in results if r.action == transcode.FAILED]
    print(f"\nmade {len(made)} companion(s), {len(failed)} failed")
    if made:
        source = sum(r.source_bytes for r in made)
        companions = sum(r.made_bytes for r in made)
        print(f"they take {_gb(companions)} beside {_gb(source)} of originals")
        if source:
            print(f"each is about {100 - companions / source * 100:.0f}% smaller than its original")
    return 1 if failed else 0
