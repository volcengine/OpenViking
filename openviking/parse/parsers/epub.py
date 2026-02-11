# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: Apache-2.0
"""
EPub (.epub) parser for OpenViking.

Converts EPub e-books to Markdown then parses using MarkdownParser.
Inspired by microsoft/markitdown approach.
"""

import html
import logging
import zipfile
from pathlib import Path
from typing import TYPE_CHECKING, List, Optional, Union

from openviking.parse.base import ParseResult
from openviking.parse.parsers.base_parser import BaseParser

logger = logging.getLogger(__name__)

if TYPE_CHECKING:
    pass


class EPubParser(BaseParser):
    """
    EPub e-book parser for OpenViking.

    Supports: .epub

    Converts EPub e-books to Markdown using ebooklib (if available)
    or falls back to manual extraction, then delegates to MarkdownParser.

    Features:
    - Chapter/toc extraction
    - Metadata extraction (title, author, etc.)
    - HTML to markdown conversion
    - Image reference preservation
    """

    def __init__(self):
        """Initialize EPub parser."""
        self._markdown_parser = None
        self._ebooklib_module = None

    def _get_markdown_parser(self):
        """Lazy import MarkdownParser."""
        if self._markdown_parser is None:
            from openviking.parse.parsers.markdown import MarkdownParser

            self._markdown_parser = MarkdownParser()
        return self._markdown_parser

    def _get_ebooklib(self):
        """Lazy import ebooklib."""
        if self._ebooklib_module is None:
            try:
                import ebooklib
                from ebooklib import epub

                self._ebooklib_module = (ebooklib, epub)
            except ImportError:
                self._ebooklib_module = None
        return self._ebooklib_module

    @property
    def supported_extensions(self) -> List[str]:
        """Return list of supported file extensions."""
        return [".epub"]

    async def parse(self, source: Union[str, Path], instruction: str = "", **kwargs) -> ParseResult:
        """
        Parse EPub e-book from file path.

        Args:
            source: File path to .epub file
            instruction: Processing instruction
            **kwargs: Additional arguments

        Returns:
            ParseResult with document tree
        """
        path = Path(source)
        if not path.exists():
            raise FileNotFoundError(f"EPub file not found: {path}")

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
        Parse EPub content.

        Note: This expects the actual epub binary content, which isn't directly
        usable. Use parse() with a file path instead.

        Args:
            content: Not directly supported for binary epub content
            source_path: Optional source path for reference
            instruction: Processing instruction
            **kwargs: Additional arguments

        Returns:
            ParseResult with document tree
        """
        if source_path and Path(source_path).exists():
            return await self.parse(source_path, instruction, **kwargs)
        raise ValueError(
            "EPubParser.parse_content() requires a valid source_path to the .epub file"
        )

    def _convert_to_markdown(self, path: Path) -> str:
        """
        Convert EPub e-book to Markdown string.

        Args:
            path: Path to .epub file

        Returns:
            Markdown formatted string
        """
        ebooklib = self._get_ebooklib()

        if ebooklib:
            return self._convert_with_ebooklib(path, ebooklib)
        else:
            return self._convert_manual(path)

    def _convert_with_ebooklib(self, path: Path, ebooklib) -> str:
        """Convert EPub using ebooklib."""
        _, epub = ebooklib
        book = epub.read_epub(path)

        markdown_parts = []

        # Extract metadata
        title = self._get_metadata(book, "title")
        author = self._get_metadata(book, "creator")

        if title:
            markdown_parts.append(f"# {title}")
        if author:
            markdown_parts.append(f"**Author:** {author}")

        # Extract content items
        for item in book.get_items():
            if item.get_type() == ebooklib.ITEM_DOCUMENT:
                content = item.get_content().decode("utf-8", errors="ignore")
                md_content = self._html_to_markdown(content)
                if md_content.strip():
                    markdown_parts.append(md_content)

        return "\n\n".join(markdown_parts)

    def _get_metadata(self, book, key: str) -> str:
        """Get metadata from EPub book."""
        try:
            metadata = book.get_metadata("DC", key)
            if metadata:
                return metadata[0][0]
        except Exception:
            pass
        return ""

    def _convert_manual(self, path: Path) -> str:
        """Convert EPub manually using zipfile and HTML parsing."""
        markdown_parts = []

        with zipfile.ZipFile(path, "r") as zf:
            # List all HTML/XHTML files
            html_files = [f for f in zf.namelist() if f.endswith((".html", ".xhtml", ".htm"))]

            # Try to read content in order
            for html_file in sorted(html_files):
                try:
                    content = zf.read(html_file).decode("utf-8", errors="ignore")
                    md_content = self._html_to_markdown(content)
                    if md_content.strip():
                        markdown_parts.append(md_content)
                except Exception as e:
                    logger.warning(f"Failed to process {html_file}: {e}")

        return (
            "\n\n".join(markdown_parts)
            if markdown_parts
            else "# EPub Content\n\nUnable to extract content."
        )

    def _html_to_markdown(self, html_content: str) -> str:
        """Simple HTML to markdown conversion."""
        import re

        # Remove script and style tags
        html_content = re.sub(r"<script[^>]*>.*?</script>", "", html_content, flags=re.DOTALL)
        html_content = re.sub(r"<style[^>]*>.*?</style>", "", html_content, flags=re.DOTALL)

        # Convert headers
        html_content = re.sub(r"<h1[^>]*>(.*?)</h1>", r"# \1", html_content, flags=re.DOTALL)
        html_content = re.sub(r"<h2[^>]*>(.*?)</h2>", r"## \1", html_content, flags=re.DOTALL)
        html_content = re.sub(r"<h3[^>]*>(.*?)</h3>", r"### \1", html_content, flags=re.DOTALL)
        html_content = re.sub(r"<h4[^>]*>(.*?)</h4>", r"#### \1", html_content, flags=re.DOTALL)

        # Convert bold and italic
        html_content = re.sub(r"<strong>(.*?)</strong>", r"**\1**", html_content, flags=re.DOTALL)
        html_content = re.sub(r"<b>(.*?)</b>", r"**\1**", html_content, flags=re.DOTALL)
        html_content = re.sub(r"<em>(.*?)</em>", r"*\1*", html_content, flags=re.DOTALL)
        html_content = re.sub(r"<i>(.*?)</i>", r"*\1*", html_content, flags=re.DOTALL)

        # Convert paragraphs
        html_content = re.sub(r"<p[^>]*>(.*?)</p>", r"\1\n\n", html_content, flags=re.DOTALL)

        # Convert line breaks
        html_content = re.sub(r"<br\s*/?>", "\n", html_content)

        # Remove remaining HTML tags
        html_content = re.sub(r"<[^>]+>", "", html_content)

        # Unescape HTML entities
        html_content = html.unescape(html_content)

        # Normalize whitespace
        html_content = re.sub(r"\n\s*\n", "\n\n", html_content)
        html_content = re.sub(r"[ \t]+", " ", html_content)

        return html_content.strip()
