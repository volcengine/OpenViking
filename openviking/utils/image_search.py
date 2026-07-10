# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0
"""Small helpers for image search inputs."""

from __future__ import annotations

import base64
import mimetypes
import os
from pathlib import Path
from typing import Any, Dict, List, Optional


def image_mime_type(file_name: str = "") -> str:
    mime_type, _ = mimetypes.guess_type(file_name or "")
    if mime_type and mime_type.startswith("image/"):
        return mime_type
    return "image/png"


def image_bytes_to_data_uri(data: bytes | bytearray | memoryview, file_name: str = "") -> str:
    encoded = base64.b64encode(bytes(data)).decode("ascii")
    return f"data:{image_mime_type(file_name)};base64,{encoded}"


def is_data_image_uri(value: str) -> bool:
    return value.startswith("data:image/") and ";base64," in value


def is_http_url(value: str) -> bool:
    return value.startswith(("http://", "https://"))


def is_viking_uri(value: str) -> bool:
    return value.startswith("viking://")


def build_multimodal_embedding_input(
    text: str = "",
    image_url: Optional[str] = None,
) -> List[Dict[str, Any]]:
    parts: List[Dict[str, Any]] = []
    if text.strip():
        parts.append({"type": "text", "text": text})
    if image_url:
        parts.append({"type": "image_url", "image_url": {"url": image_url}})
    return parts


def normalize_client_image_input(image: Any) -> Optional[str]:
    if image is None:
        return None
    if isinstance(image, (bytes, bytearray, memoryview)):
        return image_bytes_to_data_uri(image)

    value = os.fspath(image) if isinstance(image, os.PathLike) else str(image)
    if is_data_image_uri(value) or is_http_url(value) or is_viking_uri(value):
        return value

    path = Path(value).expanduser()
    if path.is_file():
        return image_bytes_to_data_uri(path.read_bytes(), path.name)
    return value
