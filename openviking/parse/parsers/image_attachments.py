# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0
"""Helpers for parser-produced image attachments."""

from __future__ import annotations

from pathlib import Path
from typing import Any, Dict, Optional

from openviking.parse.base import RESOURCE_ROOT_PLACEHOLDER

SUPPORTED_IMAGE_EXTENSIONS = frozenset({".png", ".jpg", ".jpeg", ".gif", ".bmp", ".webp", ".svg"})

_CONTENT_TYPE_EXTENSIONS = {
    "image/png": ".png",
    "image/jpeg": ".jpg",
    "image/jpg": ".jpg",
    "image/gif": ".gif",
    "image/bmp": ".bmp",
    "image/webp": ".webp",
    "image/svg+xml": ".svg",
}


def image_media_path(index: int, extension: str) -> str:
    """Return the canonical parser-produced media path."""
    return f"media/images/image-{index}{_normalize_extension(extension)}"


def markdown_image_reference(media_path: str, alt_text: str = "image") -> str:
    """Return a Markdown image reference that will be rewritten to the resource URI."""
    return f"![{alt_text}]({RESOURCE_ROOT_PLACEHOLDER}/{media_path})"


def image_attachment(media_path: str, content: bytes) -> Dict[str, Any]:
    """Return the parser-produced attachment payload used by MarkdownParser."""
    return {"path": media_path, "content": content}


def image_extension_from_name_type_or_data(
    name: str = "",
    content_type: str = "",
    data: Optional[bytes] = None,
) -> str:
    """Choose a stable supported image extension from metadata or bytes."""
    suffix = Path(name).suffix.lower()
    if suffix in SUPPORTED_IMAGE_EXTENSIONS:
        return ".jpg" if suffix == ".jpeg" else suffix

    ext = _CONTENT_TYPE_EXTENSIONS.get(content_type.lower())
    if ext:
        return ext

    if data:
        return detect_image_extension(data)

    return ".png"


def detect_image_extension(data: bytes) -> str:
    """Detect a supported image extension from file signatures."""
    if data.startswith(b"\x89PNG\r\n\x1a\n"):
        return ".png"
    if data.startswith(b"\xff\xd8\xff"):
        return ".jpg"
    if data.startswith((b"GIF87a", b"GIF89a")):
        return ".gif"
    if data.startswith(b"BM"):
        return ".bmp"
    if data.startswith(b"RIFF") and data[8:12] == b"WEBP":
        return ".webp"
    if data.lstrip().startswith((b"<svg", b"<?xml")):
        return ".svg"
    return ".png"


def _normalize_extension(extension: str) -> str:
    ext = extension.lower()
    if not ext.startswith("."):
        ext = f".{ext}"
    return ".jpg" if ext == ".jpeg" else ext
