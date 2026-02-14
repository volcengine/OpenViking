# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: Apache-2.0
"""
EPub (.epub) parser for OpenViking.

Converts EPub e-books to Markdown then parses using MarkdownParser.
Inspired by microsoft/markitdown approach.
"""

import html
import re
import zipfile
from pathlib import Path
from typing import List, Optional, Union

from openviking.parse.base import ParseResult
from openviking.parse.parsers.base_parser import BaseParser
from openviking_cli.utils.config.parser_config import ParserConfig
from openviking_cli.utils.logger import get_logger

logger = get_logger(__name__)


class EPubParser(BaseParser):
    """
    EPub e-book parser for OpenViking.

    Supports: .epub

    Converts EPub e-books to Markdown using ebooklib (if available)
    or falls back to manual extraction, then delegates to MarkdownParser.
    """

    def __init__(self, config: Optional[ParserConfig] = None):
        """Initialize EPub parser."""
        from openviking.parse.parsers.markdown import MarkdownParser

        self._md_parser = MarkdownParser(config=config)
        self.config = config or ParserConfig()

    @property
    def supported_extensions(self) -> List[str]:
        return [".epub"]

    async def parse(self, source: Union[str, Path], instruction: str = "", **kwargs) -> ParseResult:
        """Parse EPub e-book from file path."""
        path = Path(source)

        if path.exists():
            markdown_content = self._convert_to_markdown(path)
            result = await self._md_parser.parse_content(
                markdown_content, source_path=str(path), instruction=instruction, **kwargs
            )
        else:
            result = await self._md_parser.parse_content(
                str(source), instruction=instruction, **kwargs
            )
        result.source_format = "epub"
        result.parser_name = "EPubParser"
        return result

    async def parse_content(
        self, content: str, source_path: Optional[str] = None, instruction: str = "", **kwargs
    ) -> ParseResult:
        """Parse content - delegates to MarkdownParser."""
        result = await self._md_parser.parse_content(content, source_path, **kwargs)
        result.source_format = "epub"
        result.parser_name = "EPubParser"
        return result

    def _convert_to_markdown(self, path: Path) -> str:
        """Convert EPub e-book to Markdown string."""
        # Try using ebooklib first
        try:
            import ebooklib
            from ebooklib import epub

            return self._convert_with_ebooklib(path, ebooklib, epub)
        except ImportError:
            pass

        # Fall back to manual extraction
        return self._convert_manual(path)

    def _convert_with_ebooklib(self, path: Path, ebooklib, epub) -> str:
        """Convert EPub using ebooklib."""
        book = epub.read_epub(path)
        markdown_parts = []

        title = self._get_metadata(book, "title")
        author = self._get_metadata(book, "creator")

        if title:
            markdown_parts.append(f"# {title}")
        if author:
            markdown_parts.append(f"**Author:** {author}")

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
            html_files = [f for f in zf.namelist() if f.endswith((".html", ".xhtml", ".htm"))]

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
