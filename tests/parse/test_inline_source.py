# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0
"""Check source selection through the parser and registry entrypoints."""

from pathlib import Path

import pytest

from openviking.parse.output import LocalParseOutputStore
from openviking.parse.parsers.markdown import MarkdownParser
from openviking.parse.registry import ParserRegistry


@pytest.mark.asyncio
@pytest.mark.parametrize("parser_type", [MarkdownParser, ParserRegistry])
@pytest.mark.parametrize("use_file", [False, True], ids=["inline", "file"])
async def test_parse_long_line_from_inline_source_or_file(tmp_path, parser_type, use_file):
    content = ("An agent stored this paragraph. " * 16).rstrip()
    source: str | Path = content
    if use_file:
        source = tmp_path / "document.md"
        source.write_text(content, encoding="utf-8")

    output_root = tmp_path / "artifacts"
    result = await parser_type().parse(
        source, parse_output_store=LocalParseOutputStore(str(output_root))
    )

    assert not result.warnings
    files = list(output_root.rglob("*.md"))
    assert [file.read_text(encoding="utf-8") for file in files] == [content]
