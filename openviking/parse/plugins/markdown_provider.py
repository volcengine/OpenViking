# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0
"""Builtin provider for the markdown parser."""

from typing import List, Optional

from openviking.parse.parsers.base_parser import BaseParser
from openviking.parse.parsers.markdown import MarkdownParser
from openviking.parse.plugin_base import ParserProvider
from openviking_cli.utils.config.parser_config import ParserConfig


class MarkdownParserProvider(ParserProvider):
    """Create markdown parser instances for the registry."""

    name = "markdown"

    @property
    def supported_extensions(self) -> List[str]:
        return [".md", ".markdown", ".mdown", ".mkd"]

    def create_parser(self, config: Optional[ParserConfig] = None) -> BaseParser:
        return MarkdownParser(config=config)


PROVIDER = MarkdownParserProvider()


def get_provider() -> ParserProvider:
    return PROVIDER
