# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0
"""Builtin provider for the text parser."""

from typing import List, Optional

from openviking.parse.parsers.base_parser import BaseParser
from openviking.parse.parsers.text import TextParser
from openviking.parse.plugin_base import ParserProvider
from openviking_cli.utils.config.parser_config import ParserConfig


class TextParserProvider(ParserProvider):
    """Create text parser instances for the registry."""

    name = "text"

    @property
    def supported_extensions(self) -> List[str]:
        return [".txt", ".text"]

    def create_parser(self, config: Optional[ParserConfig] = None) -> BaseParser:
        return TextParser(config=config)


PROVIDER = TextParserProvider()


def get_provider() -> ParserProvider:
    return PROVIDER
