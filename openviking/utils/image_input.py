# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0
"""Smaller model-input renditions; original resources and their identities stay intact."""

import io

# An optimization threshold, not a limit on which resources can be read.
_COMPRESS_ABOVE_BYTES = 512 * 1024
_JPEG_QUALITY = 85


def compress_image_input(data: bytes, mime_type: str) -> tuple[bytes, str]:
    """Reduce encoded bytes without reducing the source's pixel resolution.

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
        # PNG can carry a transparent color without an alpha channel. Expand it
        # before compositing so transparent pixels get a readable background.
        image = original.convert("RGBA") if "transparency" in original.info else original
        # Preview-size downsampling can erase small text in long document images.
        # Correct display orientation while retaining the full source resolution.
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
