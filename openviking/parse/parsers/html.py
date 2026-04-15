# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0
"""
HTML Parser for OpenViking.

Parses local HTML files.

For URL downloading, use HTTPAccessor in the new two-layer architecture.
"""

import time
from pathlib import Path
from typing import List, Optional, Union

from openviking.parse.base import (
    NodeType,
    ParseResult,
    ResourceNode,
    create_parse_result,
)
from openviking.parse.parsers.base_parser import BaseParser

logger = __import__("openviking_cli.utils.logger").utils.logger.get_logger(__name__)


# Backward compatibility: Re-export URLType and URLTypeDetector from http_accessor
try:
    from openviking.parse.accessors.http_accessor import URLType, URLTypeDetector
except ImportError:
    URLType = None
    URLTypeDetector = None


class HTMLParser(BaseParser):
    """
    Parser for local HTML files.

    Features:
    - Parse local HTML files
    - Build hierarchy based on heading tags (h1-h6)
    - Filter out navigation, ads, and boilerplate
    - Extract tables and preserve structure

    NOTE: URL downloading functionality has been moved to HTTPAccessor
    in the new two-layer architecture. This parser only handles local files.
    """

    def __init__(self, timeout: float = 30.0, **kwargs):
        """
        Initialize HTML parser.

        Args:
            timeout: [DEPRECATED] Kept for backward compatibility.
                URL downloading has been moved to HTTPAccessor.
            **kwargs: Additional arguments (kept for backward compatibility)
        """
        pass

    @staticmethod
    def _extract_filename_from_url(url: str) -> str:
        """
        Extract and URL-decode the original filename from a URL.

        Args:
            url: URL to extract filename from

        Returns:
            Decoded filename (e.g., "schemas.py" from ".../schemas.py")
            Falls back to "download" if no filename can be extracted.
        """
        from pathlib import Path
        from urllib.parse import unquote, urlparse

        parsed = urlparse(url)
        # URL-decode path to handle encoded characters (e.g., %E7%99%BE -> Chinese chars)
        decoded_path = unquote(parsed.path)
        basename = Path(decoded_path).name
        return basename if basename else "download"

    def _get_readabilipy(self):
        """Lazy import of readabilipy."""
        if not hasattr(self, "_readabilipy") or self._readabilipy is None:
            try:
                from readabilipy import simple_json

                self._readabilipy = simple_json
            except ImportError:
                raise ImportError(
                    "readabilipy is required for HTML parsing. "
                    "Install it with: pip install readabilipy"
                )
        return self._readabilipy

    def _get_markdownify(self):
        """Lazy import of markdownify."""
        if not hasattr(self, "_markdownify") or self._markdownify is None:
            try:
                import markdownify

                self._markdownify = markdownify
            except ImportError:
                raise ImportError(
                    "markdownify is required for HTML parsing. "
                    "Install it with: pip install markdownify"
                )
        return self._markdownify

    @property
    def supported_extensions(self) -> List[str]:
        """List of supported file extensions."""
        return [".html", ".htm"]

    async def parse(self, source: Union[str, Path], instruction: str = "", **kwargs) -> ParseResult:
        """
        Parse a local HTML file.

        Args:
            source: HTML file path
            instruction: Processing instruction, guides LLM how to understand the resource
            **kwargs: Additional options

        Returns:
            ParseResult with document tree
        """
        start_time = time.time()
        path = Path(source)

        return await self._parse_local_file(path, start_time, **kwargs)

    async def _parse_local_file(self, path: Path, start_time: float, **kwargs) -> ParseResult:
        """Parse local HTML file."""
        if not path.exists():
            return create_parse_result(
                root=ResourceNode(type=NodeType.ROOT, content_path=None),
                source_path=str(path),
                source_format="html",
                parser_name="HTMLParser",
                parse_time=time.time() - start_time,
                warnings=[f"File not found: {path}"],
            )

        try:
            content = self._read_file(path)
            result = await self.parse_content(content, source_path=str(path), **kwargs)

            # Add timing info
            result.parse_time = time.time() - start_time
            result.parser_name = "HTMLParser"

            return result
        except Exception as e:
            return create_parse_result(
                root=ResourceNode(type=NodeType.ROOT, content_path=None),
                source_path=str(path),
                source_format="html",
                parser_name="HTMLParser",
                parse_time=time.time() - start_time,
                warnings=[f"Failed to read HTML: {e}"],
            )

    def _html_to_markdown(self, html: str, base_url: str = "") -> str:
        """
        Convert HTML to Markdown using readabilipy + markdownify (Anthropic approach).
        """
        markdownify = self._get_markdownify()

        # Preprocess: extract hidden content areas (e.g., WeChat public account's js_content)
        html = self._preprocess_html(html)

        # Use readabilipy to extract main content (based on Mozilla Readability)
        readabilipy = self._get_readabilipy()
        result = readabilipy.simple_json_from_html_string(html, use_readability=True)
        content_html = result.get("content") or html

        # Convert to markdown using markdownify
        markdown = markdownify.markdownify(
            content_html,
            heading_style=markdownify.ATX,
            strip=["script", "style"],
        )

        return markdown.strip()

    def _preprocess_html(self, html: str) -> str:
        """Preprocess HTML to fix hidden content and lazy loading issues (e.g., WeChat public accounts)."""
        from bs4 import BeautifulSoup

        soup = BeautifulSoup(html, "html.parser")

        # WeChat public account: js_content is hidden by default, need to remove hidden style
        js_content = soup.find(id="js_content")
        if js_content:
            if js_content.get("style"):
                del js_content["style"]
            # Handle lazy loading images: data-src -> src
            for img in js_content.find_all("img"):
                if img.get("data-src") and not img.get("src"):
                    img["src"] = img["data-src"]
            return str(js_content)

        return html

    async def parse_content(
        self, content: str, source_path: Optional[str] = None, instruction: str = "", **kwargs
    ) -> ParseResult:
        """
        Parse HTML content.

        Converts HTML to Markdown and delegates to MarkdownParser.

        Args:
            content: HTML content string
            source_path: Optional source path for reference

        Returns:
            ParseResult with document tree
        """
        # Convert HTML to Markdown
        markdown_content = self._html_to_markdown(content, base_url=source_path or "")

        # Delegate to MarkdownParser
        from openviking.parse.parsers.markdown import MarkdownParser

        md_parser = MarkdownParser()
        result = await md_parser.parse_content(markdown_content, source_path=source_path, **kwargs)

        # Update metadata
        result.source_format = "html"
        result.parser_name = "HTMLParser"

        return result
