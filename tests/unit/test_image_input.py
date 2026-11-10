# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0
"""Model renditions preserve source bytes, animation and readable backgrounds."""

import io
import random

import pytest
from PIL import Image

from openviking.utils.image_input import compress_image_input


def encoded(image, **options):
    output = io.BytesIO()
    image.save(output, **options)
    return output.getvalue()


@pytest.mark.parametrize("mode", ["RGBA", "RGB"])
def test_large_transparent_image_uses_white_background_and_bounds_dimensions(mode):
    pixels = random.Random(7).randbytes(2400 * 1200 * len(mode))
    image = Image.frombytes(mode, (2400, 1200), pixels)
    if mode == "RGBA":
        image.putalpha(0)
        original = encoded(image, format="PNG")
    else:
        image.paste((0, 0, 0), (0, 0, 1200, 1200))
        original = encoded(image, format="PNG", transparency=(0, 0, 0))
    assert len(original) > 512 * 1024
    compressed, mime = compress_image_input(original, "image/png")
    assert mime == "image/jpeg"
    assert len(compressed) < len(original)
    with Image.open(io.BytesIO(compressed)) as rendition:
        assert rendition.size == (2048, 1024)
        assert rendition.getpixel((100, 100)) == (255, 255, 255)
    with Image.open(io.BytesIO(original)) as source:
        assert source.size == (2400, 1200)
        assert source.convert("RGBA").getpixel((100, 100))[3] == 0


def test_large_jpeg_keeps_exif_orientation_after_thumbnail_decode():
    pixels = random.Random(9).randbytes(256 * 192 * 3)
    image = Image.frombytes("RGB", (256, 192), pixels).resize(
        (4096, 3072), Image.Resampling.BILINEAR
    )
    image.paste((255, 0, 0), (0, 0, 500, 500))
    exif = Image.Exif()
    exif[274] = 6  # Rotate 90 degrees clockwise for display.
    original = encoded(image, format="JPEG", quality=90, exif=exif)
    assert len(original) > 512 * 1024
    compressed, mime = compress_image_input(original, "image/jpeg")
    assert mime == "image/jpeg" and len(compressed) < len(original)
    with Image.open(io.BytesIO(compressed)) as rendition:
        assert rendition.size == (1536, 2048)
        red, green, blue = rendition.getpixel((1436, 100))
        assert red > 240 and green < 15 and blue < 15
        assert rendition.getexif().get(274, 1) == 1


def test_animated_image_is_never_flattened(monkeypatch):
    monkeypatch.setattr("openviking.utils.image_input._COMPRESS_ABOVE_BYTES", 0)
    image = Image.new("RGB", (100, 100), "white")
    frame = Image.new("RGB", (100, 100), "black")
    original = encoded(image, format="GIF", save_all=True, append_images=[frame], duration=100)
    assert compress_image_input(original, "image/gif") == (original, "image/gif")


def test_large_high_bit_depth_image_preserves_original_intensities():
    image = Image.frombytes("I;16", (800, 400), random.Random(16).randbytes(800 * 400 * 2))
    original = encoded(image, format="PNG")
    assert len(original) > 512 * 1024
    assert compress_image_input(original, "image/png") == (original, "image/png")


def test_small_original_is_kept_when_jpeg_would_be_larger(monkeypatch):
    monkeypatch.setattr("openviking.utils.image_input._COMPRESS_ABOVE_BYTES", 0)
    original = encoded(Image.new("RGB", (100, 100), "white"), format="PNG")
    assert compress_image_input(original, "image/png") == (original, "image/png")
