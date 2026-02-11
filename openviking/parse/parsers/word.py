# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: Apache-2.0
"""
Word document (.docx) parser for OpenViking.

Converts Word documents to Markdown then parses using MarkdownParser.
Inspired by microsoft/markitdown approach.
"""

import logging
from pathlib import Path
from typing import TYPE_CHECKING, List, Optional, Union

from openviking.parse.base import ParseResult
from openviking.parse.parsers.base_parser import BaseParser

logger = logging.getLogger(__name__)

if TYPE_CHECKING:
    pass


class WordParser(BaseParser):
    """
    Word document parser for OpenViking.

    Supports: .docx

    Converts Word documents to Markdown using python-docx,
    then delegates to MarkdownParser for tree structure creation.
    """

    def __init__(self):
        """Initialize Word parser."""
        self._markdown_parser = None

    def _get_markdown_parser(self):
        """Lazy import MarkdownParser."""
        if self._markdown_parser is None:
            from openviking.parse.parsers.markdown import MarkdownParser

            self._markdown_parser = MarkdownParser()
        return self._markdown_parser

    @property
    def supported_extensions(self) -> List[str]:
        """Return list of supported file extensions."""
        return [".docx"]

    async def parse(self, source: Union[str, Path], instruction: str = "", **kwargs) -> ParseResult:
        """Parse Word document from file path."""
        path = Path(source)
        if not path.exists():
            raise FileNotFoundError(f"Word document not found: {path}")

        try:
            import docx
        except ImportError:
            raise ImportError(
                "python-docx is required for Word document parsing. "
                "Install with: pip install python-docx"
            )

        markdown_content = self._convert_to_markdown(path, docx)
        return await self._get_markdown_parser().parse_content(
            markdown_content, str(path), instruction, **kwargs
        )

    async def parse_content(
        self,
        content: str,
        source_path: Optional[str] = None,
        instruction: str = "",
        **kwargs,
    ) -> ParseResult:
        """Parse Word document content."""
        if source_path and Path(source_path).exists():
            return await self.parse(source_path, instruction, **kwargs)
        raise ValueError(
            "WordParser.parse_content() requires a valid source_path to the .docx file"
        )

    def _convert_to_markdown(self, path: Path, docx) -> str:
        """Convert Word document to Markdown string."""
        doc = docx.Document(path)
        markdown_parts = []

        for paragraph in doc.paragraphs:
            if not paragraph.text.strip():
                continue

            style_name = paragraph.style.name if paragraph.style else "Normal"

            if style_name.startswith("Heading"):
                level = self._extract_heading_level(style_name)
                markdown_parts.append(f"{'#' * level} {paragraph.text}")
            else:
                text = self._convert_formatted_text(paragraph)
                markdown_parts.append(text)

        for table in doc.tables:
            markdown_parts.append(self._convert_table(table))

        return "\n\n".join(markdown_parts)

    def _extract_heading_level(self, style_name: str) -> int:
        """Extract heading level from style name."""
        try:
            if "Heading" in style_name:
                parts = style_name.split()
                for part in parts:
                    if part.isdigit():
                        return min(int(part), 6)
        except Exception:
            pass
        return 1

    def _convert_formatted_text(self, paragraph) -> str:
        """Convert paragraph with formatting to markdown."""
        text_parts = []
        for run in paragraph.runs:
            text = run.text
            if not text:
                continue
            if run.bold:
                text = f"**{text}**"
            if run.italic:
                text = f"*{text}*"
            if run.underline:
                text = f"<ins>{text}</ins>"
            text_parts.append(text)
        return "".join(text_parts)

    def _convert_table(self, table) -> str:
        """Convert Word table to markdown format."""
        if not table.rows:
            return ""

        rows = []
        for row in table.rows:
            row_data = [cell.text.strip() for cell in row.cells]
            rows.append(row_data)

        from openviking.parse.base import format_table_to_markdown

        return format_table_to_markdown(rows, has_header=True)
