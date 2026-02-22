# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: Apache-2.0
"""
Plain text parser for OpenViking.

Delegates to MarkdownParser since plain text is just unformatted markdown.
"""

from pathlib import Path
from typing import List, Optional, Union

from openviking.parse.base import ParseResult
from openviking.parse.parsers.base_parser import BaseParser
from openviking_cli.utils.config.parser_config import ParserConfig


class TextParser(BaseParser):
    """Plain text parser - delegates to MarkdownParser."""

    def __init__(self, config: Optional[ParserConfig] = None):
        """Initialize text parser."""
        from openviking.parse.parsers.markdown import MarkdownParser

        self._md_parser = MarkdownParser(config=config)

    @property
    def supported_extensions(self) -> List[str]:
        return [".txt", ".text"]

    async def parse(self, source: Union[str, Path], instruction: str = "", **kwargs) -> ParseResult:
        """Parse from file path or content string."""
        return await self._md_parser.parse(source, **kwargs)

    async def parse_content(
        self, content: str, source_path: Optional[str] = None, instruction: str = "", **kwargs
    ) -> ParseResult:
        """Parse text content - delegates to MarkdownParser."""
        result = await self._md_parser.parse_content(content, source_path, **kwargs)
        result.source_format = "text"
        result.parser_name = "TextParser"
        return result
