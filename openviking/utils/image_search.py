# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0
"""Small helpers for image search inputs."""

from __future__ import annotations

import base64
import io
import mimetypes
import os
from pathlib import Path
from typing import Any, Dict, List, Optional

from PIL import Image, UnidentifiedImageError

from openviking.utils.media_limits import is_large_image_by_size
from openviking_cli.utils.config.parser_config import ImageConfig


def image_mime_type(file_name: str = "") -> str:
    mime_type, _ = mimetypes.guess_type(file_name or "")
    if mime_type and mime_type.startswith("image/"):
        return mime_type
    return "image/png"


def image_bytes_to_data_uri(data: bytes | bytearray | memoryview, file_name: str = "") -> str:
    encoded = base64.b64encode(bytes(data)).decode("ascii")
    return f"data:{image_mime_type(file_name)};base64,{encoded}"


def _prepare_image_bytes_for_model(
    data: bytes | bytearray | memoryview,
    config: ImageConfig | None = None,
) -> tuple[bytes, bool]:
    """Return model-ready image bytes and whether they differ from the input."""
    content = bytes(data)
    try:
        with Image.open(io.BytesIO(content)) as img:
            width, height = img.size
            image_config = config or ImageConfig()
            if not is_large_image_by_size(
                file_size_bytes=len(content),
                width=width,
                height=height,
                config=image_config,
            ):
                return content, False

            preview = img.convert("RGB")

            max_dimension = image_config.preview_max_dimension
            if width > max_dimension or height > max_dimension:
                ratio = min(max_dimension / width, max_dimension / height)
                new_size = (max(1, int(width * ratio)), max(1, int(height * ratio)))
                preview = preview.resize(new_size, Image.Resampling.LANCZOS)

            buf = io.BytesIO()
            preview.save(buf, format="JPEG", quality=85, optimize=True)
            return buf.getvalue(), True
    except (UnidentifiedImageError, OSError, ValueError):
        return content, False


def prepare_image_bytes_for_model(
    data: bytes | bytearray | memoryview,
    config: ImageConfig | None = None,
) -> bytes:
    """Downsample oversized image bytes for model requests without changing storage."""
    model_bytes, _ = _prepare_image_bytes_for_model(data, config=config)
    return model_bytes


def image_bytes_to_model_data_uri(
    data: bytes | bytearray | memoryview,
    file_name: str = "",
    config: ImageConfig | None = None,
) -> str:
    model_bytes, changed = _prepare_image_bytes_for_model(data, config=config)
    return image_bytes_to_data_uri(model_bytes, "model_input.jpg" if changed else file_name)


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
