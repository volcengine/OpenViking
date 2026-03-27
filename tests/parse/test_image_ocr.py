# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: Apache-2.0
"""Tests for OCR text extraction in ImageParser."""

import io
from unittest.mock import MagicMock, patch

import pytest
from PIL import Image

from openviking.parse.parsers.media.image import ImageParser
from openviking_cli.utils.config.parser_config import ImageConfig


def _create_test_image(width: int = 100, height: int = 50) -> bytes:
    """Create a simple test image and return as bytes."""
    img = Image.new("RGB", (width, height), color="white")
    buf = io.BytesIO()
    img.save(buf, format="PNG")
    return buf.getvalue()


@pytest.mark.asyncio
async def test_ocr_extract_returns_text():
    """OCR extraction returns text when pytesseract finds text in the image."""
    parser = ImageParser(config=ImageConfig(enable_ocr=True))
    image_bytes = _create_test_image()

    with patch.dict("sys.modules", {"pytesseract": MagicMock()}):
        import sys

        mock_pytesseract = sys.modules["pytesseract"]
        mock_pytesseract.image_to_string.return_value = "Hello World"

        result = await parser._ocr_extract(image_bytes, lang="eng")
        assert result == "Hello World"
        mock_pytesseract.image_to_string.assert_called_once()


@pytest.mark.asyncio
async def test_ocr_extract_returns_none_for_empty_text():
    """OCR extraction returns None when no text is found in the image."""
    parser = ImageParser(config=ImageConfig(enable_ocr=True))
    image_bytes = _create_test_image()

    with patch.dict("sys.modules", {"pytesseract": MagicMock()}):
        import sys

        mock_pytesseract = sys.modules["pytesseract"]
        mock_pytesseract.image_to_string.return_value = "   "

        result = await parser._ocr_extract(image_bytes, lang="eng")
        assert result is None


@pytest.mark.asyncio
async def test_ocr_extract_returns_none_when_pytesseract_not_installed():
    """OCR extraction returns None gracefully when pytesseract is not installed."""
    parser = ImageParser(config=ImageConfig(enable_ocr=True))
    image_bytes = _create_test_image()

    # Ensure pytesseract is not importable
    with patch.dict("sys.modules", {"pytesseract": None}):
        result = await parser._ocr_extract(image_bytes, lang="eng")
        assert result is None


@pytest.mark.asyncio
async def test_ocr_extract_handles_exception():
    """OCR extraction returns None and logs error when pytesseract raises."""
    parser = ImageParser(config=ImageConfig(enable_ocr=True))
    image_bytes = _create_test_image()

    with patch.dict("sys.modules", {"pytesseract": MagicMock()}):
        import sys

        mock_pytesseract = sys.modules["pytesseract"]
        mock_pytesseract.image_to_string.side_effect = RuntimeError("Tesseract not found")

        result = await parser._ocr_extract(image_bytes, lang="eng")
        assert result is None


@pytest.mark.asyncio
async def test_ocr_extract_passes_language_parameter():
    """OCR extraction passes the lang parameter to pytesseract."""
    parser = ImageParser(config=ImageConfig(enable_ocr=True, ocr_lang="chi_sim"))
    image_bytes = _create_test_image()

    with patch.dict("sys.modules", {"pytesseract": MagicMock()}):
        import sys

        mock_pytesseract = sys.modules["pytesseract"]
        mock_pytesseract.image_to_string.return_value = "你好世界"

        result = await parser._ocr_extract(image_bytes, lang="chi_sim")
        assert result == "你好世界"
        call_args = mock_pytesseract.image_to_string.call_args
        assert call_args[1]["lang"] == "chi_sim"
