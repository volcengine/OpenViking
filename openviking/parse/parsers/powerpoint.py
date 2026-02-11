# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: Apache-2.0
"""
PowerPoint (.pptx) parser for OpenViking.

Converts PowerPoint presentations to Markdown then parses using MarkdownParser.
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


class PowerPointParser(BaseParser):
    """
    PowerPoint presentation parser for OpenViking.

    Supports: .pptx

    Converts PowerPoint presentations to Markdown using python-pptx,
    then delegates to MarkdownParser for tree structure creation.

    Features:
    - Slide-by-slide extraction
    - Title and content separation
    - Table conversion to markdown format
    - Text shape extraction
    - Notes extraction (optional)
    """

    def __init__(self, extract_notes: bool = False):
        """
        Initialize PowerPoint parser.

        Args:
            extract_notes: Whether to extract speaker notes
        """
        self.extract_notes = extract_notes
        self._markdown_parser = None
        self._pptx_module = None

    def _get_markdown_parser(self):
        """Lazy import MarkdownParser."""
        if self._markdown_parser is None:
            from openviking.parse.parsers.markdown import MarkdownParser

            self._markdown_parser = MarkdownParser()
        return self._markdown_parser

    def _get_pptx(self):
        """Lazy import python-pptx."""
        if self._pptx_module is None:
            try:
                import pptx

                self._pptx_module = pptx
            except ImportError:
                raise ImportError(
                    "python-pptx is required for PowerPoint parsing. "
                    "Install with: pip install python-pptx"
                )
        return self._pptx_module

    @property
    def supported_extensions(self) -> List[str]:
        """Return list of supported file extensions."""
        return [".pptx"]

    async def parse(self, source: Union[str, Path], instruction: str = "", **kwargs) -> ParseResult:
        """
        Parse PowerPoint presentation from file path.

        Args:
            source: File path to .pptx file
            instruction: Processing instruction
            **kwargs: Additional arguments

        Returns:
            ParseResult with document tree
        """
        path = Path(source)
        if not path.exists():
            raise FileNotFoundError(f"PowerPoint file not found: {path}")

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
        Parse PowerPoint content.

        Note: This expects the actual pptx binary content, which isn't directly
        usable. Use parse() with a file path instead.

        Args:
            content: Not directly supported for binary pptx content
            source_path: Optional source path for reference
            instruction: Processing instruction
            **kwargs: Additional arguments

        Returns:
            ParseResult with document tree
        """
        if source_path and Path(source_path).exists():
            return await self.parse(source_path, instruction, **kwargs)
        raise ValueError(
            "PowerPointParser.parse_content() requires a valid source_path to the .pptx file"
        )

    def _convert_to_markdown(self, path: Path) -> str:
        """
        Convert PowerPoint presentation to Markdown string.

        Args:
            path: Path to .pptx file

        Returns:
            Markdown formatted string
        """
        pptx = self._get_pptx()
        prs = pptx.Presentation(path)

        markdown_parts = []
        slide_count = len(prs.slides)

        for idx, slide in enumerate(prs.slides, 1):
            slide_parts = []

            # Slide header
            slide_parts.append(f"## Slide {idx}/{slide_count}")

            # Extract title
            title = self._extract_slide_title(slide)
            if title:
                slide_parts.append(f"### {title}")

            # Extract content from shapes
            content = self._extract_slide_content(slide)
            if content:
                slide_parts.append(content)

            # Extract notes if enabled
            if self.extract_notes and slide.has_notes_slide:
                notes = slide.notes_slide.notes_text_frame.text.strip()
                if notes:
                    slide_parts.append(f"**Notes:** {notes}")

            markdown_parts.append("\n\n".join(slide_parts))

        return "\n\n---\n\n".join(markdown_parts)

    def _extract_slide_title(self, slide) -> str:
        """Extract title from a slide."""
        for shape in slide.shapes:
            if shape.is_placeholder:
                placeholder_format = shape.placeholder_format
                if placeholder_format.type == 1:  # TITLE placeholder
                    return shape.text.strip()
        return ""

    def _extract_slide_content(self, slide) -> str:
        """Extract content from slide shapes."""
        content_parts = []

        for shape in slide.shapes:
            # Skip title shape (already extracted)
            if shape.is_placeholder:
                placeholder_format = shape.placeholder_format
                if placeholder_format.type == 1:  # TITLE
                    continue

            # Extract text from shape
            if hasattr(shape, "text") and shape.text.strip():
                # Check if it's a table
                if shape.has_table:
                    content_parts.append(self._convert_table(shape.table))
                else:
                    text = shape.text.strip()
                    if text:
                        content_parts.append(text)

        return "\n\n".join(content_parts)

    def _convert_table(self, table) -> str:
        """Convert PowerPoint table to markdown format."""
        if not table.rows:
            return ""

        rows = []
        for row in table.rows:
            row_data = [cell.text.strip() for cell in row.cells]
            rows.append(row_data)

        from openviking.parse.base import format_table_to_markdown

        return format_table_to_markdown(rows, has_header=True)
