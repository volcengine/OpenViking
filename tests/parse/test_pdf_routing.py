# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0
"""PDF extractor routing.

The thresholds come from a corpus of 1281 real PDFs; see ``pdf_routing`` for
what each one means. These tests pin the decision itself: given a set of
signals, which extractor gets the file.
"""

from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import pytest

from openviking.parse.parsers.pdf import PDFParser
from openviking.parse.parsers.pdf_routing import (
    EXTRACTOR_INSPECTOR,
    EXTRACTOR_MINERU,
    RoutingSignals,
    choose_extractor,
    code_ratio,
    page_aspect,
)
from openviking_cli.utils.config.parser_config import PDFConfig


def _signals(**overrides) -> RoutingSignals:
    """A healthy prose book: intact text layer, A4 portrait, no code."""
    base = {
        "pdf_type": "text_based",
        "page_count": 100,
        "ocr_pages": 0,
        "empty_pages": 0,
        "chars": 100_000,
        "code_ratio": 0.0,
        "aspect": 0.71,  # measured value for a real A4 book
    }
    base.update(overrides)
    return RoutingSignals(**base)


class TestChooseExtractor:
    def test_prose_goes_to_pdf_inspector(self):
        assert choose_extractor(_signals()) == EXTRACTOR_INSPECTOR

    @pytest.mark.parametrize("pdf_type", ["scanned", "image_based"])
    def test_scans_go_to_mineru(self, pdf_type):
        assert choose_extractor(_signals(pdf_type=pdf_type)) == EXTRACTOR_MINERU

    def test_partial_scan_goes_to_mineru(self):
        """pdf-inspector leaves the flagged pages blank; only OCR recovers them."""
        signals = _signals(pdf_type="mixed", ocr_pages=2)
        assert choose_extractor(signals) == EXTRACTOR_MINERU

    def test_silent_failure_goes_to_mineru(self):
        """Text layer present, classifier silent, but almost nothing came out."""
        signals = _signals(page_count=100, empty_pages=25, chars=300)
        assert choose_extractor(signals) == EXTRACTOR_MINERU

    def test_sparse_pages_with_healthy_text_stay(self):
        """Many blank pages but normal per-page volume: a picture book, not a failure."""
        signals = _signals(page_count=100, empty_pages=30, chars=200_000)
        assert choose_extractor(signals) == EXTRACTOR_INSPECTOR

    def test_slides_go_to_mineru(self):
        assert choose_extractor(_signals(aspect=1.78)) == EXTRACTOR_MINERU

    def test_landscape_book_is_not_a_slide(self):
        """A 1.61 landscape book exists in the corpus -- ratio alone would misfile it."""
        assert choose_extractor(_signals(aspect=1.61)) == EXTRACTOR_INSPECTOR

    def test_unknown_aspect_is_not_a_slide(self):
        assert choose_extractor(_signals(aspect=None)) == EXTRACTOR_INSPECTOR

    def test_code_dense_goes_to_mineru(self):
        assert choose_extractor(_signals(code_ratio=0.2)) == EXTRACTOR_MINERU

    def test_scan_outranks_every_other_signal(self):
        signals = _signals(pdf_type="scanned", aspect=0.71, code_ratio=0.0, chars=999_999)
        assert choose_extractor(signals) == EXTRACTOR_MINERU


class TestCodeRatio:
    def test_empty_markdown(self):
        assert code_ratio("") == 0.0

    def test_prose_has_no_code(self):
        assert code_ratio("plain prose, no fences at all. " * 20) == 0.0

    def test_fully_fenced_document(self):
        assert code_ratio("```\n" + "x = 1\n" * 20 + "```") == 1.0

    def test_prose_with_one_snippet_stays_below_threshold(self):
        fence = "```\n" + "x = 1\n" * 9 + "```"
        assert code_ratio("prose " * 400 + fence) < 0.05


def _write_pdf(path: Path, width: float, height: float) -> Path:
    """Minimal one-page PDF carrying nothing but a MediaBox."""
    objects = [
        "<< /Type /Catalog /Pages 2 0 R >>",
        "<< /Type /Pages /Kids [3 0 R] /Count 1 >>",
        f"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 {width} {height}] >>",
    ]
    out = bytearray(b"%PDF-1.4\n")
    offsets = []
    for number, body in enumerate(objects, start=1):
        offsets.append(len(out))
        out += f"{number} 0 obj\n{body}\nendobj\n".encode()
    xref_at = len(out)
    out += f"xref\n0 {len(objects) + 1}\n".encode()
    out += b"0000000000 65535 f \n"
    for offset in offsets:
        out += f"{offset:010d} 00000 n \n".encode()
    out += (
        f"trailer\n<< /Size {len(objects) + 1} /Root 1 0 R >>\nstartxref\n{xref_at}\n%%EOF\n"
    ).encode()
    path.write_bytes(bytes(out))
    return path


class TestPageAspect:
    def test_portrait_page(self, tmp_path):
        pdf = _write_pdf(tmp_path / "a4.pdf", 595.28, 841.89)
        assert page_aspect(pdf) == pytest.approx(0.707, abs=0.01)

    def test_landscape_page(self, tmp_path):
        pdf = _write_pdf(tmp_path / "slide.pdf", 960, 540)
        assert page_aspect(pdf) == pytest.approx(1.778, abs=0.01)

    def test_unreadable_file_is_unknown(self, tmp_path):
        """Aspect is a tiebreaker, never a hard requirement."""
        broken = tmp_path / "broken.pdf"
        broken.write_bytes(b"not a pdf at all")
        assert page_aspect(broken) is None


def _classification(pdf_type: str, page_count: int = 10, ocr_pages=(), confidence: float = 0.9):
    return SimpleNamespace(
        pdf_type=pdf_type,
        page_count=page_count,
        pages_needing_ocr=list(ocr_pages),
        confidence=confidence,
    )


def _auto_parser(**overrides) -> PDFParser:
    return PDFParser(PDFConfig(strategy="auto", **overrides))


PROSE = "prose " * 1000
INSPECTOR_RESULT = (PROSE, {"total_pages": 10, "empty_pages": 0})
MINERU_RESULT = ("mineru markdown", {"strategy": "mineru"})


class TestAutoStrategyRouting:
    """``auto`` routes on signals; it is no longer "local first, MinerU on error"."""

    @pytest.mark.asyncio
    async def test_prose_is_kept_as_is(self, tmp_path):
        parser = _auto_parser()
        with (
            patch.object(parser, "_classify_pdf", return_value=_classification("text_based")),
            patch.object(parser, "_convert_pdf_inspector", return_value=INSPECTOR_RESULT),
            patch.object(parser, "_convert_mineru", new=AsyncMock()) as mineru,
        ):
            markdown, meta = await parser._convert_to_markdown(tmp_path / "book.pdf")

        assert markdown == PROSE
        assert meta["routing"]["chosen"] == EXTRACTOR_INSPECTOR
        mineru.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_ocr_pages_escalate_to_mineru(self, tmp_path):
        parser = _auto_parser(mineru_endpoint="http://127.0.0.1:8000")
        with (
            patch.object(
                parser, "_classify_pdf", return_value=_classification("mixed", 10, [0, 1])
            ),
            patch.object(parser, "_convert_pdf_inspector", return_value=INSPECTOR_RESULT),
            patch.object(parser, "_convert_mineru", new=AsyncMock(return_value=MINERU_RESULT)),
        ):
            markdown, meta = await parser._convert_to_markdown(tmp_path / "book.pdf")

        assert markdown == MINERU_RESULT[0]
        assert meta["strategy"] == "mineru"

    @pytest.mark.asyncio
    async def test_escalation_without_endpoint_keeps_inspector_output(self, tmp_path):
        """Degrade, don't fail: the offline OCR batch owns these files, not this call."""
        parser = _auto_parser()  # no mineru_endpoint
        with (
            patch.object(
                parser, "_classify_pdf", return_value=_classification("mixed", 10, [0, 1])
            ),
            patch.object(parser, "_convert_pdf_inspector", return_value=INSPECTOR_RESULT),
            patch.object(parser, "_convert_mineru", new=AsyncMock()) as mineru,
        ):
            markdown, meta = await parser._convert_to_markdown(tmp_path / "book.pdf")

        assert markdown == PROSE
        assert meta["routing"]["chosen"] == EXTRACTOR_MINERU
        mineru.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_inspector_failure_falls_back_to_mineru(self, tmp_path):
        parser = _auto_parser(mineru_endpoint="http://127.0.0.1:8000")
        with (
            patch.object(parser, "_classify_pdf", return_value=None),
            patch.object(
                parser, "_convert_pdf_inspector", side_effect=ImportError("no pdf-inspector")
            ),
            patch.object(parser, "_convert_mineru", new=AsyncMock(return_value=MINERU_RESULT)),
        ):
            markdown, _ = await parser._convert_to_markdown(tmp_path / "book.pdf")

        assert markdown == MINERU_RESULT[0]

    @pytest.mark.asyncio
    async def test_inspector_failure_without_endpoint_raises(self, tmp_path):
        parser = _auto_parser()  # no mineru_endpoint
        with (
            patch.object(parser, "_classify_pdf", return_value=None),
            patch.object(
                parser, "_convert_pdf_inspector", side_effect=ImportError("no pdf-inspector")
            ),
        ):
            with pytest.raises(ImportError):
                await parser._convert_to_markdown(tmp_path / "book.pdf")

    @pytest.mark.asyncio
    async def test_missing_classifier_still_parses(self, tmp_path):
        """Classification is a signal source, not a gate -- losing it must not fail the parse."""
        parser = _auto_parser()
        with (
            patch.object(parser, "_classify_pdf", return_value=None),
            patch.object(parser, "_convert_pdf_inspector", return_value=INSPECTOR_RESULT),
        ):
            markdown, meta = await parser._convert_to_markdown(tmp_path / "book.pdf")

        assert markdown == PROSE
        assert meta["routing"]["chosen"] == EXTRACTOR_INSPECTOR
