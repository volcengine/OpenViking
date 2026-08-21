# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0
"""Shared media limits and limit checks."""

from typing import Protocol

MAX_MEDIA_FILE_BYTES = 512 * 1024 * 1024

DEFAULT_LARGE_IMAGE_MAX_FILE_SIZE_MB = 10.0
DEFAULT_LARGE_IMAGE_THRESHOLD_DIMENSION = 4096
DEFAULT_IMAGE_PREVIEW_MAX_DIMENSION = 2048
DEFAULT_IMAGE_MAX_TILE_DIMENSION_PX = 2048
DEFAULT_IMAGE_TILE_OVERLAP_PX = 2


class ImageLimitConfig(Protocol):
    max_file_size_mb: float
    large_image_threshold_dimension: int


def is_large_image_by_size(
    *,
    file_size_bytes: int,
    width: int,
    height: int,
    config: ImageLimitConfig | None = None,
) -> bool:
    """Return whether image bytes exceed the configured large-image limits."""
    max_file_size_mb = config.max_file_size_mb if config else DEFAULT_LARGE_IMAGE_MAX_FILE_SIZE_MB
    max_dimension_px = (
        config.large_image_threshold_dimension
        if config
        else DEFAULT_LARGE_IMAGE_THRESHOLD_DIMENSION
    )

    file_size_mb = file_size_bytes / (1024 * 1024)
    return file_size_mb > max_file_size_mb or width > max_dimension_px or height > max_dimension_px
