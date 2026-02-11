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

from openviking.parse.base import ParseResult, format_table_to_markdown
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

    Features:
    - Paragraph and heading extraction
    - Table conversion to markdown format
    - List preservation (numbered and bulleted)
    - Text formatting (bold, italic, underline)
    """

    def __init__(self):
        """Initialize Word parser."""
        self._markdown_parser = None
        self._docx_module = None

    def _get_markdown_parser(self):
        """Lazy import MarkdownParser."""
        if self._markdown_parser is None:
            from openviking.parse.parsers.markdown import MarkdownParser

            self._markdown_parser = MarkdownParser()
        return self._markdown_parser

    def _get_docx(self):
        """Lazy import python-docx."""
        if self._docx_module is None:
            try:
                import docx

                self._docx_module = docx
            except ImportError:
                raise ImportError(
                    "python-docx is required for Word document parsing. "
                    "Install with: pip install python-docx"
                )
        return self._docx_module

    @property
    def supported_extensions(self) -> List[str]:
        """Return list of supported file extensions."""
        return [".docx"]

    async def parse(self, source: Union[str, Path], instruction: str = "", **kwargs) -> ParseResult:
        """
        Parse Word document from file path.

        Args:
            source: File path to .docx file
            instruction: Processing instruction
            **kwargs: Additional arguments

        Returns:
            ParseResult with document tree
        """
        path = Path(source)
        if not path.exists():
            raise FileNotFoundError(f"Word document not found: {path}")

        # Convert to markdown
        markdown_content = self._convert_to_markdown(path)

        # Delegate to MarkdownParser
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
        """
        Parse Word document content.

        Note: This expects the actual docx binary content, which isn't directly
        usable. Use parse() with a file path instead.

        Args:
            content: Not directly supported for binary docx content
            source_path: Optional source path for reference
            instruction: Processing instruction
            **kwargs: Additional arguments

        Returns:
            ParseResult with document tree
        """
        if source_path and Path(source_path).exists():
            return await self.parse(source_path, instruction, **kwargs)
        raise ValueError(
            "WordParser.parse_content() requires a valid source_path to the .docx file"
        )

    def _convert_to_markdown(self, path: Path) -> str:
        """
        Convert Word document to Markdown string.

        Args:
            path: Path to .docx file

        Returns:
            Markdown formatted string
        """
        docx = self._get_docx()
        doc = docx.Document(path)

        markdown_parts = []

        for paragraph in doc.paragraphs:
            if not paragraph.text.strip():
                continue

            # Get paragraph style name
            style_name = paragraph.style.name if paragraph.style else "Normal"

            # Convert based on style
            if style_name.startswith("Heading"):
                level = self._extract_heading_level(style_name)
                markdown_parts.append(f"{'#' * level} {paragraph.text}")
            elif self._is_list_paragraph(paragraph):
                markdown_parts.append(self._convert_list_item(paragraph))
            else:
                # Regular paragraph with formatting
                text = self._convert_formatted_text(paragraph)
                markdown_parts.append(text)

        # Process tables
        for table in doc.tables:
            markdown_parts.append(self._convert_table(table))

        return "\n\n".join(markdown_parts)

    def _extract_heading_level(self, style_name: str) -> int:
        """Extract heading level from style name (e.g., 'Heading 1' -> 1)."""
        try:
            if "Heading" in style_name:
                parts = style_name.split()
                for part in parts:
                    if part.isdigit():
                        return min(int(part), 6)  # Max 6 levels
        except Exception:
            pass
        return 1  # Default to level 1

    def _is_list_paragraph(self, paragraph) -> bool:
        """Check if paragraph is a list item."""
        style_name = paragraph.style.name if paragraph.style else ""
        return "List" in style_name or "Bullet" in style_name

    def _convert_list_item(self, paragraph) -> str:
        """Convert a list paragraph to markdown."""
        text = self._convert_formatted_text(paragraph)
        style_name = paragraph.style.name if paragraph.style else ""

        # Determine list type
        if "Number" in style_name or "List Number" in style_name:
            return f"1. {text}"
        else:
            return f"- {text}"

    def _convert_formatted_text(self, paragraph) -> str:
        """Convert paragraph with formatting to markdown."""
        text_parts = []

        for run in paragraph.runs:
            text = run.text
            if not text:
                continue

            # Apply formatting
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

        return format_table_to_markdown(rows, has_header=True)
