# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0
"""Parser-specific section limits affect the Markdown artifacts."""

from contextlib import nullcontext
from pathlib import Path
from types import SimpleNamespace

from openviking.parse.output import LocalParseOutputStore
from openviking.parse.parsers.html import HTMLParser
from openviking.parse.parsers.pdf import PDFParser
from openviking_cli.utils.config.parser_config import HTMLConfig, PDFConfig


async def test_pdf_parser_applies_configured_section_size(monkeypatch, tmp_path):
    paragraphs = [f"Page {i}: " + "PDF text. " * 10 for i in range(1, 4)]
    pages = [
        SimpleNamespace(extract_text=lambda text=text: text, extract_tables=list, images=[])
        for text in paragraphs
    ]
    monkeypatch.setattr("pdfplumber.open", lambda _: nullcontext(SimpleNamespace(pages=pages)))
    source = tmp_path / "sample.pdf"
    source.touch()
    store = LocalParseOutputStore(str(tmp_path / "artifacts"))
    parser = PDFParser(PDFConfig(strategy="local", heading_detection="none", max_section_chars=130))

    result = await parser.parse(source, parse_output_store=store)

    assert result.artifact_ref is not None
    assert result.warnings == []
    root = Path(result.artifact_ref.root) / result.artifact_ref.resource_rel
    sections = [file.read_text() for file in root.glob("*.md")]
    assert len(sections) == len(paragraphs)
    assert all(any(text.strip() in section for section in sections) for text in paragraphs)


async def test_html_parser_applies_configured_section_size(tmp_path):
    paragraphs = [
        "The initial setup creates an empty workspace for the parser. " * 3,
        "The preview lists converted documents with their original titles. " * 3,
        "The final step stores the generated pages with readable names. " * 3,
    ]
    article = "".join(
        f"<h2>Section {i}</h2><p>{paragraph}</p>" for i, paragraph in enumerate(paragraphs, 1)
    )
    source = tmp_path / "manual.html"
    source.write_text(f"<html><body><article>{article}</article></body></html>")
    store = LocalParseOutputStore(str(tmp_path / "artifacts"))
    parser = HTMLParser(config=HTMLConfig(max_section_chars=230))

    result = await parser.parse(source, parse_output_store=store)

    assert result.artifact_ref is not None
    assert result.warnings == []
    root = Path(result.artifact_ref.root) / result.artifact_ref.resource_rel
    sections = [file.read_text() for file in root.glob("*.md")]
    assert len(sections) == len(paragraphs)
    assert all(
        any(paragraph.strip() in section for section in sections) for paragraph in paragraphs
    )
