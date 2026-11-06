# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0
"""Smaller model-input renditions; original resources and their identities stay intact."""

import io

from openviking.utils.media_limits import DEFAULT_IMAGE_PREVIEW_MAX_DIMENSION

# An optimization threshold, not a limit on which resources can be read.
_COMPRESS_ABOVE_BYTES = 512 * 1024
_JPEG_QUALITY = 85


def compress_image_input(data: bytes, mime_type: str) -> tuple[bytes, str]:
    """Reduce large static images using the existing preview size and JPEG quality.

    Keep small images, animations and any rendition larger than its original unchanged.
    This is independent of provider limits: the endpoint validates its actual request.
    """
    if len(data) <= _COMPRESS_ABOVE_BYTES:
        return data, mime_type

    from PIL import Image, ImageOps

    with Image.open(io.BytesIO(data)) as original:
        if getattr(original, "is_animated", False):
            return data, mime_type
        # RGB conversion clips high-bit-depth intensities instead of scaling them.
        if original.mode in {"I", "F"} or original.mode.startswith("I;16"):
            return data, mime_type
        if original.format == "JPEG":
            scale = min(1.0, DEFAULT_IMAGE_PREVIEW_MAX_DIMENSION / max(original.size))
            original.draft(None, tuple(max(1, int(side * scale)) for side in original.size))
        # PNG can carry a transparent color without an alpha channel. Expand it
        # before resampling so edge pixels retain their transparency, too.
        image = original.convert("RGBA") if "transparency" in original.info else original
        image.thumbnail(
            (DEFAULT_IMAGE_PREVIEW_MAX_DIMENSION, DEFAULT_IMAGE_PREVIEW_MAX_DIMENSION),
            Image.Resampling.LANCZOS,
        )
        # Transpose after shrinking to avoid copying the full decoded image.
        image = ImageOps.exif_transpose(image)
        if image.mode in {"RGBA", "LA", "P"}:
            rgba = image.convert("RGBA")
            background = Image.new("RGBA", rgba.size, "white")
            image = Image.alpha_composite(background, rgba).convert("RGB")
        else:
            image = image.convert("RGB")
        output = io.BytesIO()
        image.save(output, format="JPEG", quality=_JPEG_QUALITY, optimize=True)
    compressed = output.getvalue()
    return (compressed, "image/jpeg") if len(compressed) < len(data) else (data, mime_type)
