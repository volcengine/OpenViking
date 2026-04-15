# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0
"""
Tests for PDF bookmark/outline extraction in PDFParser.

Verifies that _extract_bookmarks correctly extracts bookmark entries
and that _convert_local injects them as markdown headings.
"""

from contextlib import nullcontext
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest

from openviking.parse.parsers.pdf import PDFParser


def _make_page(*, pageid=None, objid=None):
    """Create a minimal page stub for bookmark extraction tests."""
    return SimpleNamespace(page_obj=SimpleNamespace(pageid=pageid, objid=objid))


def _make_ref(objid):
    """Create a minimal PDF object reference stub."""
    return SimpleNamespace(objid=objid)


class _FakePage:
    """Minimal pdfplumber page stub for _convert_local tests."""

    def __init__(self, text: str):
        self._text = text
        self.images = []

    def extract_text(self):
        return self._text

    def extract_tables(self):
        return []


class TestExtractBookmarks:
    """Test PDF bookmark extraction logic."""

    def setup_method(self):
        self.parser = PDFParser()

    def test_extract_bookmarks_with_outlines(self):
        """Bookmarks are extracted from PDF outlines with correct levels and page mapping."""
        # Mock pdfplumber PDF object
        mock_pdf = MagicMock()

        # Real pdfminer outlines point at page.page_obj.pageid
        mock_pdf.pages = [_make_page(pageid=100), _make_page(pageid=200)]

        # Mock page reference objects for bookmark destinations
        mock_ref1 = _make_ref(100)  # Points to page 1
        mock_ref2 = _make_ref(200)  # Points to page 2

        # Mock document outlines: (level, title, dest, action, structelem)
        mock_pdf.doc.get_outlines.return_value = [
            (1, "Chapter 1", [mock_ref1, "/Fit"], None, None),
            (2, "Section 1.1", [mock_ref1, "/Fit"], None, None),
            (1, "Chapter 2", [mock_ref2, "/Fit"], None, None),
        ]

        bookmarks = self.parser._extract_bookmarks(mock_pdf)

        assert len(bookmarks) == 3
        assert bookmarks[0] == {"title": "Chapter 1", "level": 1, "page_num": 1}
        assert bookmarks[1] == {"title": "Section 1.1", "level": 2, "page_num": 1}
        assert bookmarks[2] == {"title": "Chapter 2", "level": 1, "page_num": 2}

    def test_extract_bookmarks_falls_back_to_objid_mapping(self):
        """Objid-based mapping remains supported for tests and alternate backends."""
        mock_pdf = MagicMock()
        mock_pdf.pages = [_make_page(objid=100), _make_page(objid=200)]

        mock_pdf.doc.get_outlines.return_value = [
            (1, "Chapter 1", [_make_ref(100), "/Fit"], None, None),
            (1, "Chapter 2", [_make_ref(200), "/Fit"], None, None),
        ]

        bookmarks = self.parser._extract_bookmarks(mock_pdf)
        assert [b["page_num"] for b in bookmarks] == [1, 2]

    def test_extract_bookmarks_no_outlines(self):
        """Returns empty list when PDF has no outlines."""
        mock_pdf = MagicMock()
        mock_pdf.pages = []
        mock_pdf.doc.get_outlines.return_value = []

        bookmarks = self.parser._extract_bookmarks(mock_pdf)
        assert bookmarks == []

    def test_extract_bookmarks_no_get_outlines(self):
        """Returns empty list when document has no get_outlines method."""
        mock_pdf = MagicMock()
        mock_pdf.pages = []
        del mock_pdf.doc.get_outlines  # Remove the method

        bookmarks = self.parser._extract_bookmarks(mock_pdf)
        assert bookmarks == []

    def test_extract_bookmarks_skips_empty_titles(self):
        """Bookmarks with empty or whitespace-only titles are skipped."""
        mock_pdf = MagicMock()
        mock_pdf.pages = []
        mock_pdf.doc.get_outlines.return_value = [
            (1, "", None, None, None),
            (1, "   ", None, None, None),
            (1, "Valid Title", None, None, None),
        ]

        bookmarks = self.parser._extract_bookmarks(mock_pdf)
        assert len(bookmarks) == 1
        assert bookmarks[0]["title"] == "Valid Title"

    def test_extract_bookmarks_caps_level_at_6(self):
        """Heading levels are capped at 6 for markdown compatibility."""
        mock_pdf = MagicMock()
        mock_pdf.pages = []
        mock_pdf.doc.get_outlines.return_value = [
            (10, "Deep Heading", None, None, None),
        ]

        bookmarks = self.parser._extract_bookmarks(mock_pdf)
        assert bookmarks[0]["level"] == 6

    def test_extract_bookmarks_unresolved_pages(self):
        """Bookmarks with unresolvable destinations get page_num=None."""
        mock_pdf = MagicMock()
        mock_pdf.pages = []
        mock_pdf.doc.get_outlines.return_value = [
            (1, "No Destination", None, None, None),
        ]

        bookmarks = self.parser._extract_bookmarks(mock_pdf)
        assert len(bookmarks) == 1
        assert bookmarks[0]["page_num"] is None

    def test_extract_bookmarks_integer_page_index(self):
        """Bookmarks with integer destination (0-based) are resolved correctly."""
        mock_pdf = MagicMock()
        mock_pdf.pages = [_make_page(pageid=100), _make_page(pageid=200)]

        # Integer page indices instead of object references
        mock_pdf.doc.get_outlines.return_value = [
            (1, "Chapter 1", [0, "/Fit"], None, None),
            (1, "Chapter 2", [1, "/Fit"], None, None),
        ]

        bookmarks = self.parser._extract_bookmarks(mock_pdf)
        assert len(bookmarks) == 2
        assert bookmarks[0]["page_num"] == 1
        assert bookmarks[0]["title"] == "Chapter 1"
        assert bookmarks[1]["page_num"] == 2
        assert bookmarks[1]["title"] == "Chapter 2"

    def test_extract_bookmarks_integer_page_index_out_of_range(self):
        """Out-of-range integer page indices are treated as unresolved."""
        mock_pdf = MagicMock()
        mock_pdf.pages = [_make_page(pageid=100)]  # Only 1 page

        mock_pdf.doc.get_outlines.return_value = [
            (1, "Valid", [0, "/Fit"], None, None),
            (1, "Too High", [5, "/Fit"], None, None),
            (1, "Negative", [-1, "/Fit"], None, None),
        ]

        bookmarks = self.parser._extract_bookmarks(mock_pdf)
        assert len(bookmarks) == 3
        assert bookmarks[0]["page_num"] == 1
        assert bookmarks[1]["page_num"] is None
        assert bookmarks[2]["page_num"] is None

    def test_extract_bookmarks_exception_returns_empty(self):
        """Returns empty list on unexpected exceptions (best-effort)."""
        mock_pdf = MagicMock()
        mock_pdf.pages = []
        mock_pdf.doc.get_outlines.side_effect = RuntimeError("Corrupt PDF")

        bookmarks = self.parser._extract_bookmarks(mock_pdf)
        assert bookmarks == []


class TestConvertLocalBookmarks:
    """Test bookmark injection behavior in local PDF conversion."""

    @pytest.mark.asyncio
    async def test_convert_local_skips_unresolved_bookmarks(self):
        parser = PDFParser()
        fake_pdf = SimpleNamespace(pages=[_FakePage("Page one"), _FakePage("Page two")])
        fake_pdfplumber = SimpleNamespace(open=lambda _path: nullcontext(fake_pdf))

        with (
            patch("openviking.parse.parsers.pdf.lazy_import", return_value=fake_pdfplumber),
            patch.object(
                parser,
                "_extract_bookmarks",
                return_value=[
                    {"level": 1, "title": "Broken Bookmark", "page_num": None},
                    {"level": 1, "title": "Chapter 2", "page_num": 2},
                ],
            ),
        ):
            markdown, meta = await parser._convert_local(
                "dummy.pdf", storage=MagicMock(), resource_name="dummy"
            )

        assert "Broken Bookmark" not in markdown
        assert "\n# Chapter 2\n" in markdown
        assert meta["bookmarks_found"] == 2
        assert meta["bookmarks_resolved"] == 1
        assert meta["bookmarks_unresolved"] == 1
        assert meta["headings_found"] == 1
        assert meta["heading_source"] == "bookmarks"

    @pytest.mark.asyncio
    async def test_convert_local_falls_back_to_font_when_bookmarks_unresolved(self):
        parser = PDFParser()
        fake_pdf = SimpleNamespace(pages=[_FakePage("Page one"), _FakePage("Page two")])
        fake_pdfplumber = SimpleNamespace(open=lambda _path: nullcontext(fake_pdf))

        with (
            patch("openviking.parse.parsers.pdf.lazy_import", return_value=fake_pdfplumber),
            patch.object(
                parser,
                "_extract_bookmarks",
                return_value=[{"level": 1, "title": "Broken Bookmark", "page_num": None}],
            ),
            patch.object(
                parser,
                "_detect_headings_by_font",
                return_value=[{"level": 1, "title": "Font Heading", "page_num": 2}],
            ),
        ):
            markdown, meta = await parser._convert_local(
                "dummy.pdf", storage=MagicMock(), resource_name="dummy"
            )

        assert "Broken Bookmark" not in markdown
        assert "\n# Font Heading\n" in markdown
        assert meta["bookmarks_found"] == 1
        assert meta["bookmarks_resolved"] == 0
        assert meta["bookmarks_unresolved"] == 1
        assert meta["headings_found"] == 1
        assert meta["heading_source"] == "font_analysis"
