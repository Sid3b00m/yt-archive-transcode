"""
Making a companion.

Two promises carry the weight, and most of these exist to hold them down: the
original is never touched, and the audio is copied rather than re-encoded.
Everything else follows from those, including which container a companion ends
up in.
"""

import pytest

from yt_archive_transcode import transcode
from yt_archive_transcode.archive import media_files, renditions


class TestTargetSize:
    def test_a_landscape_4k_video_becomes_1080p(self):
        assert transcode.target_size(3840, 2160) == (1920, 1080)

    def test_an_unusual_width_keeps_its_shape(self):
        assert transcode.target_size(3240, 2160) == (1620, 1080)

    def test_a_portrait_video_is_capped_on_its_narrow_side(self):
        """
        Capping the height would shrink a phone recording to 607 wide for no
        reason. A portrait video is the same picture turned sideways.
        """
        assert transcode.target_size(2160, 3840) == (1080, 1920)

    def test_something_already_small_enough_is_left_alone(self):
        assert transcode.target_size(1920, 1080) is None
        assert transcode.target_size(1080, 1920) is None
        assert transcode.target_size(1280, 720) is None

    def test_dimensions_come_back_even(self):
        width, height = transcode.target_size(2999, 2161)
        assert width % 2 == 0 and height % 2 == 0

    def test_nonsense_dimensions_are_declined(self):
        assert transcode.target_size(0, 0) is None
        assert transcode.target_size(-1, 100) is None


class TestPlanning:
    def test_an_oversized_video_gets_a_plan(self, make_video, probed):
        vdir = make_video()
        probed()
        plan = transcode.plan_video(vdir)
        assert plan.action == transcode.MADE
        assert (plan.target_width, plan.target_height) == (1920, 1080)
        assert plan.target.name == "video-1080p.webm"

    def test_a_video_already_small_enough_is_not_considered(self, make_video, probed):
        vdir = make_video()
        probed(default={"width": 1920, "height": 1080, "video_codec": "vp9",
                        "audio_codec": "opus", "duration": 61.0})
        assert transcode.plan_video(vdir) is None

    def test_a_portrait_companion_is_named_for_its_shorter_side(self, make_video, probed):
        """
        A 1080x1920 phone video is a 1080p video, not a 1920p one. Naming it by
        its height mislabels it, and stops a remembered choice of 1080p from
        matching the copy that is in fact 1080p.
        """
        vdir = make_video()
        probed(default={"width": 2160, "height": 3840, "video_codec": "vp9",
                        "audio_codec": "opus", "duration": 61.0})
        plan = transcode.plan_video(vdir)
        assert (plan.target_width, plan.target_height) == (1080, 1920)
        assert plan.target.name == "video-1080p.webm"

    def test_a_companion_already_on_disk_is_not_remade(self, make_video, probed):
        vdir = make_video()
        (vdir / "video-1080p.webm").write_bytes(b"\x00" * 64)
        probed()
        plan = transcode.plan_video(vdir)
        assert plan.action == transcode.SKIPPED
        assert "already archived" in plan.reason

    def test_a_long_video_is_left_alone_by_the_batch(self, make_video, probed):
        vdir = make_video()
        probed(default={"width": 3840, "height": 2160, "video_codec": "vp9",
                        "audio_codec": "opus", "duration": 6 * 3600.0})
        plan = transcode.plan_video(vdir, max_minutes=60)
        assert plan.action == transcode.SKIPPED
        assert "limit" in plan.reason

    def test_the_length_limit_can_be_lifted(self, make_video, probed):
        vdir = make_video()
        probed(default={"width": 3840, "height": 2160, "video_codec": "vp9",
                        "audio_codec": "opus", "duration": 6 * 3600.0})
        assert transcode.plan_video(vdir, max_minutes=0).action == transcode.MADE

    def test_a_directory_with_no_video_is_passed_over(self, make_video):
        vdir = make_video()
        (vdir / "video.webm").unlink()
        assert transcode.plan_video(vdir) is None


class TestChoosingAnEncoder:
    def test_the_default_follows_the_audio_so_it_can_be_copied(self, make_video, probed):
        """
        Tying the container to one encoder would skip every AAC source, which
        on a real archive is most of it. The default picks per video instead.
        """
        vdir = make_video()
        probed()
        opus_plan = transcode.plan_video(vdir)
        assert opus_plan.encoder == "vp9"
        assert opus_plan.target.name.endswith(".webm")

        probed(default={"width": 3840, "height": 2160, "video_codec": "h264",
                        "audio_codec": "aac", "duration": 61.0})
        aac_plan = transcode.plan_video(vdir)
        assert aac_plan.encoder == "x264"
        assert aac_plan.target.name.endswith(".mp4")
        assert aac_plan.action == transcode.MADE

    def test_nvenc_moves_to_mp4_and_still_copies_opus(self, make_video, probed):
        """
        NVENC cannot produce VP9, so the companion has to be mp4 -- and Opus in
        mp4 is what lets the audio be carried across rather than rebuilt.
        """
        vdir = make_video()
        probed()
        plan = transcode.plan_video(vdir, encoder="nvenc")
        assert plan.action == transcode.MADE
        assert plan.target.name == "video-1080p.mp4"

    def test_audio_that_cannot_survive_the_container_is_declined(self, make_video, probed):
        vdir = make_video()
        probed(default={"width": 3840, "height": 2160, "video_codec": "vp9",
                        "audio_codec": "vorbis", "duration": 61.0})
        plan = transcode.plan_video(vdir, encoder="nvenc")
        assert plan.action == transcode.SKIPPED
        assert "cannot be copied" in plan.reason

    def test_audio_that_fits_nowhere_is_declined_under_auto(self, make_video, probed):
        vdir = make_video()
        probed(default={"width": 3840, "height": 2160, "video_codec": "vp9",
                        "audio_codec": "flac", "duration": 61.0})
        plan = transcode.plan_video(vdir)
        assert plan.action == transcode.SKIPPED
        assert "re-encoding" in plan.reason

    def test_an_unknown_encoder_is_refused(self, make_video, probed):
        vdir = make_video()
        probed()
        with pytest.raises(transcode.TranscodeError):
            transcode.plan_video(vdir, encoder="nonsense")

    def test_every_encoder_declares_a_container_and_a_note(self):
        for name, spec in transcode.ENCODERS.items():
            assert spec.suffix in (".webm", ".mp4"), name
            assert spec.note and spec.audio_codecs

    def test_the_chosen_encoder_reaches_ffmpeg_and_audio_is_copied(
        self, make_video, probed, monkeypatch
    ):
        from pathlib import Path

        vdir = make_video()
        probed()
        seen = {}
        monkeypatch.setattr(transcode, "tool", lambda name, tools_dir=None: name)
        monkeypatch.setattr(transcode, "verify", lambda path, plan, tools_dir=None: None)

        def fake_run(cmd):
            seen["cmd"] = cmd
            Path(cmd[-1]).write_bytes(b"\x00" * 64)
            return type("R", (), {"returncode": 0, "stderr": ""})()

        monkeypatch.setattr(transcode, "_run", fake_run)
        transcode.apply_plan(transcode.plan_video(vdir, encoder="nvenc"))

        assert "h264_nvenc" in seen["cmd"]
        assert seen["cmd"][seen["cmd"].index("-c:a") + 1] == "copy"
        assert "scale=1920:1080" in seen["cmd"]


class TestApplying:
    def test_it_writes_a_companion_and_leaves_the_original(self, make_video, probed, encodes):
        vdir = make_video()
        probed()
        encodes()

        result = transcode.apply_plan(transcode.plan_video(vdir))
        assert result.action == transcode.MADE
        assert (vdir / "video-1080p.webm").exists()
        # The promise: the original is untouched.
        assert (vdir / "video.webm").exists()
        assert (vdir / "video.webm").stat().st_size == 2048

    def test_the_companion_is_not_offered_as_the_archived_video(
        self, make_video, probed, encodes
    ):
        vdir = make_video()
        probed()
        encodes()
        transcode.apply_plan(transcode.plan_video(vdir))
        assert [p.name for p in media_files(vdir, "video")] == ["video.webm"]
        assert [r["height"] for r in renditions(vdir)] == [1080]

    def test_a_failed_encode_leaves_nothing_behind(self, make_video, probed, monkeypatch):
        vdir = make_video()
        probed()
        monkeypatch.setattr(
            transcode, "encode",
            lambda *a, **k: (_ for _ in ()).throw(transcode.TranscodeError("ffmpeg said no")),
        )

        result = transcode.apply_plan(transcode.plan_video(vdir))
        assert result.action == transcode.FAILED
        assert not (vdir / "video-1080p.webm").exists()
        assert not (vdir / transcode.WORK_DIR).exists()
        assert (vdir / "video.webm").exists()

    def test_a_silent_companion_is_refused(self, make_video, probed, encodes):
        """Publishing a silent copy would be worse than publishing none."""
        vdir = make_video()
        probed({"video-1080p.webm": {"audio_codec": "", "width": 1920, "height": 1080}})
        encodes(verified=False)

        result = transcode.apply_plan(transcode.plan_video(vdir))
        assert result.action == transcode.FAILED
        assert "audio" in result.errors[0]
        assert not (vdir / "video-1080p.webm").exists()

    def test_a_companion_whose_audio_was_rebuilt_is_refused(self, make_video, probed, encodes):
        """The audio must arrive as the same codec it left as."""
        vdir = make_video()
        probed({"video-1080p.webm": {"audio_codec": "aac", "width": 1920, "height": 1080}})
        encodes(verified=False)

        result = transcode.apply_plan(transcode.plan_video(vdir))
        assert result.action == transcode.FAILED
        assert "opus" in result.errors[0]

    def test_a_companion_of_the_wrong_size_is_refused(self, make_video, probed, encodes):
        vdir = make_video()
        probed({"video-1080p.webm": {"width": 640, "height": 360}})
        encodes(verified=False)

        result = transcode.apply_plan(transcode.plan_video(vdir))
        assert result.action == transcode.FAILED
        assert not (vdir / "video-1080p.webm").exists()


class TestBatch:
    def test_a_dry_run_changes_nothing(self, archive_root, make_video, probed):
        vdir = make_video()
        probed()
        results = transcode.run(archive_root, dry_run=True)
        assert [r.action for r in results] == [transcode.MADE]
        assert not (vdir / "video-1080p.webm").exists()

    def test_it_skips_what_is_already_done(self, archive_root, make_video, probed):
        """Resumable: stopping the run loses at most the file being encoded."""
        vdir = make_video()
        (vdir / "video-1080p.webm").write_bytes(b"\x00" * 64)
        probed()
        assert transcode.run(archive_root) == []

    def test_the_limit_stops_it_early(self, archive_root, make_video, probed, encodes):
        make_video(video_id="vid00000001")
        make_video(video_id="vid00000002")
        probed()
        encodes()
        assert len(transcode.run(archive_root, limit=1)) == 1

    def test_it_reports_each_one_as_it_goes(self, archive_root, make_video, probed, encodes):
        make_video()
        probed()
        encodes()
        seen = []
        transcode.run(archive_root, on_result=seen.append)
        assert [r.video_id for r in seen] == ["vid00000001"]


class TestOneVideo:
    def test_it_makes_a_companion(self, archive_root, make_video, probed, encodes):
        vdir = make_video()
        probed()
        encodes()
        assert transcode.transcode_one(archive_root, "vid00000001").action == transcode.MADE
        assert (vdir / "video-1080p.webm").exists()

    def test_a_small_video_is_declined_with_a_reason(self, archive_root, make_video, probed):
        make_video()
        probed(default={"width": 1280, "height": 720, "video_codec": "vp9",
                        "audio_codec": "opus", "duration": 61.0})
        result = transcode.transcode_one(archive_root, "vid00000001")
        assert result.action == transcode.SKIPPED
        assert "plays as it is" in result.detail

    def test_an_unknown_video_is_an_error(self, archive_root):
        with pytest.raises(transcode.TranscodeError):
            transcode.transcode_one(archive_root, "doesnotexist")

    def test_naming_one_has_no_length_limit(self, archive_root, make_video, probed, encodes):
        make_video()
        probed(default={"width": 3840, "height": 2160, "video_codec": "vp9",
                        "audio_codec": "opus", "duration": 8 * 3600.0})
        encodes()
        assert transcode.transcode_one(archive_root, "vid00000001").action == transcode.MADE
