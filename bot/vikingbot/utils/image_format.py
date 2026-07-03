"""Image format detection helpers."""

from typing import NamedTuple


class ImageFormat(NamedTuple):
    extension: str
    mime_type: str


PNG_FORMAT = ImageFormat("png", "image/png")

_MIME_FORMATS = {
    "image/png": PNG_FORMAT,
    "image/jpeg": ImageFormat("jpg", "image/jpeg"),
    "image/jpg": ImageFormat("jpg", "image/jpeg"),
    "image/gif": ImageFormat("gif", "image/gif"),
    "image/webp": ImageFormat("webp", "image/webp"),
    "image/bmp": ImageFormat("bmp", "image/bmp"),
}


def image_format_from_mime(mime_type: str | None) -> ImageFormat | None:
    """Return a supported image format for a MIME type."""
    if not mime_type:
        return None
    return _MIME_FORMATS.get(mime_type.lower().split(";", 1)[0].strip())


def detect_image_format(image_data: bytes, fallback_mime: str | None = None) -> ImageFormat:
    """Detect common image formats from magic bytes, falling back to a MIME hint."""
    if image_data.startswith(b"\x89PNG\r\n\x1a\n"):
        return PNG_FORMAT
    if image_data.startswith(b"\xff\xd8\xff"):
        return ImageFormat("jpg", "image/jpeg")
    if image_data.startswith((b"GIF87a", b"GIF89a")):
        return ImageFormat("gif", "image/gif")
    if image_data.startswith(b"BM"):
        return ImageFormat("bmp", "image/bmp")
    if len(image_data) >= 12 and image_data[:4] == b"RIFF" and image_data[8:12] == b"WEBP":
        return ImageFormat("webp", "image/webp")

    return image_format_from_mime(fallback_mime) or PNG_FORMAT
