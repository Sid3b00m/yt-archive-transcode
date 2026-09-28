# yt-archive-transcode

Smaller copies of archived videos, for players that cannot manage the originals.

Companion to [yt-archive-ghost](https://github.com/Sid3b00m/yt-archive-ghost). It
reads that archive, writes smaller copies beside the originals, and stops there.

## The problem

An archive that always takes the best stream YouTube offers fills up with 4K. A
browser that cannot hand 4K to a hardware decoder falls back to decoding it in
software, and when that cannot keep up, **the audio is what audibly breaks** —
stuttering, gaps, crackling — even though the audio stream is perfectly sound.

It is easy to misread as a bad download. It is not: the file is fine and the
machine cannot play it in real time. Non-standard frame sizes make it worse,
because hardware decoders generally refuse anything that is not a shape they
recognise, and a 3240x2160 upload is exactly that.

A 1080p copy alongside the original fixes it, and the player offers the choice.

## What it will not do

**It never touches the original.** A companion is written beside it. A failed
encode leaves the archive exactly as it was.

**It never re-encodes the audio.** Sound is the thing that was wrong and the
audio stream was never the cause, so it is copied across bit for bit. A
companion whose audio arrives as a different codec is thrown away rather than
kept.

That second rule is why the container is not a free choice. The companion has
to be something the existing audio is legal in: an Opus source stays in webm, an
AAC source stays in mp4. A source whose audio fits nowhere is declined and says
so, rather than being quietly rebuilt.

## Install

Python 3.11 or newer, plus `ffmpeg` and `ffprobe`. There are no Python
dependencies at all — that is the point. The machine doing the encoding needs no
archiver, no database and no yt-dlp.

```bash
git clone https://github.com/Sid3b00m/yt-archive-transcode.git
cd yt-archive-transcode
```

Run it with `src` on the path:

```bash
PYTHONPATH=src python -m yt_archive_transcode --help
```

```powershell
$env:PYTHONPATH="src"; python -m yt_archive_transcode --help
```

If ffmpeg is not on PATH, point at it with `--tools-dir`.

## Use

Look before leaping. Nothing is written:

```bash
python -m yt_archive_transcode --root /path/to/archive --dry-run
```

Then make them:

```bash
python -m yt_archive_transcode --root /path/to/archive
```

`YTARCHIVE_ROOT` is read when `--root` is absent, so the archiver's own
environment variable works unchanged.

Useful flags:

| Flag | What it does |
| --- | --- |
| `--height 1080` | Cap the **shorter** side. Default 1080. |
| `--max-minutes 60` | Leave longer videos alone. `0` for no limit. |
| `--encoder nvenc` | Pick an encoder. `list` describes them all. |
| `--video ID [ID…]` | Only these videos. |
| `--limit 1` | Stop after this many. Worth using first. |
| `--tools-dir DIR` | Where ffmpeg and ffprobe live. |

Stopping it is safe. Anything finished is on disk and is skipped next time; the
file being encoded was in a work directory that is cleaned up on the way out.

## Encoders

`--encoder list` prints this with the details. The short version:

| Encoder | Container | Speed | Needs |
| --- | --- | --- | --- |
| `auto` | follows the audio | — | the default |
| `vp9` | webm | ~5x slower than real time | nothing |
| `vp9-fast` | webm | several times quicker, softer | nothing |
| `x264` | mp4 | ~4x quicker than `vp9` | nothing |
| `nvenc` | mp4 | very fast | an NVIDIA card |
| `amf` | mp4 | very fast | a Radeon card |
| `qsv` | webm | very fast | Intel iGPU, 10th gen or newer |

`auto` picks per video from the audio it already has, so an Opus source stays
webm and an AAC source stays mp4. Naming an encoder instead fixes the container,
which is what a run on particular hardware wants.

**ffmpeg lists a hardware encoder whether or not the hardware is there.** The
only way to find out is to try one:

```bash
python -m yt_archive_transcode --root /path/to/archive --encoder nvenc --limit 1
```

On the encoders that force mp4, Opus audio is still copied rather than rebuilt —
Opus in mp4 is standardised and both Chrome and Firefox play it. Safari does not,
which is why a webm encoder stays the default where there is a choice.

## Running it on another machine

This is the reason it is a separate project. Encoding is slow and wants the
fastest machine available, which is rarely the one serving the archive.

Nothing needs to be installed on that machine beyond Python and ffmpeg, and
nothing needs to happen afterwards. **The archiver discovers companions by
looking in the directory**, so one written by another machine is offered as soon
as the file is in place — no database to update, no service to restart, no
reindex.

Point it at the archive over a share, or copy video directories across and copy
the companions back. Either works.

## How a companion is named

The contract between this tool and the archiver, and the one thing that must not
drift:

```
<archive root>/<Channel Name>/videos/<video id>/
    video.webm          the archived original, untouched
    video-1080p.webm    a companion this tool wrote
```

Two rules:

**Named for the shorter side.** A portrait 1080x1920 copy is a 1080p video, not
a 1920p one. That is how resolutions are named, and the player's remembered
choice matches on it.

**The `video-` prefix is load-bearing.** The archiver serves whatever matches the
bare `video.*` stem exactly, so the prefix is the only thing stopping a smaller
copy being mistaken for the original and served as the archive.

## Why capping the shorter side

A portrait video is not oversized for being 1920 tall. A phone recording at
1080x1920 is already a 1080p video, and capping its *height* would shrink it to
607 wide for no reason. Capping the shorter side treats both orientations as the
same picture turned sideways, which is what they are.

## Tests

```bash
PYTHONPATH=src python -m pytest
```

No network, no ffmpeg needed: the encoder and the probe are stubbed, so what is
under test is the decisions — which videos want a companion, what it is called,
which container it lands in, and what gets refused.

## Honest constraints

**Re-encoding loses quality.** Fewer pixels is a different encode; there is no
lossless way down. The original stays, so nothing is lost permanently, but the
companion is not as good as a 1080p download from YouTube would have been.

**It is slow.** Even the fast encoders are doing real work on every frame. A
large archive is a long unattended run, which is why `--limit` and `--dry-run`
exist and why stopping is safe.

**Hardware encoders are not checked up front.** ffmpeg will claim to have them
and fail at the first frame. Try one video.
