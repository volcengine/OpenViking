# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0
"""Regression tests for offloading synchronous document conversions."""

import sys
from pathlib import Path
from types import SimpleNamespace
from typing import Any, Callable

import pytest

from openviking.parse.base import NodeType, ResourceNode, create_parse_result
from openviking.parse.parsers import epub, excel, legacy_doc, powerpoint, word


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


@pytest.mark.asyncio
async def test_word_parser_offloads_docx_conversion(monkeypatch, tmp_path: Path):
    parser = word.WordParser()
    seen = _stub_markdown_parse(parser)
    calls = _patch_to_thread(monkeypatch, word)
    fake_docx = SimpleNamespace()
    monkeypatch.setitem(sys.modules, "docx", fake_docx)

    def convert(path: Path, docx_module, resource_name=None, storage=None) -> str:
        assert docx_module is fake_docx
        return "# converted docx"

    monkeypatch.setattr(parser, "_convert_to_markdown", convert)
    source = tmp_path / "sample.docx"
    source.write_bytes(b"placeholder")

    result = await parser.parse(source)

    # #2429 added resource_name/storage to the offloaded conversion call; assert
    # the conversion was offloaded with the doc + docx module, without pinning the
    # identity of the storage singleton.
    assert len(calls) == 1
    func, args, _ = calls[0]
    assert func is convert
    assert args[0] == source
    assert args[1] is fake_docx
    assert seen["content"] == "# converted docx"
    assert result.source_format == "docx"
    assert result.parser_name == "WordParser"


@pytest.mark.asyncio
async def test_word_parser_forwards_original_name_to_markdown(
    monkeypatch, tmp_path: Path
):
    # On single-file upload the on-disk path is a temp name (upload_<uuid>.docx)
    # and the user's original filename arrives via source_name. WordParser must
    # forward that name to MarkdownParser explicitly; otherwise parse_content can
    # only fall back to the temp path and the resource ends up named upload_<uuid>.
    parser = word.WordParser()
    seen = _stub_markdown_parse(parser)
    _patch_to_thread(monkeypatch, word)
    monkeypatch.setitem(sys.modules, "docx", SimpleNamespace())
    monkeypatch.setattr(
        parser, "_convert_to_markdown", lambda *args, **kwargs: "# converted docx"
    )

    upload = tmp_path / "upload_abc123.docx"
    upload.write_bytes(b"placeholder")

    await parser.parse(upload, source_name="季度报告.docx")

    forwarded = seen["kwargs"].get("source_name") or seen["kwargs"].get("resource_name")
    assert forwarded == "季度报告.docx", seen["kwargs"]


@pytest.mark.asyncio
async def test_excel_parser_offloads_xlsx_conversion(monkeypatch, tmp_path: Path):
    parser = excel.ExcelParser()
    seen = _stub_markdown_parse(parser)
    calls = _patch_to_thread(monkeypatch, excel)
    fake_openpyxl = SimpleNamespace()
    monkeypatch.setitem(sys.modules, "openpyxl", fake_openpyxl)

    def convert(path: Path, openpyxl_module) -> str:
        assert openpyxl_module is fake_openpyxl
        return "# converted xlsx"

    monkeypatch.setattr(parser, "_convert_to_markdown", convert)
    source = tmp_path / "sample.xlsx"
    source.write_bytes(b"placeholder")

    result = await parser.parse(source)

    assert calls == [(convert, (source, fake_openpyxl), {})]
    assert seen["content"] == "# converted xlsx"
    assert result.source_format == "xlsx"
    assert result.parser_name == "ExcelParser"


@pytest.mark.asyncio
async def test_excel_parser_offloads_xls_conversion(monkeypatch, tmp_path: Path):
    parser = excel.ExcelParser()
    seen = _stub_markdown_parse(parser)
    calls = _patch_to_thread(monkeypatch, excel)

    def convert(path: Path) -> str:
        return "# converted xls"

    monkeypatch.setattr(parser, "_convert_xls_to_markdown", convert)
    source = tmp_path / "sample.xls"
    source.write_bytes(b"placeholder")

    result = await parser.parse(source)

    assert calls == [(convert, (source,), {})]
    assert seen["content"] == "# converted xls"
    assert result.source_format == "xls"
    assert result.parser_name == "ExcelParser"


@pytest.mark.asyncio
async def test_powerpoint_parser_offloads_pptx_conversion(monkeypatch, tmp_path: Path):
    parser = powerpoint.PowerPointParser()
    seen = _stub_markdown_parse(parser)
    calls = _patch_to_thread(monkeypatch, powerpoint)
    fake_pptx = SimpleNamespace()
    monkeypatch.setitem(sys.modules, "pptx", fake_pptx)

    def convert(path: Path, pptx_module) -> str:
        assert pptx_module is fake_pptx
        return "# converted pptx"

    monkeypatch.setattr(parser, "_convert_to_markdown", convert)
    source = tmp_path / "sample.pptx"
    source.write_bytes(b"placeholder")

    result = await parser.parse(source)

    assert calls == [(convert, (source, fake_pptx), {})]
    assert seen["content"] == "# converted pptx"
    assert result.source_format == "pptx"
    assert result.parser_name == "PowerPointParser"


@pytest.mark.asyncio
async def test_epub_parser_offloads_epub_conversion(monkeypatch, tmp_path: Path):
    parser = epub.EPubParser()
    seen = _stub_markdown_parse(parser)
    calls = _patch_to_thread(monkeypatch, epub)

    def convert(path: Path) -> str:
        return "# converted epub"

    monkeypatch.setattr(parser, "_convert_to_markdown", convert)
    source = tmp_path / "sample.epub"
    source.write_bytes(b"placeholder")

    result = await parser.parse(source)

    assert calls == [(convert, (source,), {})]
    assert seen["content"] == "# converted epub"
    assert result.source_format == "epub"
    assert result.parser_name == "EPubParser"


@pytest.mark.asyncio
async def test_legacy_doc_parser_offloads_doc_extraction(monkeypatch, tmp_path: Path):
    parser = legacy_doc.LegacyDocParser()
    seen = _stub_markdown_parse(parser)
    calls = _patch_to_thread(monkeypatch, legacy_doc)

    def extract(path: Path) -> str:
        return "# converted doc"

    monkeypatch.setattr(parser, "_extract_text", extract)
    source = tmp_path / "sample.doc"
    source.write_bytes(b"placeholder")

    result = await parser.parse(source)

    assert calls == [(extract, (source,), {})]
    assert seen["content"] == "# converted doc"
    assert result.source_format == "doc"
    assert result.parser_name == "LegacyDocParser"
