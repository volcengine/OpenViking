"""Tests for .ts file media type detection — regression for #3266."""

from openviking.parse.parsers.media.utils import get_media_type
from openviking.parse.parsers.media.constants import VIDEO_EXTENSIONS, MEDIA_EXTENSIONS


class TestTsFileMediaType:
    """TypeScript .ts files must not be detected as video (issue #3266)."""

    def test_ts_is_not_video(self):
        """.ts files should not be detected as video."""
        assert get_media_type("app.ts", None) is None

    def test_tsx_is_not_video(self):
        """.tsx files should not be detected as video."""
        assert get_media_type("component.tsx", None) is None

    def test_ts_not_in_video_extensions(self):
        """.ts should not be in VIDEO_EXTENSIONS."""
        assert ".ts" not in VIDEO_EXTENSIONS

    def test_ts_not_in_media_extensions(self):
        """.ts should not be in MEDIA_EXTENSIONS."""
        assert ".ts" not in MEDIA_EXTENSIONS

    def test_mp4_still_video(self):
        """Genuine video extensions should still be detected as video."""
        assert get_media_type("movie.mp4", None) == "video"

    def test_ts_with_explicit_video_format(self):
        """source_format='video' should override code extension check."""
        assert get_media_type("transport.ts", "video") == "video"

    def test_other_code_extensions_not_media(self):
        """Other code extensions should not be detected as media."""
        for ext in [".py", ".js", ".go", ".rs", ".java"]:
            assert get_media_type(f"file{ext}", None) is None

    def test_image_still_image(self):
        """Image extensions should still be detected as image."""
        assert get_media_type("photo.png", None) == "image"

    def test_audio_still_audio(self):
        """Audio extensions should still be detected as audio."""
        assert get_media_type("song.mp3", None) == "audio"
