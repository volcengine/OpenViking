# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: Apache-2.0
"""
Word document (.docx) parser for OpenViking.

Converts Word documents to Markdown then parses using MarkdownParser.
Inspired by microsoft/markitdown approach.
"""

from pathlib import Path
from typing import List, Optional, Union

from openviking.parse.base import ParseResult
from openviking.parse.parsers.base_parser import BaseParser
from openviking_cli.utils.config.parser_config import ParserConfig
from openviking_cli.utils.logger import get_logger

logger = get_logger(__name__)


class WordParser(BaseParser):
    """
    Word document parser for OpenViking.

    Supports: .docx

    Converts Word documents to Markdown using python-docx,
    then delegates to MarkdownParser for tree structure creation.
    """

    def __init__(self, config: Optional[ParserConfig] = None):
        """Initialize Word parser."""
        from openviking.parse.parsers.markdown import MarkdownParser

        self._md_parser = MarkdownParser(config=config)
        self.config = config or ParserConfig()

    @property
    def supported_extensions(self) -> List[str]:
        return [".docx"]

    async def parse(self, source: Union[str, Path], instruction: str = "", **kwargs) -> ParseResult:
        """Parse Word document from file path."""
        path = Path(source)

        if path.exists():
            import docx

            markdown_content = self._convert_to_markdown(path, docx)
            result = await self._md_parser.parse_content(
                markdown_content, source_path=str(path), instruction=instruction, **kwargs
            )
        else:
            result = await self._md_parser.parse_content(
                str(source), instruction=instruction, **kwargs
            )
        result.source_format = "docx"
        result.parser_name = "WordParser"
        return result

    async def parse_content(
        self, content: str, source_path: Optional[str] = None, instruction: str = "", **kwargs
    ) -> ParseResult:
        """Parse content - delegates to MarkdownParser."""
        result = await self._md_parser.parse_content(content, source_path, **kwargs)
        result.source_format = "docx"
        result.parser_name = "WordParser"
        return result

    def _convert_to_markdown(self, path: Path, docx) -> str:
        """Convert Word document to Markdown string.

        Iterates the document body in order so that tables appear in their
        original position rather than being appended at the end.
        """
        doc = docx.Document(path)
        markdown_parts = []

        # Map XML table elements to python-docx Table objects for O(1) lookup
        table_by_element = {table._tbl: table for table in doc.tables}

        # Walk the document body in order to preserve table positions
        from docx.oxml.ns import qn

        for child in doc.element.body:
            if child.tag == qn("w:p"):
                # It's a paragraph
                from docx.text.paragraph import Paragraph

                paragraph = Paragraph(child, doc)
                if not paragraph.text.strip():
                    continue

                style_name = paragraph.style.name if paragraph.style else "Normal"

                if style_name.startswith("Heading"):
                    level = self._extract_heading_level(style_name)
                    markdown_parts.append(f"{'#' * level} {paragraph.text}")
                else:
                    text = self._convert_formatted_text(paragraph)
                    markdown_parts.append(text)

            elif child.tag == qn("w:tbl"):
                # It's a table
                if child in table_by_element:
                    markdown_parts.append(self._convert_table(table_by_element[child]))

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
