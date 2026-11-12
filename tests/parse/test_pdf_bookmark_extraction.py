# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0
"""
Tests for PDF bookmark/outline extraction in PDFParser.

Verifies that _extract_bookmarks correctly extracts bookmark entries
and that _convert_local injects them as markdown headings.
"""

import time
from concurrent.futures import ThreadPoolExecutor
from contextlib import nullcontext
from pathlib import Path
from threading import Barrier, Lock
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest
from pdfminer.pdftypes import PDFObjRef
from pdfminer.psparser import LIT

from openviking.parse.parsers.pdf import PDFParser


def _make_page(*, pageid=None, objid=None):
    """Create a minimal page stub for bookmark extraction tests."""
    return SimpleNamespace(page_obj=SimpleNamespace(pageid=pageid, objid=objid))


def _make_ref(objid):
    """Create a minimal PDF object reference stub."""
    return SimpleNamespace(objid=objid)


class _FakePDFStream(dict):
    """Minimal pdfminer PDFStream-like object."""

    def __init__(self, data: bytes, subtype: str = "Image"):
        super().__init__({"Subtype": SimpleNamespace(name=subtype)})
        self._data = data

    def get_data(self):
        return self._data


class _FakePDFObjectRef:
    """Minimal PDF object reference with a resolve() method."""

    def __init__(self, stream):
        self._stream = stream

    def resolve(self):
        return self._stream


def _make_image_page(xobjects):
    return SimpleNamespace(page_obj=SimpleNamespace(resources={"XObject": xobjects}))


class _FakeRenderedImage:
    """Minimal rendered image stub that writes deterministic PNG bytes."""

    def __init__(self, data: bytes):
        self._data = data

    def save(self, buffer, format: str):
        assert format == "PNG"
        buffer.write(self._data)


class _FakeCroppedPage:
    """Minimal cropped page stub for image extraction tests."""

    def __init__(self, rendered_image: _FakeRenderedImage):
        self._rendered_image = rendered_image
        self.resolution_calls = []

    def to_image(self, resolution: int):
        self.resolution_calls.append(resolution)
        return self._rendered_image


class _FakeImagePage:
    """Minimal pdfplumber page stub for _extract_image_from_page tests."""

    def __init__(self, *, width: int = 100, height: int = 200, image_bytes: bytes = b"png-bytes"):
        self.width = width
        self.height = height
        self.crop_calls = []
        self.cropped_page = _FakeCroppedPage(_FakeRenderedImage(image_bytes))

    def crop(self, bbox):
        self.crop_calls.append(bbox)
        return self.cropped_page


class _FakePage:
    """Minimal pdfplumber page stub for _convert_local tests."""

    def __init__(self, text: str):
        self._text = text
        self.images = []
        self.close_count = 0
        self.flush_count = 0

    def extract_text(self):
        return self._text

    def extract_tables(self):
        return []

    def flush_cache(self):
        self.flush_count += 1

    def close(self):
        self.close_count += 1
        self.flush_cache()


class _FakeFontPage(_FakePage):
    """Minimal page stub with character layout data for font heading detection."""

    def __init__(self, *, page_number: int, chars: list[dict], height: int = 1000):
        super().__init__("")
        self.page_number = page_number
        self.chars = chars
        self.height = height


class TestExtractBookmarks:
    """Test PDF bookmark extraction logic."""

    def setup_method(self):
        self.parser = PDFParser()

    def test_extract_bookmarks_with_outlines(self):
        """Local Dest and GoTo outlines map to pages; remote GoToR does not."""
        # Mock pdfplumber PDF object
        mock_pdf = MagicMock()

        # Real pdfminer outlines point at page.page_obj.pageid
        mock_pdf.pages = [_make_page(pageid=100), _make_page(pageid=200)]

        # Mock page reference objects for bookmark destinations
        mock_ref1 = _make_ref(100)  # Points to page 1
        mock_ref2 = _make_ref(200)  # Points to page 2
        mock_pdf.doc.getobj.return_value = {
            "S": LIT("GoTo"),
            "D": [mock_ref2, LIT("Fit")],
        }

        # Mock document outlines: (level, title, dest, action, structelem)
        mock_pdf.doc.get_outlines.return_value = [
            (1, "Chapter 1", [mock_ref1, LIT("Fit")], None, None),
            (2, "Section 1.1", None, {"S": LIT("GoTo"), "D": [mock_ref1, LIT("Fit")]}, None),
            (1, "Chapter 2", None, PDFObjRef(mock_pdf.doc, 300), None),
            (1, "External", None, {"S": LIT("GoToR"), "D": [0, LIT("Fit")]}, None),
        ]

        bookmarks = self.parser._extract_bookmarks(mock_pdf)

        assert bookmarks == [
            {"title": "Chapter 1", "level": 1, "page_num": 1},
            {"title": "Section 1.1", "level": 2, "page_num": 1},
            {"title": "Chapter 2", "level": 1, "page_num": 2},
            {"title": "External", "level": 1, "page_num": None},
        ]

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

    @pytest.mark.asyncio
    async def test_convert_local_skips_images_stacked_on_same_bbox(self):
        """Two image XObjects at the same spot must render (and save) only once."""
        parser = PDFParser()
        stacked = {"x0": 0.0, "top": 0.1, "x1": 595.0, "bottom": 841.9}
        page = _FakePage("Page one")
        page.images = [dict(stacked), dict(stacked)]
        fake_pdf = SimpleNamespace(pages=[page])
        fake_pdfplumber = SimpleNamespace(open=lambda _path: nullcontext(fake_pdf))
        storage = MagicMock()
        storage.save_image.return_value = Path("/media/dummy/images/page1_img1.png")
        storage.media_dir = Path("/media")

        with (
            patch("openviking.parse.parsers.pdf.lazy_import", return_value=fake_pdfplumber),
            patch.object(parser, "_extract_bookmarks", return_value=[]),
            patch.object(parser, "_detect_headings_by_font", return_value=[]),
            patch.object(parser, "_extract_image_from_page", return_value=b"png") as extract,
        ):
            markdown, meta = await parser._convert_local(
                "dummy.pdf", storage=storage, resource_name="dummy"
            )

        # The duplicate is dropped before rendering, so it never costs a render.
        assert extract.call_count == 1
        assert storage.save_image.call_count == 1
        assert meta["images_extracted"] == 1
        assert meta["images_deduplicated"] == 1
        assert markdown.count("![Page 1 Image") == 1

    @pytest.mark.asyncio
    async def test_convert_local_skips_images_rendering_to_same_bytes(self):
        """Distinct bboxes that still render identically are caught by the hash."""
        parser = PDFParser()
        page = _FakePage("Page one")
        page.images = [
            {"x0": 0.0, "top": 0.0, "x1": 100.0, "bottom": 100.0},
            {"x0": 5.0, "top": 5.0, "x1": 105.0, "bottom": 105.0},
        ]
        fake_pdf = SimpleNamespace(pages=[page])
        fake_pdfplumber = SimpleNamespace(open=lambda _path: nullcontext(fake_pdf))
        storage = MagicMock()
        storage.save_image.return_value = Path("/media/dummy/images/page1_img1.png")
        storage.media_dir = Path("/media")

        with (
            patch("openviking.parse.parsers.pdf.lazy_import", return_value=fake_pdfplumber),
            patch.object(parser, "_extract_bookmarks", return_value=[]),
            patch.object(parser, "_detect_headings_by_font", return_value=[]),
            patch.object(parser, "_extract_image_from_page", return_value=b"png") as extract,
        ):
            _markdown, meta = await parser._convert_local(
                "dummy.pdf", storage=storage, resource_name="dummy"
            )

        # Both are rendered (bboxes differ), but only one is saved.
        assert extract.call_count == 2
        assert storage.save_image.call_count == 1
        assert meta["images_extracted"] == 1
        assert meta["images_deduplicated"] == 1

    @pytest.mark.asyncio
    async def test_convert_local_keeps_distinct_images_and_repeats_across_pages(self):
        """Dedup is per-page: a logo on every page survives on every page."""
        parser = PDFParser()
        logo = {"x0": 0.0, "top": 0.0, "x1": 50.0, "bottom": 50.0}
        figure = {"x0": 0.0, "top": 200.0, "x1": 300.0, "bottom": 400.0}
        page1, page2 = _FakePage("Page one"), _FakePage("Page two")
        page1.images = [dict(logo), dict(figure)]
        page2.images = [dict(logo)]
        fake_pdf = SimpleNamespace(pages=[page1, page2])
        fake_pdfplumber = SimpleNamespace(open=lambda _path: nullcontext(fake_pdf))
        storage = MagicMock()
        storage.save_image.return_value = Path("/media/dummy/images/img.png")
        storage.media_dir = Path("/media")

        renders = {
            (0.0, 0.0, 50.0, 50.0): b"logo-png",
            (0.0, 200.0, 300.0, 400.0): b"figure-png",
        }

        with (
            patch("openviking.parse.parsers.pdf.lazy_import", return_value=fake_pdfplumber),
            patch.object(parser, "_extract_bookmarks", return_value=[]),
            patch.object(parser, "_detect_headings_by_font", return_value=[]),
            patch.object(
                parser,
                "_extract_image_from_page",
                side_effect=lambda _page, img: renders[
                    (img["x0"], img["top"], img["x1"], img["bottom"])
                ],
            ),
        ):
            _markdown, meta = await parser._convert_local(
                "dummy.pdf", storage=storage, resource_name="dummy"
            )

        assert meta["images_extracted"] == 3
        assert meta["images_deduplicated"] == 0

    @pytest.mark.asyncio
    async def test_convert_local_closes_page_after_each_page(self):
        parser = PDFParser()
        pages = [_FakePage("Page one"), _FakePage("Page two")]
        fake_pdf = SimpleNamespace(pages=pages)
        fake_pdfplumber = SimpleNamespace(open=lambda _path: nullcontext(fake_pdf))

        with (
            patch("openviking.parse.parsers.pdf.lazy_import", return_value=fake_pdfplumber),
            patch.object(parser, "_extract_bookmarks", return_value=[]),
            patch.object(parser, "_detect_headings_by_font", return_value=[]),
        ):
            await parser._convert_local("dummy.pdf", storage=MagicMock(), resource_name="dummy")

        assert [page.close_count for page in pages] == [1, 1]
        assert [page.flush_count for page in pages] == [1, 1]

    def test_detect_headings_by_font_closes_pages(self):
        parser = PDFParser()

        def chars(text: str, size: float, top: float) -> list[dict]:
            return [
                {"text": char, "size": size, "top": top, "x0": idx} for idx, char in enumerate(text)
            ]

        pages = [
            _FakeFontPage(
                page_number=1,
                chars=chars("Body text repeated", 10, 500) + chars("Heading", 14, 100),
            ),
            _FakeFontPage(
                page_number=2,
                chars=chars("Another heading", 14, 120),
            ),
            _FakeFontPage(
                page_number=3,
                chars=chars("Plain body", 10, 500),
            ),
            _FakeFontPage(
                page_number=4,
                chars=chars("More body", 10, 500),
            ),
        ]
        fake_pdf = SimpleNamespace(pages=pages)

        headings = parser._detect_headings_by_font(fake_pdf)

        assert [heading["title"] for heading in headings] == ["Heading", "Another heading"]
        assert [heading["page_num"] for heading in headings] == [1, 2]
        assert [page.close_count for page in pages] == [2, 1, 1, 1]
        assert [page.flush_count for page in pages] == [2, 1, 1, 1]


class TestExtractImages:
    """Test PDF XObject image extraction."""

    def setup_method(self):
        self.parser = PDFParser()

    def test_extract_image_renders_cropped_bbox_as_png(self, monkeypatch):
        pages = [_FakeImagePage(image_bytes=b"rendered-png") for _ in range(2)]
        parsers = [self.parser, PDFParser()]
        start = Barrier(2)
        rendering = Lock()
        original_render = _FakeCroppedPage.to_image

        def render(page, resolution):
            assert rendering.acquire(blocking=False), "Concurrent PDFium calls"
            try:
                # Native rendering releases the GIL; allow the other thread to run.
                time.sleep(0.05)
                return original_render(page, resolution)
            finally:
                rendering.release()

        monkeypatch.setattr(_FakeCroppedPage, "to_image", render)

        def extract(index):
            start.wait(timeout=5)
            return parsers[index]._extract_image_from_page(
                pages[index],
                {"x0": 10, "top": 20, "x1": 40, "bottom": 60},
            )

        with ThreadPoolExecutor(max_workers=2) as executor:
            assert list(executor.map(extract, range(2))) == [b"rendered-png"] * 2

        for parser, page in zip(parsers, pages, strict=True):
            assert page.crop_calls == [(10, 20, 40, 60)]
            assert page.cropped_page.resolution_calls == [parser.config.image_resolution]

    def test_extract_image_clamps_bbox_to_page_bounds(self):
        page = _FakeImagePage()

        self.parser._extract_image_from_page(
            page,
            {"x0": -5, "top": -10, "x1": 150, "bottom": 250},
        )

        assert page.crop_calls == [(0, 0, page.width, page.height)]

    def test_extract_image_returns_none_for_zero_area_bbox(self):
        page = _FakeImagePage()

        assert (
            self.parser._extract_image_from_page(
                page,
                {"x0": 30, "top": 40, "x1": 30, "bottom": 60},
            )
            is None
        )
        assert page.crop_calls == []

    def test_extract_image_returns_none_when_crop_fails(self):
        page = MagicMock(width=100, height=200)
        page.crop.side_effect = RuntimeError("crop failed")

        assert (
            self.parser._extract_image_from_page(
                page,
                {"x0": 10, "top": 20, "x1": 40, "bottom": 60},
            )
            is None
        )


def _write_font_heading_pdf(path: Path) -> None:
    """Write a real four-page PDF with unique headings on its first and last pages."""
    objects = [
        b"<< /Type /Catalog /Pages 2 0 R >>",
        b"<< /Type /Pages /Kids [4 0 R 6 0 R 8 0 R 10 0 R] /Count 4 >>",
        b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>",
    ]
    for page in range(1, 5):
        commands = ""
        if page in (1, 4):
            commands += f"BT /F1 18 Tf 50 750 Td (Chapter {page}) Tj ET\n"
        commands += (
            f"BT /F1 10 Tf 50 650 Td (Page {page} body with enough small letters "
            "to establish the dominant body font.) Tj ET\n"
        )
        stream = commands.encode()
        objects.append(
            (
                "<< /Type /Page /Parent 2 0 R /MediaBox [0 0 612 792] "
                "/Resources << /Font << /F1 3 0 R >> >> "
                f"/Contents {len(objects) + 2} 0 R >>"
            ).encode()
        )
        objects.append(f"<< /Length {len(stream)} >>\nstream\n".encode() + stream + b"endstream")
    data = bytearray(b"%PDF-1.4\n")
    offsets = []
    for number, obj in enumerate(objects, 1):
        offsets.append(len(data))
        data += f"{number} 0 obj\n".encode() + obj + b"\nendobj\n"
    xref = len(data)
    data += f"xref\n0 {len(objects) + 1}\n0000000000 65535 f \n".encode()
    for offset in offsets:
        data += f"{offset:010d} 00000 n \n".encode()
    data += (
        f"trailer\n<< /Size {len(objects) + 1} /Root 1 0 R >>\nstartxref\n{xref}\n%%EOF\n"
    ).encode()
    path.write_bytes(data)


@pytest.mark.asyncio
@pytest.mark.parametrize("heading_detection", ["font", "auto"])
async def test_real_pdf_font_headings_precede_their_own_pages(
    tmp_path: Path, heading_detection: str
):
    from openviking.parse.output import LocalParseOutputStore
    from openviking_cli.utils.config.parser_config import PDFConfig

    source = tmp_path / "chapters.pdf"
    _write_font_heading_pdf(source)
    parser = PDFParser(PDFConfig(strategy="local", heading_detection=heading_detection))
    result = await parser.parse(
        source,
        split_content=False,
        parse_output_store=LocalParseOutputStore(str(tmp_path / "artifacts")),
    )

    assert result.warnings == []
    assert result.meta["heading_source"] == "font_analysis"
    assert result.meta["headings_found"] == 2
    assert result.meta["pages_processed"] == 4
    assert result.artifact_ref is not None
    files = list(Path(result.artifact_ref.root).rglob("*.md"))
    assert len(files) == 1
    markdown = files[0].read_text()
    assert markdown.index("# Chapter 1\n") < markdown.index("<!-- Page 1 -->")
    assert markdown.index("<!-- Page 3 -->") < markdown.index("# Chapter 4\n")
    assert markdown.index("# Chapter 4\n") < markdown.index("<!-- Page 4 -->")
