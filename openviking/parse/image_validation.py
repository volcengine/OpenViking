# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0
"""Shared acceptance policy for images embedded in parsed documents."""

import io
from pathlib import Path

from openviking_cli.utils.logger import get_logger

logger = get_logger(__name__)

IMAGE_MIN_SIDE = 14
IMAGE_MIN_PIXELS = 196
IMAGE_MAX_PIXELS = 36_000_000
IMAGE_MIN_ASPECT_RATIO = 1 / 150
IMAGE_MAX_ASPECT_RATIO = 150
IMAGE_MAX_FILE_BYTES = 10 * 1024 * 1024


def is_valid_image(image_bytes: bytes, source_path: Path) -> bool:
    """Return whether an extracted image is safe and useful to ingest."""
    if len(image_bytes) > IMAGE_MAX_FILE_BYTES:
        logger.warning(f"[ImageValidation] Image exceeds 10MB, skipping: {source_path}")
        return False

    try:
        from PIL import Image

        with Image.open(io.BytesIO(image_bytes)) as image:
            width, height = image.size
    except Exception as exc:
        logger.warning(
            f"[ImageValidation] Cannot read image dimensions, skipping {source_path}: {exc}"
        )
        return False

    if width <= IMAGE_MIN_SIDE or height <= IMAGE_MIN_SIDE:
        logger.warning(
            f"[ImageValidation] Image side too small ({width}x{height}), skipping: {source_path}"
        )
        return False

    pixels = width * height
    if pixels < IMAGE_MIN_PIXELS or pixels > IMAGE_MAX_PIXELS:
        logger.warning(
            f"[ImageValidation] Image pixel count out of range ({pixels}), skipping: {source_path}"
        )
        return False

    aspect_ratio = width / height
    if aspect_ratio < IMAGE_MIN_ASPECT_RATIO or aspect_ratio > IMAGE_MAX_ASPECT_RATIO:
        logger.warning(
            f"[ImageValidation] Image aspect ratio out of range ({aspect_ratio:.4f}), "
            f"skipping: {source_path}"
        )
        return False

    return True
