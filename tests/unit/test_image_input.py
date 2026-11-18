# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0
"""Model renditions preserve source bytes, animation and readable backgrounds."""

import io
import random
import threading
import time
from concurrent.futures import ThreadPoolExecutor

import pytest
from PIL import Image

from openviking.utils.image_input import compress_image_input


def encoded(image, **options):
    output = io.BytesIO()
    image.save(output, **options)
    return output.getvalue()


@pytest.mark.parametrize("mode", ["RGBA", "RGB"])
def test_large_transparent_image_uses_white_background_and_preserves_dimensions(mode):
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
        assert rendition.size == (2400, 1200)
        assert rendition.getpixel((100, 100)) == (255, 255, 255)
    with Image.open(io.BytesIO(original)) as source:
        assert source.size == (2400, 1200)
        assert source.convert("RGBA").getpixel((100, 100))[3] == 0


def test_large_jpeg_keeps_exif_orientation_and_full_resolution():
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
        assert rendition.size == (3072, 4096)
        red, green, blue = rendition.getpixel((2972, 100))
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


def test_compression_pixel_budget_is_checked_before_raster_decode(monkeypatch):
    monkeypatch.setattr("openviking.utils.image_input._COMPRESS_ABOVE_BYTES", 0)
    monkeypatch.setattr("openviking.utils.image_input.MAX_IMAGE_INPUT_DECODE_PIXELS", 100)
    original = encoded(Image.new("RGB", (20, 20)), format="PNG")

    def unexpected_load(*args, **kwargs):
        raise AssertionError("Pixel budget must be checked before decoding")

    monkeypatch.setattr("PIL.PngImagePlugin.PngImageFile.load", unexpected_load)
    with pytest.raises(ValueError, match="pixel decode budget"):
        compress_image_input(original, "image/png")


def test_full_resolution_conversions_share_one_process_slot(monkeypatch):
    from PIL import ImageOps

    monkeypatch.setattr("openviking.utils.image_input._COMPRESS_ABOVE_BYTES", 0)
    original = encoded(Image.new("RGB", (20, 20)), format="PNG")
    transpose = ImageOps.exif_transpose
    active = peak = 0
    lock = threading.Lock()

    def observed_transpose(image):
        nonlocal active, peak
        with lock:
            active += 1
            peak = max(peak, active)
        try:
            time.sleep(0.01)
            return transpose(image)
        finally:
            with lock:
                active -= 1

    monkeypatch.setattr(ImageOps, "exif_transpose", observed_transpose)
    with ThreadPoolExecutor(max_workers=2) as pool:
        list(pool.map(lambda _: compress_image_input(original, "image/png"), range(2)))
    assert peak == 1
