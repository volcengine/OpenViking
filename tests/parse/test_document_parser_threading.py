# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0
"""Regression tests for offloading synchronous AnyDoc conversions."""

from pathlib import Path
from types import SimpleNamespace
from typing import Any, Callable

import pytest

from openviking.parse.base import NodeType, ResourceNode, create_parse_result
from openviking.parse.output import LocalParseOutputStore
from openviking.parse.parsers import anydoc, anydoc_converter, pdf
from openviking.parse.parsers.html import HTMLParser
from openviking.parse.parsers.text import TextParser
from openviking_cli.utils.config.parser_config import AnydocConfig


def _stub_markdown_parse(parser) -> dict[str, Any]:
    seen: dict[str, Any] = {}

    async def parse_content(
        content: str,
        source_path: str | None = None,
        instruction: str = "",
        **kwargs,
    ):
        seen["content"] = content
        seen["source_path"] = source_path
        seen["instruction"] = instruction
        seen["kwargs"] = kwargs
        return create_parse_result(
            root=ResourceNode(type=NodeType.ROOT),
            source_path=source_path,
            source_format="markdown",
            parser_name="MarkdownParser",
        )

    parser._md_parser.parse_content = parse_content
    return seen


def _patch_to_thread(monkeypatch, module) -> list[tuple[Callable[..., Any], tuple, dict]]:
    calls: list[tuple[Callable[..., Any], tuple, dict]] = []

    async def fake_to_thread(func, /, *args, **kwargs):
        calls.append((func, args, kwargs))
        return func(*args, **kwargs)

    monkeypatch.setattr(module.asyncio, "to_thread", fake_to_thread)
    return calls


def _conversion(markdown: str = "# converted docx", source_format: str = "docx"):
    return SimpleNamespace(
        markdown=markdown,
        source_format=source_format,
        images_saved=0,
        assets_referenced=0,
        warnings=(),
    )


@pytest.mark.asyncio
async def test_anydoc_parser_offloads_docx_conversion(monkeypatch, tmp_path: Path):
    parser = anydoc.AnyDocParser(anydoc_config=AnydocConfig(max_table_rows=7))
    seen = _stub_markdown_parse(parser)
    calls = _patch_to_thread(monkeypatch, anydoc)

    def convert(self, path: Path, *, resource_name, storage, max_table_rows=1000):
        return _conversion()

    monkeypatch.setattr(anydoc_converter.AnyDocConverter, "convert", convert)
    source = tmp_path / "sample.docx"
    source.write_bytes(b"placeholder")

    result = await parser.parse(source)

    assert len(calls) == 1
    func, args, call_kwargs = calls[0]
    assert func.__func__ is convert
    assert args == (source,)
    assert call_kwargs["resource_name"] == "sample"
    assert call_kwargs["max_table_rows"] == 7
    storage = call_kwargs["storage"]
    assert seen["content"] == "# converted docx"
    assert seen["kwargs"]["allowed_media_dirs"] == [storage.media_dir]
    assert result.source_format == "docx"
    assert result.parser_name == "AnyDocParser"


@pytest.mark.asyncio
async def test_pdf_parser_forwards_parse_output_store_to_markdown(monkeypatch, tmp_path: Path):
    parser = pdf.PDFParser()
    markdown_parser = parser._get_markdown_parser()
    seen = _stub_markdown_parse(SimpleNamespace(_md_parser=markdown_parser))
    source = tmp_path / "sample.pdf"
    source.write_bytes(b"%PDF-placeholder")
    store = LocalParseOutputStore(str(tmp_path / "artifacts"))

    async def convert(_path, *, resource_name=None):
        return "# converted pdf", {}

    monkeypatch.setattr(parser, "_convert_to_markdown", convert)

    await parser.parse(source, parse_output_store=store)

    assert seen["kwargs"]["parse_output_store"] is store


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "parser",
    [TextParser(), HTMLParser(), anydoc.AnyDocParser()],
)
async def test_markdown_delegate_parsers_write_local_artifacts(tmp_path: Path, parser) -> None:
    store = LocalParseOutputStore(str(tmp_path / type(parser).__name__))

    result = await parser.parse_content(
        "# title\n\nbody",
        source_path=str(tmp_path / "document.md"),
        parse_output_store=store,
    )

    assert result.artifact_ref is not None
    assert result.artifact_ref.backend == "local"


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("plain 中文" * 8000, "plain 中文" * 8000),
        ("`code` and *emphasis*", r"\`code` and \*emphasis*"),
        ("_under_score_ ~~strike~~ [label]", r"\_under_score_ \~\~strike\~~ \[label]"),
        ("a` lone * and _ and ~ and [x]", r"a` lone * and _ and ~ and \[x]"),
        ("prefix *a* then trailing *", r"prefix \*a* then trailing *"),
        ("a\\b <tag> | pipe", r"a\\b \<tag> | pipe"),
    ],
    ids=[
        "long-unformatted-run",
        "paired-markers",
        "mixed-markers",
        "unpaired-markers",
        "last-marker",
        "literal-text",
    ],
)
def test_anydoc_converter_preserves_docx_text(tmp_path: Path, text: str, expected: str):
    """Exercise real Office decoding and escaping, including long single text runs."""
    from xml.sax.saxutils import escape
    from zipfile import ZipFile

    source = tmp_path / "text.docx"
    with ZipFile(source, "w") as archive:
        archive.writestr(
            "[Content_Types].xml",
            '<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">'
            '<Override PartName="/word/document.xml" ContentType="application/vnd.'
            'openxmlformats-officedocument.wordprocessingml.document.main+xml"/></Types>',
        )
        archive.writestr(
            "_rels/.rels",
            '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
            '<Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/'
            '2006/relationships/officeDocument" Target="word/document.xml"/></Relationships>',
        )
        archive.writestr(
            "word/document.xml",
            '<w:document xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main">'
            f'<w:body><w:p><w:r><w:t xml:space="preserve">{escape(text)}</w:t></w:r></w:p>'
            "</w:body></w:document>",
        )

    result = anydoc_converter.AnyDocConverter().convert(source, resource_name="text", storage=None)

    assert result.markdown == expected + "\n"
    assert result.source_format == "docx"
    assert result.warnings == ()


def test_anydoc_docx_preserves_line_start_after_styled_runs(tmp_path: Path):
    """Alternating styled runs retain Markdown escaping across line breaks."""
    from zipfile import ZipFile

    source = tmp_path / "styled.docx"
    runs = (
        "<w:r><w:t>hello</w:t></w:r>"
        '<w:r><w:rPr><w:b/></w:rPr><w:t xml:space="preserve"> bold</w:t></w:r>'
    ) * 500
    runs += "<w:r><w:br/><w:t># heading</w:t></w:r>"
    runs += '<w:r><w:rPr><w:b/></w:rPr><w:t xml:space="preserve"> end</w:t></w:r>'
    with ZipFile(source, "w") as archive:
        archive.writestr(
            "[Content_Types].xml",
            '<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">'
            '<Override PartName="/word/document.xml" ContentType="application/vnd.'
            'openxmlformats-officedocument.wordprocessingml.document.main+xml"/></Types>',
        )
        archive.writestr(
            "_rels/.rels",
            '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
            '<Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/'
            '2006/relationships/officeDocument" Target="word/document.xml"/></Relationships>',
        )
        archive.writestr(
            "word/document.xml",
            '<w:document xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main">'
            f"<w:body><w:p>{runs}</w:p></w:body></w:document>",
        )

    result = anydoc_converter.AnyDocConverter().convert(
        source, resource_name="styled", storage=None
    )

    assert result.markdown == ("hello **bold**" * 500) + "\\\n\\# heading **end**\n"


def test_anydoc_inline_line_start_survives_empty_rendered_elements():
    """Empty note and math elements must not change the following text's context."""
    from openviking.parse.parsers.anydoc_renderer import _AnyDocMarkdownRenderer

    renderer = _AnyDocMarkdownRenderer(
        SimpleNamespace(blocks=[], notes=[], assets=[]),
        source_format="docx",
        resource_name="test",
        storage=None,
    )

    def text(value: str):
        return SimpleNamespace(kind="text", text=value, style=None)

    inlines = [
        text("before"),
        SimpleNamespace(kind="line_break"),
        SimpleNamespace(kind="anchor", anchor="missing"),
        SimpleNamespace(kind="note_ref", note_id="missing"),
        SimpleNamespace(kind="math", text=""),
        text("# heading"),
        SimpleNamespace(kind="checkbox", checked=True),
        text("next"),
    ]

    assert renderer._render_inlines(inlines, context="block") == ("before\\\n\\# heading[x] next")
