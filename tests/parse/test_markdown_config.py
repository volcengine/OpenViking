# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0
"""Tests for MarkdownParser configuration wiring."""

from openviking.parse.parsers.markdown import MarkdownParser
from openviking.parse.registry import ParserRegistry
from openviking_cli.utils.config.parser_config import MarkdownConfig

CONTENT_WITH_FRONTMATTER = "---\ntitle: Kept\n---\nBody"


async def test_markdown_config_can_disable_frontmatter_extraction():
    parser = MarkdownParser(config=MarkdownConfig(extract_frontmatter=False))

    layout = await parser._compute_layout(CONTENT_WITH_FRONTMATTER, "viking://temp/test")

    assert layout.meta == {}
    assert [op.content for op in layout.ops if op.kind == "write"] == [CONTENT_WITH_FRONTMATTER]


async def test_markdown_parser_keeps_frontmatter_extraction_enabled_by_default():
    parser = MarkdownParser()

    layout = await parser._compute_layout(CONTENT_WITH_FRONTMATTER, "viking://temp/test")

    assert layout.meta == {"frontmatter": {"title": "Kept"}}
    assert [op.content for op in layout.ops if op.kind == "write"] == ["Body"]


async def test_explicit_frontmatter_option_overrides_markdown_config():
    parser = MarkdownParser(
        extract_frontmatter=True,
        config=MarkdownConfig(extract_frontmatter=False),
    )

    layout = await parser._compute_layout(CONTENT_WITH_FRONTMATTER, "viking://temp/test")

    assert layout.meta == {"frontmatter": {"title": "Kept"}}


async def test_explicit_false_frontmatter_option_overrides_markdown_config():
    parser = MarkdownParser(
        extract_frontmatter=False,
        config=MarkdownConfig(extract_frontmatter=True),
    )

    layout = await parser._compute_layout(CONTENT_WITH_FRONTMATTER, "viking://temp/test")

    assert layout.meta == {}
    assert [op.content for op in layout.ops if op.kind == "write"] == [CONTENT_WITH_FRONTMATTER]


def test_registry_uses_markdown_frontmatter_config():
    registry = ParserRegistry(
        parser_configs={"markdown": MarkdownConfig(extract_frontmatter=False)}
    )

    parser = registry.get_parser_for_file("readme.md")

    assert isinstance(parser, MarkdownParser)
    assert parser.extract_frontmatter is False
