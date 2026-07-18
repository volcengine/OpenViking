from openviking.parse.parsers.media.utils import get_media_type, get_resource_media_type


def test_explicit_text_parser_format_overrides_ambiguous_ts_extension():
    assert get_media_type("example.ts", "markdown") is None


def test_explicit_video_format_preserves_mpeg_ts():
    assert get_media_type("example.ts", "video") == "video"


def test_typescript_in_repository_uses_text_summary_path():
    assert get_resource_media_type("viking://resources/org/repo/src/example.ts") is None


def test_mpeg_ts_in_video_namespace_uses_video_summary_path():
    assert get_resource_media_type("viking://resources/video/2026/07/19/example.ts") == "video"
