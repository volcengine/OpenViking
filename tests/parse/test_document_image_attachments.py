# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0
"""Tests for document parsers that produce image attachments."""

import base64
from pathlib import Path

import pytest

from openviking.parse.base import (
    RESOURCE_ROOT_PLACEHOLDER,
    NodeType,
    ResourceNode,
    create_parse_result,
)
from openviking.parse.parsers.legacy_doc import LegacyDocParser
from openviking.parse.parsers.word import WordParser

_PNG_1X1 = base64.b64decode(
    "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAwMB/gL+X7QAAAAASUVORK5CYII="
)


def test_word_parser_extracts_docx_images_in_place(tmp_path: Path):
    docx = pytest.importorskip("docx")

    image_path = tmp_path / "diagram.png"
    image_path.write_bytes(_PNG_1X1)
    docx_path = tmp_path / "sample.docx"

    doc = docx.Document()
    paragraph = doc.add_paragraph("before ")
    paragraph.add_run().add_picture(str(image_path))
    paragraph.add_run(" after")
    doc.save(docx_path)

    markdown, attachments = WordParser()._convert_to_markdown(docx_path, docx)

    assert attachments == [{"path": "media/images/image-1.png", "content": _PNG_1X1}]
    assert (
        f"before ![image]({RESOURCE_ROOT_PLACEHOLDER}/media/images/image-1.png) after" in markdown
    )


@pytest.mark.asyncio
async def test_legacy_doc_parser_reuses_pdf_conversion_when_available(
    monkeypatch,
    tmp_path: Path,
):
    doc_path = tmp_path / "legacy.doc"
    doc_path.write_bytes(b"placeholder")
    pdf_path = tmp_path / "legacy.pdf"
    pdf_path.write_bytes(b"%PDF")
    parser = LegacyDocParser()

    async def convert_to_pdf(path):
        assert path == doc_path
        return pdf_path

    async def parse_pdf(path, instruction="", **kwargs):
        assert path == pdf_path
        assert instruction == "extract images"
        assert kwargs["resource_name"] == "legacy"
        return create_parse_result(
            root=ResourceNode(type=NodeType.ROOT),
            source_path=str(path),
            source_format="pdf",
            parser_name="PDFParser",
            meta={"images_extracted": 1},
        )

    monkeypatch.setattr(parser, "_convert_to_pdf", convert_to_pdf)
    monkeypatch.setattr(parser, "_parse_pdf", parse_pdf)

    result = await parser.parse(doc_path, instruction="extract images", resource_name="legacy")

    assert result.source_path == str(doc_path)
    assert result.source_format == "doc"
    assert result.parser_name == "LegacyDocParser"
    assert result.meta["images_extracted"] == 1
    assert result.meta["intermediate_format"] == "pdf"
