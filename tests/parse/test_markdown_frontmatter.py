# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0
"""Tests for YAML frontmatter handling in MarkdownParser.

Frontmatter is parsed into ``ParseResult.meta`` but that metadata is never written
to VikingFS, so removing the block from the stored body loses the fields for good.
These tests pin the contract: parse always, remove only when explicitly configured.
"""

import pytest

from openviking.parse.parsers.markdown import MarkdownParser
from openviking_cli.utils.config.parser_config import MarkdownConfig, ParserConfig

FRONTMATTER = """---
id: stuck-pr-watch
name: PR watch
---
"""

BODY = """
# Guide

body text
"""

DOCUMENT = FRONTMATTER + BODY


def _written(layout) -> list[str]:
    return [op.content for op in layout.ops if op.kind == "write"]


@pytest.mark.asyncio
async def test_frontmatter_is_kept_in_stored_body_by_default() -> None:
    layout = await MarkdownParser()._compute_layout(
        DOCUMENT, "viking://temp/test", source_path="/tmp/stuck-pr-watch.md"
    )

    assert _written(layout) == [DOCUMENT]
    assert layout.meta["frontmatter"] == {"id": "stuck-pr-watch", "name": "PR watch"}


@pytest.mark.asyncio
async def test_frontmatter_is_kept_when_config_omits_the_option() -> None:
    # The registry passes a bare ParserConfig when no [parsers.markdown] section
    # exists; that must not silently enable removal.
    layout = await MarkdownParser(config=ParserConfig())._compute_layout(
        DOCUMENT, "viking://temp/test", source_path="/tmp/stuck-pr-watch.md"
    )

    assert _written(layout) == [DOCUMENT]
    assert layout.meta["frontmatter"] == {"id": "stuck-pr-watch", "name": "PR watch"}


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "parser",
    [
        MarkdownParser(extract_frontmatter=True),
        MarkdownParser(config=MarkdownConfig(extract_frontmatter=True)),
    ],
)
async def test_frontmatter_is_removed_only_when_explicitly_requested(
    parser: MarkdownParser,
) -> None:
    layout = await parser._compute_layout(
        DOCUMENT, "viking://temp/test", source_path="/tmp/stuck-pr-watch.md"
    )

    assert _written(layout) == [BODY]
    assert layout.meta["frontmatter"] == {"id": "stuck-pr-watch", "name": "PR watch"}


@pytest.mark.asyncio
async def test_frontmatter_title_still_names_the_document_when_kept() -> None:
    document = "---\ntitle: Release Notes\n---\n\n# Guide\n\nbody text\n"

    layout = await MarkdownParser()._compute_layout(
        document, "viking://temp/test", source_path="/tmp/whatever.md"
    )

    assert layout.doc_title == "Release Notes"
    assert _written(layout) == [document]
