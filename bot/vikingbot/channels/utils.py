"""Extract and read outbound image references."""

import re
import stat
from pathlib import Path

from vikingbot.utils import get_images_path

_IMAGE_EXTENSIONS = {".png", ".jpg", ".jpeg", ".gif", ".webp", ".bmp", ".svg", ".tiff"}
_IMAGE_FILENAME_RE = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]{0,200}")


def extract_image_filenames(content: str) -> list[str]:
    """Find distinct image filenames in Markdown images and backticks."""
    references = re.findall(r"!\[.*?\]\((.*?)\)", content)
    references.extend(re.findall(r"`([^`]+)`", content))
    return [
        filename
        for filename in dict.fromkeys(references)
        if _IMAGE_FILENAME_RE.fullmatch(filename)
        and Path(filename).suffix.lower() in _IMAGE_EXTENSIONS
    ]


def read_image_file(filename: str) -> bytes:
    """Read a regular file by bare filename from the bot's image directory."""
    if not _IMAGE_FILENAME_RE.fullmatch(filename):
        raise ValueError(f"Image reference must be a bare filename, got {filename!r}")

    images_dir = get_images_path().resolve()
    path = (images_dir / filename).resolve()
    if not path.is_relative_to(images_dir):
        raise ValueError(f"Image reference resolves outside the image directory: {filename!r}")
    if not stat.S_ISREG(path.stat().st_mode):
        raise OSError(f"Image reference is not a regular file: {filename!r}")

    return path.read_bytes()
