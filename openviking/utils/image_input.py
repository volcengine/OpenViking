# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0
"""Smaller model-input renditions; original resources and their identities stay intact."""

from __future__ import annotations

import io
from threading import BoundedSemaphore
from typing import TYPE_CHECKING

from openviking.utils.media_limits import DEFAULT_IMAGE_PREVIEW_MAX_DIMENSION

if TYPE_CHECKING:
    from PIL.Image import Image as PillowImage

# An optimization threshold, not a limit on which resources can be read.
_COMPRESS_ABOVE_BYTES = 512 * 1024
_JPEG_QUALITY = 85
MAX_IMAGE_INPUT_DOWNLOAD_BYTES = 10 * 1024 * 1024
MAX_COMPILE_IMAGE_DOWNLOAD_BYTES = 20 * 1024 * 1024
MAX_IMAGE_INPUT_DECODE_PIXELS = 24_000_000
# Full-resolution conversion may hold several raster copies. Serialize it across
# callers, including callers on different asyncio loops, without blocking a loop.
_COMPRESSION_SLOTS = BoundedSemaphore(1)


def encode_image_jpeg(image: PillowImage, max_dimension: int | None = None) -> bytes:
    """Encode an RGB image as JPEG, optionally fitting it within a square."""
    from PIL import Image

    width, height = image.size
    long_edge = max(width, height)
    if max_dimension is not None and long_edge > max_dimension:
        size = (
            max(1, width * max_dimension // long_edge),
            max(1, height * max_dimension // long_edge),
        )
        image = image.resize(size, Image.Resampling.LANCZOS)
    with io.BytesIO() as output:
        image.save(output, format="JPEG", quality=_JPEG_QUALITY, optimize=True)
        return output.getvalue()


def compress_image_input(
    data: bytes, mime_type: str, *, compile_inline_budget: int | None = None
) -> tuple[bytes, str]:
    """Prepare a rendition, preserving full resolution unless Compile needs a preview.

    Compile checks dimensions even when the input already fits its byte budget.
    """
    if compile_inline_budget is None and len(data) <= _COMPRESS_ABOVE_BYTES:
        return data, mime_type
    download_limit = (
        MAX_IMAGE_INPUT_DOWNLOAD_BYTES
        if compile_inline_budget is None
        else MAX_COMPILE_IMAGE_DOWNLOAD_BYTES
    )
    if len(data) > download_limit:
        raise ValueError(f"Image exceeds the {download_limit} byte compression input budget")

    from PIL import Image, ImageOps

    with _COMPRESSION_SLOTS, Image.open(io.BytesIO(data)) as original:
        max_dimension = DEFAULT_IMAGE_PREVIEW_MAX_DIMENSION
        resize = compile_inline_budget is not None and max(original.size) > max_dimension
        if compile_inline_budget is not None and len(data) <= compile_inline_budget and not resize:
            return data, mime_type
        if original.width * original.height > MAX_IMAGE_INPUT_DECODE_PIXELS:
            raise ValueError("Image exceeds the 24 million pixel decode budget")
        # RGB conversion clips high-bit-depth intensities instead of scaling them.
        high_bit_depth = original.mode in {"I", "F"} or original.mode.startswith("I;16")
        if getattr(original, "is_animated", False) or high_bit_depth:
            if resize:
                raise ValueError(
                    f"Animated or high-bit-depth image exceeds the {max_dimension} pixel long-edge limit"
                )
            return data, mime_type
        # PNG can carry a transparent color without an alpha channel. Expand it
        # before compositing so transparent pixels get a readable background.
        image = original.convert("RGBA") if "transparency" in original.info else original
        # Correct display orientation before preparing the model input.
        image = ImageOps.exif_transpose(image)
        if image.mode in {"RGBA", "LA", "P"}:
            rgba = image.convert("RGBA")
            background = Image.new("RGBA", rgba.size, "white")
            image = Image.alpha_composite(background, rgba).convert("RGB")
        else:
            image = image.convert("RGB")
        compressed = encode_image_jpeg(image, max_dimension if resize else None)
        if compile_inline_budget is not None and len(compressed) > compile_inline_budget:
            # Bound the extra encode while retaining the largest rendition on the first attempt.
            preview_dimension = max(1, min(max(image.size), max_dimension) // 2)
            compressed = encode_image_jpeg(image, preview_dimension)
    return (
        (compressed, "image/jpeg") if resize or len(compressed) < len(data) else (data, mime_type)
    )
