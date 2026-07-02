# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0
"""Regression tests for image format detection."""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from vikingbot.utils.image_format import detect_image_format  # noqa: E402


def test_detect_image_format_identifies_jpeg():
    image_format = detect_image_format(b"\xff\xd8\xff\xe0\x00\x10JFIF\x00\x01")

    assert image_format.extension == "jpg"
    assert image_format.mime_type == "image/jpeg"


def test_detect_image_format_uses_mime_hint_for_unknown_bytes():
    image_format = detect_image_format(b"unknown", fallback_mime="image/webp; charset=binary")

    assert image_format.extension == "webp"
    assert image_format.mime_type == "image/webp"
