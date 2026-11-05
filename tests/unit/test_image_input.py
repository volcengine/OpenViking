# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0
"""Model renditions preserve source bytes, animation and readable backgrounds."""

import io
import random

from PIL import Image

from openviking.utils.image_input import compress_image_input


def encoded(image, **options):
    output = io.BytesIO()
    image.save(output, **options)
    return output.getvalue()


def test_large_transparent_image_uses_white_background_and_bounds_dimensions():
    pixels = random.Random(7).randbytes(2400 * 1200 * 4)
    image = Image.frombytes("RGBA", (2400, 1200), pixels)
    image.putalpha(0)
    original = encoded(image, format="PNG")
    assert len(original) > 512 * 1024
    compressed, mime = compress_image_input(original, "image/png")
    assert mime == "image/jpeg"
    assert len(compressed) < len(original)
    with Image.open(io.BytesIO(compressed)) as rendition:
        assert rendition.size == (2048, 1024)
        assert rendition.getpixel((100, 100)) == (255, 255, 255)
    with Image.open(io.BytesIO(original)) as source:
        assert source.size == (2400, 1200)
        assert source.getpixel((100, 100))[3] == 0


def test_animated_image_is_never_flattened(monkeypatch):
    monkeypatch.setattr("openviking.utils.image_input._COMPRESS_ABOVE_BYTES", 0)
    image = Image.new("RGB", (100, 100), "white")
    frame = Image.new("RGB", (100, 100), "black")
    original = encoded(image, format="GIF", save_all=True, append_images=[frame], duration=100)
    assert compress_image_input(original, "image/gif") == (original, "image/gif")


def test_small_original_is_kept_when_jpeg_would_be_larger(monkeypatch):
    monkeypatch.setattr("openviking.utils.image_input._COMPRESS_ABOVE_BYTES", 0)
    original = encoded(Image.new("RGB", (100, 100), "white"), format="PNG")
    assert compress_image_input(original, "image/png") == (original, "image/png")
