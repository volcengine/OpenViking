# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0
"""anydoc as an optional PDF extraction strategy.

Division of labour: pdf-inspector *detects* (see test_pdf_scan_detection.py),
anydoc *extracts*. anydoc is roughly 16x faster than pdfplumber on text-heavy
books but drops images and most tables, so it is opt-in per PDFConfig.strategy
rather than a replacement for the "local" default.
"""

from types import SimpleNamespace
from unittest.mock import patch

import pytest

from openviking.parse.parsers.pdf import PDFParser
from openviking_cli.utils.config.parser_config import PDFConfig

LAZY_IMPORT = "openviking.parse.parsers.pdf.lazy_import"


def _anydoc(markdown: str = "## Title\n\nbody") -> SimpleNamespace:
    """Stand-in for the anydoc module, whose PDF path is markdown-only."""
    return SimpleNamespace(to_markdown=lambda *_: markdown)


def _parser(**overrides) -> PDFParser:
    return PDFParser(PDFConfig(strategy="anydoc", **overrides))


class TestStrategyValidation:
    def test_anydoc_is_accepted(self):
        PDFConfig(strategy="anydoc").validate()

    def test_unknown_strategy_is_still_rejected(self):
        with pytest.raises(ValueError):
            PDFConfig(strategy="nope").validate()


class TestConversion:
    @pytest.mark.asyncio
    async def test_anydoc_strategy_converts_through_anydoc(self, tmp_path):
        pdf = tmp_path / "book.pdf"
        pdf.write_bytes(b"%PDF-1.4")
        parser = _parser()

        with patch(LAZY_IMPORT, return_value=_anydoc()):
            markdown, meta = await parser._convert_to_markdown(pdf)

        assert "body" in markdown
        assert meta["strategy"] == "anydoc"
        assert meta["library"] == "anydoc"

    @pytest.mark.asyncio
    async def test_anydoc_receives_the_pdf_path(self, tmp_path):
        """to_markdown takes a path; to_document(bytes) rejects PDF outright."""
        pdf = tmp_path / "book.pdf"
        pdf.write_bytes(b"%PDF-1.4")
        seen = []

        def fake_to_markdown(path):
            seen.append(path)
            return "# x"

        parser = _parser()
        with patch(LAZY_IMPORT, return_value=SimpleNamespace(to_markdown=fake_to_markdown)):
            await parser._convert_to_markdown(pdf)

        assert seen == [str(pdf)]

    @pytest.mark.asyncio
    async def test_anydoc_failure_propagates(self, tmp_path):
        """anydoc raises NeedsOcrError for mixed PDFs -- all-or-nothing, no partial."""
        pdf = tmp_path / "book.pdf"
        pdf.write_bytes(b"%PDF-1.4")

        def boom(*_args):
            raise RuntimeError("page 3 needs OCR")

        parser = _parser()
        with patch(LAZY_IMPORT, return_value=SimpleNamespace(to_markdown=boom)):
            with pytest.raises(RuntimeError):
                await parser._convert_to_markdown(pdf)

    @pytest.mark.asyncio
    async def test_local_strategy_is_unaffected(self, tmp_path):
        """The new value is additive: pdfplumber stays the local default."""
        pdf = tmp_path / "book.pdf"
        pdf.write_bytes(b"%PDF-1.4")
        parser = PDFParser(PDFConfig(strategy="local"))

        with patch.object(
            parser, "_convert_local", return_value=("local md", {"strategy": "local"})
        ) as local:
            with patch(LAZY_IMPORT) as lazy:
                markdown, _ = await parser._convert_to_markdown(pdf)

        assert markdown == "local md"
        local.assert_awaited_once()
        lazy.assert_not_called()

    @pytest.mark.asyncio
    async def test_empty_anydoc_output_is_passed_through(self, tmp_path):
        pdf = tmp_path / "book.pdf"
        pdf.write_bytes(b"%PDF-1.4")
        parser = _parser()

        with patch(LAZY_IMPORT, return_value=_anydoc(markdown="")):
            markdown, meta = await parser._convert_to_markdown(pdf)

        assert markdown == ""
        assert meta["strategy"] == "anydoc"
