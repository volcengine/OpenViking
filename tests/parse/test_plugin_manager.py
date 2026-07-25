# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0
"""Tests for parser provider discovery and registry compatibility."""

import asyncio
from pathlib import Path
from unittest.mock import AsyncMock

from openviking.parse.base import NodeType, ResourceNode, create_parse_result
from openviking.parse.parsers.markdown import MarkdownParser
from openviking.parse.parsers.text import TextParser
from openviking.parse.plugin_manager import ParserPluginManager
from openviking.parse.registry import ParserRegistry


class NullPluginManager:
    """Plugin manager stub that forces the legacy registration path."""

    def create_parser(self, name: str):
        return None


def test_builtin_provider_discovery_finds_text_and_markdown():
    manager = ParserPluginManager()

    providers = manager.discover_providers()

    assert {"markdown", "text"}.issubset(providers)
    assert providers["markdown"].supported_extensions == [".md", ".markdown", ".mdown", ".mkd"]
    assert providers["text"].supported_extensions == [".txt", ".text"]
    assert "markdown" in manager.list_available_providers()
    assert "text" in manager.list_available_providers()


def test_registry_resolves_provider_backed_builtin_extensions():
    registry = ParserRegistry(register_optional=False)

    markdown_parser = registry.get_parser_for_file(Path("notes.md"))
    text_parser = registry.get_parser_for_file(Path("notes.txt"))

    assert isinstance(markdown_parser, MarkdownParser)
    assert isinstance(text_parser, TextParser)


def test_registry_parse_keeps_text_fallback_for_unknown_extensions(tmp_path: Path):
    registry = ParserRegistry(register_optional=False)
    source = tmp_path / "notes.custom"
    source.write_text("plain text fallback", encoding="utf-8")

    result = create_parse_result(
        root=ResourceNode(type=NodeType.ROOT),
        source_path=str(source),
        source_format="text",
        parser_name="TextParser",
    )
    text_parser = registry.get_parser("text")
    assert text_parser is not None
    text_parser.parse = AsyncMock(return_value=result)  # type: ignore[method-assign]

    parsed = asyncio.run(registry.parse(source))

    assert parsed is result
    text_parser.parse.assert_awaited_once_with(source)


def test_registry_falls_back_to_legacy_builtin_factories_when_provider_unavailable():
    registry = ParserRegistry(register_optional=False, plugin_manager=NullPluginManager())

    assert isinstance(registry.get_parser("markdown"), MarkdownParser)
    assert isinstance(registry.get_parser("text"), TextParser)
    assert isinstance(registry.get_parser_for_file(Path("legacy.md")), MarkdownParser)
    assert isinstance(registry.get_parser_for_file(Path("legacy.txt")), TextParser)
