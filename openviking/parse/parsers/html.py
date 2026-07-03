# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0
"""
HTML Parser for OpenViking.

Parses local HTML files.

For URL downloading, use HTTPAccessor in the new two-layer architecture.
"""

import re
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
from openviking_cli.utils.config.parser_config import HTMLConfig

logger = __import__("openviking_cli.utils.logger").utils.logger.get_logger(__name__)

_SPA_EMPTY_PATTERNS = (
    "You need to enable JavaScript to run this app.",
    "This app works best with JavaScript enabled.",
    "Please enable JavaScript to continue.",
    "JavaScript is required to use this application.",
    "Enable JavaScript to view this page.",
)


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

    def __init__(
        self,
        timeout: float = 30.0,
        config: Optional[HTMLConfig] = None,
        **kwargs,
    ):
        """
        Initialize HTML parser.

        Args:
            timeout: [DEPRECATED] Kept for backward compatibility.
                URL downloading has been moved to HTTPAccessor.
            **kwargs: Additional arguments (kept for backward compatibility)
        """
        self.config = config or HTMLConfig()
        self._markdown_parser = None

    def _get_markdown_parser(self):
        """Lazy import and create MarkdownParser with the HTML parser config."""
        if self._markdown_parser is None:
            from openviking.parse.parsers.markdown import MarkdownParser

            self._markdown_parser = MarkdownParser(config=self.config)
        return self._markdown_parser

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
            result = await self.parse_content(
                content, source_path=str(path), base_dir=path.parent, **kwargs
            )

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
        """Convert HTML to Markdown using trafilatura."""
        html = self._preprocess_html(html)
        content = self._extract_markdown(html, base_url or "")
        title = self._extract_title(html, base_url or "")
        content = self._clean_markdown(content)
        if title and title not in content:
            content = f"# {title}\n\n{content}" if content else f"# {title}"
        return content

    @staticmethod
    def _extract_markdown(html: str, url: str) -> str:
        """Extract clean Markdown body from HTML."""
        try:
            import trafilatura

            return (
                trafilatura.extract(
                    html,
                    url=url,
                    output_format="markdown",
                    include_links=True,
                    include_tables=True,
                )
                or ""
            )
        except Exception as exc:
            logger.debug("[HTMLParser] trafilatura extraction failed: %s", exc)
            return ""

    @staticmethod
    def _extract_title(html: str, url: str) -> str:
        """Extract page title from HTML metadata."""
        try:
            import trafilatura

            metadata = trafilatura.extract_metadata(html, default_url=url)
            if metadata and metadata.title:
                return str(metadata.title).strip()
        except Exception as exc:
            logger.debug("[HTMLParser] trafilatura title extraction failed: %s", exc)
        return ""

    @staticmethod
    def _clean_markdown(markdown: str) -> str:
        """Remove noisy artifacts from extracted Markdown."""
        markdown = markdown or ""
        markdown = re.sub(r"!\[[^\]]*\]\(data:image/[^)]+\)", "", markdown)
        markdown = re.sub(
            r"<img[^>]*src\s*=\s*['\"]data:image/[^'\"]*['\"][^>]*/?>",
            "",
            markdown,
            flags=re.IGNORECASE,
        )
        markdown = re.sub(r"<(span|a)\s+(?:id|name)=['\"][^'\"]*['\"]\s*>\s*</\1>", "", markdown)
        for pattern in _SPA_EMPTY_PATTERNS:
            markdown = markdown.replace(pattern, "")
        markdown = re.sub(r"\n{3,}", "\n\n", markdown)
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
        md_parser = self._get_markdown_parser()
        result = await md_parser.parse_content(markdown_content, source_path=source_path, **kwargs)

        # Update metadata
        result.source_format = "html"
        result.parser_name = "HTMLParser"

        return result
