# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0
"""
EPub (.epub) parser for OpenViking.

Converts EPub e-books to Markdown then parses using MarkdownParser.
Inspired by microsoft/markitdown approach.
"""

import html
import re
import zipfile
from html.parser import HTMLParser
from pathlib import Path
from typing import List, Optional, Union

from openviking.parse.base import ParseResult
from openviking.parse.parsers.base_parser import BaseParser
from openviking_cli.utils.config.parser_config import ParserConfig
from openviking_cli.utils.logger import get_logger

logger = get_logger(__name__)


class _EPubMarkdownParser(HTMLParser):
    """Convert HTML fragments to simple markdown without regex tag stripping."""

    _HEADER_PREFIX = {"h1": "# ", "h2": "## ", "h3": "### ", "h4": "#### "}

    def __init__(self):
        super().__init__(convert_charrefs=True)
        self._parts: list[str] = []
        self._ignored_tag_stack = 0

    def handle_starttag(self, tag: str, attrs):
        normalized_tag = tag.lower()
        if normalized_tag in {"script", "style"}:
            self._ignored_tag_stack += 1
            return
        if self._ignored_tag_stack:
            return
        if normalized_tag in self._HEADER_PREFIX:
            self._ensure_block_break()
            self._parts.append(self._HEADER_PREFIX[normalized_tag])
        elif normalized_tag in {"strong", "b"}:
            self._parts.append("**")
        elif normalized_tag in {"em", "i"}:
            self._parts.append("*")
        elif normalized_tag == "li":
            self._parts.append("\n- ")
        elif normalized_tag == "br":
            self._parts.append("\n")
        elif normalized_tag in {"p", "div", "section", "article"}:
            self._ensure_block_break()

    def handle_endtag(self, tag: str):
        normalized_tag = tag.lower()
        if normalized_tag in {"script", "style"}:
            if self._ignored_tag_stack:
                self._ignored_tag_stack -= 1
            return
        if self._ignored_tag_stack:
            return
        if normalized_tag in self._HEADER_PREFIX or normalized_tag in {
            "p",
            "div",
            "section",
            "article",
        }:
            self._parts.append("\n\n")
        elif normalized_tag in {"strong", "b"}:
            self._parts.append("**")
        elif normalized_tag in {"em", "i"}:
            self._parts.append("*")

    def handle_data(self, data: str):
        if not self._ignored_tag_stack:
            self._parts.append(data)

    def get_markdown(self) -> str:
        return "".join(self._parts)

    def _ensure_block_break(self):
        if self._parts and not self._parts[-1].endswith("\n"):
            self._parts.append("\n\n")


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
        parser = _EPubMarkdownParser()
        parser.feed(html_content)
        parser.close()
        markdown = html.unescape(parser.get_markdown())

        # Normalize whitespace
        markdown = re.sub(r"\n\s*\n", "\n\n", markdown)
        markdown = re.sub(r"[ \t]+", " ", markdown)

        return markdown.strip()
