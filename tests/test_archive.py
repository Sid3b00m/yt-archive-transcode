"""
Reading the archive's layout.

This tool writes into an archive it does not own, so these pin down the shape
it relies on. If the archiver ever changes how a video directory looks, this is
the file that should fail first.
"""

from yt_archive_transcode import archive


class TestFindingVideos:
    def test_it_walks_every_channel(self, archive_root, make_video):
        make_video(video_id="vid00000001")
        make_video(video_id="vid00000002", channel_folder="Other", channel_id="UC" + "y" * 22)
        found = {p.name for p in archive.video_dirs(archive_root)}
        assert found == {"vid00000001", "vid00000002"}

    def test_a_channel_without_a_manifest_is_passed_over(self, archive_root, make_video):
        """
        Channels are found by their manifest, so anything else left in the
        archive root is not walked as though it were one.
        """
        make_video()
        stray = archive_root / "not-a-channel" / "videos" / "whatever"
        stray.mkdir(parents=True)
        assert [p.name for p in archive.video_dirs(archive_root)] == ["vid00000001"]

    def test_it_can_be_narrowed_to_named_videos(self, archive_root, make_video):
        make_video(video_id="vid00000001")
        make_video(video_id="vid00000002")
        found = list(archive.video_dirs(archive_root, {"vid00000002"}))
        assert [p.name for p in found] == ["vid00000002"]

    def test_locate_finds_one_across_channels(self, archive_root, make_video):
        make_video(video_id="vid00000001")
        other = make_video(
            video_id="vid00000002", channel_folder="Other", channel_id="UC" + "y" * 22
        )
        assert archive.locate(archive_root, "vid00000002") == other
        assert archive.locate(archive_root, "nosuchvideo") is None

    def test_an_empty_directory_is_not_an_archive(self, archive_root):
        assert archive.looks_like_an_archive(archive_root) is False

    def test_a_directory_with_a_channel_is(self, archive_root, make_video):
        make_video()
        assert archive.looks_like_an_archive(archive_root) is True


class TestPickingTheMedia:
    def test_it_finds_the_archived_video(self, make_video):
        vdir = make_video()
        assert [p.name for p in archive.media_files(vdir, "video")] == ["video.webm"]

    def test_a_per_format_intermediate_is_ignored(self, make_video):
        """
        yt-dlp leaves `video.f251.webm` beside the merged file, and f251 is
        audio only. Encoding a companion from that would produce sound over a
        blank frame.
        """
        vdir = make_video()
        (vdir / "video.f251.webm").write_bytes(b"\x00" * 64)
        assert [p.name for p in archive.media_files(vdir, "video")] == ["video.webm"]

    def test_a_thumbnail_sharing_the_stem_is_ignored(self, make_video):
        vdir = make_video()
        (vdir / "video.webp").write_bytes(b"\x00" * 64)
        assert [p.name for p in archive.media_files(vdir, "video")] == ["video.webm"]

    def test_a_companion_is_not_the_archived_video(self, make_video):
        """
        The whole reason for the prefix: the archiver serves whatever matches
        the bare `video.*` stem, so a companion must fall outside it.
        """
        vdir = make_video()
        (vdir / "video-1080p.webm").write_bytes(b"\x00" * 64)
        assert [p.name for p in archive.media_files(vdir, "video")] == ["video.webm"]


class TestCompanions:
    def test_they_are_listed_smallest_first(self, make_video):
        vdir = make_video()
        (vdir / "video-1080p.webm").write_bytes(b"\x00" * 64)
        (vdir / "video-720p.webm").write_bytes(b"\x00" * 32)
        assert [r["height"] for r in archive.renditions(vdir)] == [720, 1080]

    def test_a_video_with_none_has_none(self, make_video):
        assert archive.renditions(make_video()) == []

    def test_something_else_sharing_the_prefix_is_ignored(self, make_video):
        vdir = make_video()
        (vdir / "video-notes.txt").write_text("x", encoding="utf-8")
        (vdir / "video-1080p.webm").write_bytes(b"\x00" * 64)
        assert [r["file"] for r in archive.renditions(vdir)] == ["video-1080p.webm"]

    def test_the_name_is_built_from_the_height(self):
        assert archive.rendition_name(1080, ".webm") == "video-1080p.webm"
        assert archive.rendition_name(720, ".mp4") == "video-720p.mp4"


class TestVideoId:
    def test_it_comes_from_the_metadata(self, make_video):
        vdir = make_video(video_id="vid00000001")
        assert archive.video_id_of(vdir) == "vid00000001"

    def test_the_directory_name_is_the_fallback(self, archive_root):
        vdir = archive_root / "Ch" / "videos" / "bareid00001"
        vdir.mkdir(parents=True)
        assert archive.video_id_of(vdir) == "bareid00001"

    def test_unreadable_metadata_falls_back_too(self, make_video):
        vdir = make_video()
        (vdir / "info.json").write_text("{not json", encoding="utf-8")
        assert archive.video_id_of(vdir) == "vid00000001"
