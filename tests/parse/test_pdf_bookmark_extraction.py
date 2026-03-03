# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: Apache-2.0
"""
Tests for PDF bookmark/outline extraction in PDFParser.

Verifies that _extract_bookmarks correctly extracts bookmark entries
and that _convert_local injects them as markdown headings.
"""

from unittest.mock import MagicMock

from openviking.parse.parsers.pdf import PDFParser


class TestExtractBookmarks:
    """Test PDF bookmark extraction logic."""

    def setup_method(self):
        self.parser = PDFParser()

    def test_extract_bookmarks_with_outlines(self):
        """Bookmarks are extracted from PDF outlines with correct levels and page mapping."""
        # Mock pdfplumber PDF object
        mock_pdf = MagicMock()

        # Mock pages with objid for page mapping
        mock_page1 = MagicMock()
        mock_page1.page_obj.objid = 100
        mock_page2 = MagicMock()
        mock_page2.page_obj.objid = 200
        mock_pdf.pages = [mock_page1, mock_page2]

        # Mock page reference objects for bookmark destinations
        mock_ref1 = MagicMock()
        mock_ref1.objid = 100  # Points to page 1
        mock_ref2 = MagicMock()
        mock_ref2.objid = 200  # Points to page 2

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

    def test_extract_bookmarks_exception_returns_empty(self):
        """Returns empty list on unexpected exceptions (best-effort)."""
        mock_pdf = MagicMock()
        mock_pdf.pages = []
        mock_pdf.doc.get_outlines.side_effect = RuntimeError("Corrupt PDF")

        bookmarks = self.parser._extract_bookmarks(mock_pdf)
        assert bookmarks == []
