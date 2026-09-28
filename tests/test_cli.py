"""
The command line, which is the only interface.

Most of these are about refusing clearly. The tool writes into somebody's
archive, so being pointed at the wrong directory should stop it before it
starts rather than produce a confusing nothing.
"""

from yt_archive_transcode import cli, transcode


class TestRefusingClearly:
    def test_it_needs_an_archive(self, monkeypatch, capsys):
        monkeypatch.delenv(cli.ENV_ROOT, raising=False)
        assert cli.main([]) == 2
        assert "no archive given" in capsys.readouterr().err

    def test_a_missing_directory_is_named(self, tmp_path, capsys):
        assert cli.main(["--root", str(tmp_path / "nowhere")]) == 2
        assert "no such directory" in capsys.readouterr().err

    def test_a_directory_that_is_not_an_archive_says_why(self, tmp_path, capsys):
        """
        Being pointed at the wrong folder is the easy mistake, and an empty run
        looks identical to a finished one unless it is called out.
        """
        assert cli.main(["--root", str(tmp_path)]) == 2
        assert "does not look like an archive" in capsys.readouterr().err

    def test_an_unknown_encoder_is_refused(self, archive_root, make_video, capsys):
        make_video()
        assert cli.main(["--root", str(archive_root), "--encoder", "nonsense"]) == 2
        assert "unknown encoder" in capsys.readouterr().err


class TestTheEnvironmentVariable:
    def test_the_root_can_come_from_the_environment(
        self, archive_root, make_video, probed, monkeypatch, capsys
    ):
        """
        So the archiver's own YTARCHIVE_ROOT works unchanged on a machine that
        has the archive mounted.
        """
        make_video()
        probed()
        monkeypatch.setenv(cli.ENV_ROOT, str(archive_root))
        assert cli.main(["--dry-run"]) == 0
        assert "would be made" in capsys.readouterr().out


class TestRunning:
    def test_a_dry_run_lists_without_writing(
        self, archive_root, make_video, probed, capsys
    ):
        vdir = make_video()
        probed()
        assert cli.main(["--root", str(archive_root), "--dry-run"]) == 0
        out = capsys.readouterr().out
        assert "vid00000001" in out and "would be made" in out
        assert not (vdir / "video-1080p.webm").exists()

    def test_it_makes_a_companion_and_reports_the_saving(
        self, archive_root, make_video, probed, encodes, capsys
    ):
        vdir = make_video()
        probed()
        encodes()
        assert cli.main(["--root", str(archive_root)]) == 0
        out = capsys.readouterr().out
        assert "made 1 companion" in out
        assert "smaller than its original" in out
        assert (vdir / "video-1080p.webm").exists()

    def test_nothing_to_do_says_so(self, archive_root, make_video, probed, capsys):
        make_video()
        probed(default={"width": 1280, "height": 720, "video_codec": "vp9",
                        "audio_codec": "opus", "duration": 61.0})
        assert cli.main(["--root", str(archive_root)]) == 0
        assert "nothing to do" in capsys.readouterr().out

    def test_a_failure_is_reported_and_changes_the_exit_code(
        self, archive_root, make_video, probed, monkeypatch, capsys
    ):
        make_video()
        probed()
        monkeypatch.setattr(
            transcode, "encode",
            lambda *a, **k: (_ for _ in ()).throw(transcode.TranscodeError("no encoder here")),
        )
        assert cli.main(["--root", str(archive_root)]) == 1
        assert "FAILED" in capsys.readouterr().out


class TestDescribingEncoders:
    def test_list_describes_each_one(self, capsys):
        assert cli.main(["--encoder", "list"]) == 0
        out = capsys.readouterr().out
        for name in transcode.ENCODERS:
            assert name in out
        assert transcode.AUTO in out

    def test_it_warns_that_hardware_is_not_checked(self, capsys):
        """
        ffmpeg advertises encoders it was built with, not ones the machine can
        actually use, which is a trap worth naming in the help itself.
        """
        cli.main(["--encoder", "list"])
        assert "whether or not the hardware is present" in capsys.readouterr().out
