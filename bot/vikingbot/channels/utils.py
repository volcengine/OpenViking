"""Shared utilities for channel implementations - image path handling, etc."""

import base64
import re
from pathlib import Path
from loguru import logger
from typing import Tuple, List


# Common image file extensions
IMAGE_EXTENSIONS = {".png", ".jpg", ".jpeg", ".gif", ".webp", ".bmp", ".svg", ".tiff"}


def is_image_file_path(path_str: str) -> bool:
    """
    Check if a string looks like a local image file path.

    Args:
        path_str: The string to check

    Returns:
        True if it looks like an image file path
    """
    if not path_str:
        return False

    # Check if it starts with markdown image syntax - skip these
    if path_str.startswith("!["):
        return False

    # Check if it's a data URI or URL - those are handled separately
    if (
        path_str.startswith("data:")
        or path_str.startswith("http://")
        or path_str.startswith("https://")
    ):
        return False

    try:
        path = Path(path_str)
        # Check if it has an image extension
        return path.suffix.lower() in IMAGE_EXTENSIONS
    except Exception:
        return False


def extract_image_paths(content: str) -> Tuple[List[str], str]:
    """
    Extract potential image file paths from content.
    Args:
        content: The text content to process
        Tuple of (list_of_image_paths, original_content)
    """
    paths = []
    # First, extract all markdown image syntax: ![alt](path)
    markdown_image_matches = re.findall(r"!\[.*?\]\((.*?)\)", content)
    for match in markdown_image_matches:
        if is_image_file_path(match):
            paths.append(match)

    # Next, extract all backtick-wrapped content
    backtick_matches = re.findall(r"`([^`]+)`", content)
    for match in backtick_matches:
        if is_image_file_path(match):
            paths.append(match)

    # Also check all tokens (split by whitespace)
    for token in content.split():
        # Clean up token (remove punctuation at end)
        clean_token = token.rstrip(".,!?;:)]}'\"")
        if clean_token and is_image_file_path(clean_token) and clean_token not in paths:
            paths.append(clean_token)

    # Remove duplicates while preserving order
    seen = set()
    unique_paths = []
    for p in paths:
        if p not in seen:
            seen.add(p)
            unique_paths.append(p)

    return unique_paths, content


def read_image_file(path_str: str) -> bytes:
    """
    Read an image file from disk.

    Args:
        path_str: Path to the image file

    Returns:
        Image bytes

    Raises:
        FileNotFoundError: If file doesn't exist
        IOError: If reading fails
    """
    path = Path(path_str)
    if not path.exists():
        raise FileNotFoundError(f"Image file not found: {path_str}")
    if not path.is_file():
        raise IOError(f"Path is not a file: {path_str}")

    return path.read_bytes()


def image_to_data_uri(image_bytes: bytes, mime_type: str = "image/png") -> str:
    """
    Convert image bytes to a data URI.

    Args:
        image_bytes: Image data
        mime_type: MIME type of the image

    Returns:
        Data URI string
    """
    b64 = base64.b64encode(image_bytes).decode("utf-8")
    return f"data:{mime_type};base64,{b64}"
