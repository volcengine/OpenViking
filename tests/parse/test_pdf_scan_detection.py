# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0
"""Scanned-PDF detection.

Scanned PDFs carry no text layer, so local extraction yields nothing and the
blank document used to be committed as an empty resource. These tests pin the
two halves of the fix: the classification decision, and the short-circuit that
keeps the empty document out of storage.
"""

from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import pytest

from openviking.parse.parsers.pdf import PDFParser
from openviking_cli.utils.config.parser_config import PDFConfig

LAZY_IMPORT = "openviking.parse.parsers.pdf.lazy_import"


def _classification(pdf_type: str, page_count: int = 10, ocr_pages=(), confidence: float = 0.9):
    return SimpleNamespace(
        pdf_type=pdf_type,
        page_count=page_count,
        pages_needing_ocr=list(ocr_pages),
        confidence=confidence,
    )


def _parser(**overrides) -> PDFParser:
    return PDFParser(PDFConfig(strategy="local", **overrides))


def _with_classification(classification):
    """Patch lazy_import so classify_pdf returns the given classification."""
    return patch(
        LAZY_IMPORT,
        return_value=SimpleNamespace(classify_pdf=lambda _path: classification),
    )


class TestDetectionDecision:
    def test_scanned_pdf_is_flagged(self):
        parser = _parser()
        with _with_classification(_classification("scanned", 10, [0, 1, 2])):
            scan = parser._detect_scanned(Path("book.pdf"))
        assert scan is not None
        assert scan["pdf_type"] == "scanned"
        assert scan["pages_needing_ocr"] == 3
        assert scan["page_count"] == 10

    def test_image_based_pdf_is_flagged(self):
        parser = _parser()
        with _with_classification(_classification("image_based")):
            assert parser._detect_scanned(Path("book.pdf")) is not None

    def test_text_pdf_is_not_flagged(self):
        parser = _parser()
        with _with_classification(_classification("text_based")):
            assert parser._detect_scanned(Path("book.pdf")) is None

    def test_mixed_below_ratio_is_kept(self):
        """A mixed PDF that mostly has a text layer still yields content."""
        parser = _parser(scan_mixed_ratio=0.5)
        with _with_classification(_classification("mixed", 28, range(5))):
            assert parser._detect_scanned(Path("book.pdf")) is None

    def test_mixed_at_ratio_is_flagged(self):
        parser = _parser(scan_mixed_ratio=0.5)
        with _with_classification(_classification("mixed", 10, range(5))):
            assert parser._detect_scanned(Path("book.pdf")) is not None

    def test_mixed_ratio_is_configurable(self):
        parser = _parser(scan_mixed_ratio=0.1)
        with _with_classification(_classification("mixed", 28, range(5))):
            assert parser._detect_scanned(Path("book.pdf")) is not None

    def test_detection_can_be_disabled(self):
        parser = _parser(scan_detection=False)
        with patch(LAZY_IMPORT) as lazy:
            assert parser._detect_scanned(Path("book.pdf")) is None
        lazy.assert_not_called()

    def test_unavailable_inspector_is_not_fatal(self):
        """Detection is a guard: a missing/broken dependency must not break parsing."""
        parser = _parser()
        with patch(LAZY_IMPORT, side_effect=ImportError("no pdf-inspector")):
            assert parser._detect_scanned(Path("book.pdf")) is None


class TestParseShortCircuit:
    @pytest.mark.asyncio
    async def test_scanned_pdf_yields_no_temp_dir(self, tmp_path):
        """No temp_dir_path is what stops resource_processor from persisting."""
        pdf = tmp_path / "scanned.pdf"
        pdf.write_bytes(b"%PDF-1.4 not really")

        parser = _parser()
        with _with_classification(_classification("scanned", 12, range(12))):
            result = await parser.parse(pdf)

        assert result.temp_dir_path is None
        assert result.success is False
        assert any("Scanned PDF detected" in w for w in result.warnings)
        assert result.meta["scan_detection"]["pdf_type"] == "scanned"

    @pytest.mark.asyncio
    async def test_text_pdf_still_parses(self, tmp_path):
        """A normal PDF must not be short-circuited."""
        pdf = tmp_path / "text.pdf"
        pdf.write_bytes(b"%PDF-1.4 not really")

        parser = _parser()
        with _with_classification(_classification("text_based")):
            with patch.object(parser, "_convert_to_markdown", return_value=("# x\n", {})):
                result = await parser.parse(pdf)

        assert result.meta.get("scan_detection") is None
        assert result.source_format == "pdf"
