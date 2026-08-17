# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0
"""Contract tests for pdf-inspector text and pdfplumber image extraction."""

import sys
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

from openviking.parse.parsers.pdf import (
    PDFParser,
    _PdfImageExtraction,
    _PdfTextExtraction,
)


class _FakeStorage:
    def __init__(self, media_dir: Path) -> None:
        self.media_dir = media_dir
        self.saved: list[tuple[str, bytes, str, str]] = []

    def save_image(
        self,
        resource_name: str,
        data: bytes,
        *,
        filename: str,
        extension: str,
    ) -> Path:
        self.saved.append((resource_name, data, filename, extension))
        return self.media_dir / resource_name / "images" / f"{filename}{extension}"


def test_pdf_inspector_owns_page_markdown_and_reports_ocr_pages(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    extraction = SimpleNamespace(
        pages=[
            SimpleNamespace(page=0, markdown="# Introduction\n\nBody"),
            SimpleNamespace(page=1, markdown=""),
        ],
        pages_needing_ocr=[2],
        ocr_reasons_by_page=[SimpleNamespace(page=2, reasons=["no_text", "image_coverage"])],
        pages_with_tables=[1],
        pages_with_columns=[1],
        is_complex=True,
    )
    fake_module = SimpleNamespace(
        extract_pages_markdown=lambda path: extraction,
    )
    monkeypatch.setitem(sys.modules, "pdf_inspector", fake_module)

    result = PDFParser._extract_text_sync(tmp_path / "paper.pdf")

    assert result.pages == {1: "# Introduction\n\nBody", 2: ""}
    assert result.warnings == ["PDF page 2 requires OCR: no_text, image_coverage"]
    assert result.meta == {
        "total_pages": 2,
        "pages_processed": 1,
        "pages_needing_ocr": [2],
        "ocr_reasons_by_page": [{"page": 2, "reasons": ["no_text", "image_coverage"]}],
        "pages_with_tables": [1],
        "pages_with_columns": [1],
        "is_complex_layout": True,
    }


@pytest.mark.asyncio
async def test_pdf_markdown_keeps_page_order_and_places_images_after_text(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    parser = PDFParser()
    text = _PdfTextExtraction(
        pages={1: "# Page One", 2: ""},
        warnings=["PDF page 2 requires OCR: no_text"],
        meta={"total_pages": 2, "pages_needing_ocr": [2]},
    )
    images = _PdfImageExtraction(
        pages={
            1: ["![first](doc/images/page1_img1.png)"],
            2: ["![scan](doc/images/page2_img1.png)"],
        },
        warnings=[],
        images_extracted=2,
        images_deduplicated=0,
    )
    monkeypatch.setattr(parser, "_extract_text_sync", lambda _path: text)
    monkeypatch.setattr(
        parser,
        "_extract_images_sync",
        lambda _path, _storage, _name: images,
    )

    markdown, meta, warnings = await parser._convert_to_markdown(
        tmp_path / "paper.pdf",
        storage=_FakeStorage(tmp_path / "media"),
        resource_name="doc",
    )

    assert markdown == (
        "<!-- Page 1 -->\n\n# Page One\n\n![first](doc/images/page1_img1.png)\n\n"
        "<!-- Page 2 -->\n\n![scan](doc/images/page2_img1.png)"
    )
    assert warnings == ["PDF page 2 requires OCR: no_text"]
    assert meta == {
        "total_pages": 2,
        "pages_needing_ocr": [2],
        "library": "pdf-inspector",
        "image_library": "pdfplumber",
        "images_extracted": 2,
        "images_deduplicated": 0,
    }


@pytest.mark.asyncio
async def test_pdf_image_failure_does_not_replace_text_result(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    parser = PDFParser()
    monkeypatch.setattr(
        parser,
        "_extract_text_sync",
        lambda _path: _PdfTextExtraction(
            pages={1: "Text survives"}, warnings=[], meta={"total_pages": 1}
        ),
    )

    def fail_images(*_args: Any) -> _PdfImageExtraction:
        raise RuntimeError("renderer unavailable")

    monkeypatch.setattr(parser, "_extract_images_sync", fail_images)

    markdown, meta, warnings = await parser._convert_to_markdown(
        tmp_path / "paper.pdf",
        storage=_FakeStorage(tmp_path / "media"),
        resource_name="doc",
    )

    assert markdown == "<!-- Page 1 -->\n\nText survives"
    assert meta["images_extracted"] == 0
    assert warnings == ["PDF image extraction failed: renderer unavailable"]


def test_pdfplumber_images_are_deduplicated_per_page_and_cache_is_released(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    class FakePage:
        width = 100
        height = 100
        images = [
            {"x0": 1, "top": 2, "x1": 30, "bottom": 40},
            {"x0": 1, "top": 2, "x1": 30, "bottom": 40},
        ]

        def __init__(self) -> None:
            self.closed = False

        def close(self) -> None:
            self.closed = True

    page = FakePage()

    class FakePDF:
        pages = [page]

        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return None

    monkeypatch.setitem(
        sys.modules,
        "pdfplumber",
        SimpleNamespace(open=lambda _path: FakePDF()),
    )
    parser = PDFParser()
    monkeypatch.setattr(parser, "_extract_image_from_page", lambda *_args: b"png")
    monkeypatch.setattr("openviking.parse.parsers.pdf.is_valid_image", lambda *_args: True)
    storage = _FakeStorage(tmp_path / "media")

    result = parser._extract_images_sync(tmp_path / "paper.pdf", storage, "paper")

    assert result.pages == {1: ["![Page 1 Image 1](paper/images/page1_img1.png)"]}
    assert result.images_extracted == 1
    assert result.images_deduplicated == 1
    assert page.closed is True
