import json

import pytest


@pytest.fixture
def archive_root(tmp_path):
    """An empty archive root, in the layout the archiver writes."""
    root = tmp_path / "archive"
    root.mkdir()
    return root


@pytest.fixture
def make_video(archive_root):
    """Write a video directory that looks like a finished download."""

    def build(
        video_id="vid00000001",
        *,
        channel_folder="Test Channel",
        channel_id="UC" + "x" * 22,
        title="A test video",
        video_ext="webm",
        with_audio=True,
        extra=None,
    ):
        channel_dir = archive_root / channel_folder
        vdir = channel_dir / "videos" / video_id
        vdir.mkdir(parents=True, exist_ok=True)

        manifest = channel_dir / "channel.json"
        if not manifest.exists():
            manifest.write_text(
                json.dumps({"channel_id": channel_id, "name": channel_folder}),
                encoding="utf-8",
            )

        (vdir / f"video.{video_ext}").write_bytes(b"\x00" * 2048)
        if with_audio:
            (vdir / "audio.webm").write_bytes(b"\x00" * 1024)
        (vdir / "info.json").write_text(
            json.dumps({"id": video_id, "title": title, **(extra or {})}),
            encoding="utf-8",
        )
        return vdir

    return build


@pytest.fixture
def probed(monkeypatch):
    """Stand in for ffprobe, answering per filename."""
    from yt_archive_transcode import transcode

    def install(answers=None, default=None):
        base = default or {
            "width": 3840,
            "height": 2160,
            "video_codec": "vp9",
            "audio_codec": "opus",
            "duration": 61.0,
        }

        def probe(path, tools_dir=None):
            for name, answer in (answers or {}).items():
                if path.name == name:
                    return {**base, **answer}
            return base

        monkeypatch.setattr(transcode, "probe", probe)

    return install


@pytest.fixture
def encodes(monkeypatch):
    """Let the encode succeed by writing a file, so only verify decides."""
    from yt_archive_transcode import transcode

    def install(size=4096, verified=True):
        monkeypatch.setattr(
            transcode, "encode", lambda plan, dest, tools_dir=None: dest.write_bytes(b"\x00" * size)
        )
        if verified:
            monkeypatch.setattr(transcode, "verify", lambda path, plan, tools_dir=None: None)

    return install
