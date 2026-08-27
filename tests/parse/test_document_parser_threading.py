# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0
"""Regression tests for offloading synchronous AnyDoc conversions."""

from pathlib import Path
from types import SimpleNamespace
from typing import Any, Callable

import pytest

from openviking.parse.base import NodeType, ResourceNode, create_parse_result
from openviking.parse.parsers import anydoc, anydoc_converter
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
