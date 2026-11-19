# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0
"""Shared acceptance policy for images embedded in parsed documents."""

import io
from pathlib import Path

from openviking.observability.log_safety import safe_path_for_log
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
        logger.debug(
            "Image exceeds size limit; skipping path=%s", safe_path_for_log(str(source_path))
        )
        return False

    try:
        from PIL import Image

        with Image.open(io.BytesIO(image_bytes)) as image:
            width, height = image.size
    except Exception as exc:
        logger.debug(
            "Image dimensions unreadable; skipping path=%s error_type=%s",
            safe_path_for_log(str(source_path)),
            type(exc).__name__,
        )
        return False

    if width <= IMAGE_MIN_SIDE or height <= IMAGE_MIN_SIDE:
        logger.debug(
            "Image side below minimum; skipping path=%s width=%d height=%d",
            safe_path_for_log(str(source_path)),
            width,
            height,
        )
        return False

    pixels = width * height
    if pixels < IMAGE_MIN_PIXELS or pixels > IMAGE_MAX_PIXELS:
        logger.debug(
            "Image pixel count out of range; skipping path=%s pixels=%d",
            safe_path_for_log(str(source_path)),
            pixels,
        )
        return False

    aspect_ratio = width / height
    if aspect_ratio < IMAGE_MIN_ASPECT_RATIO or aspect_ratio > IMAGE_MAX_ASPECT_RATIO:
        logger.debug(
            "Image aspect ratio out of range; skipping path=%s ratio=%.4f",
            safe_path_for_log(str(source_path)),
            aspect_ratio,
        )
        return False

    return True
