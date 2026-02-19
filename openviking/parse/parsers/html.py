# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: Apache-2.0
"""
HTML and URL parser for OpenViking.

Unified parser that handles:
- Local HTML files
- Web pages (URL -> fetch -> parse)
- Download links (URL -> download -> delegate to appropriate parser)

Preserves natural document hierarchy and filters out navigation/ads.
"""

import hashlib
import re
import tempfile
import time
from enum import Enum
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple, Union
from urllib.parse import urlparse

from openviking.parse.base import (
    NodeType,
    ParseResult,
    ResourceNode,
    create_parse_result,
    lazy_import,
)
from openviking.parse.parsers.base_parser import BaseParser


class URLType(Enum):
    """URL content types."""

    WEBPAGE = "webpage"  # HTML webpage to parse
    DOWNLOAD_PDF = "download_pdf"  # PDF file download link
    DOWNLOAD_MD = "download_md"  # Markdown file download link
    DOWNLOAD_TXT = "download_txt"  # Text file download link
    DOWNLOAD_HTML = "download_html"  # HTML file download link
    CODE_REPOSITORY = "code_repository"  # Code repository (GitHub, GitLab, etc.)
    UNKNOWN = "unknown"  # Unknown or unsupported type


class URLTypeDetector:
    """
    Detector for URL content types.

    Uses extension and HTTP HEAD request to determine if a URL is:
    - A webpage to scrape
    - A file download link (and what type)
    """

    # Extension to URL type mapping
    EXTENSION_MAP = {
        ".pdf": URLType.DOWNLOAD_PDF,
        ".md": URLType.DOWNLOAD_MD,
        ".markdown": URLType.DOWNLOAD_MD,
        ".txt": URLType.DOWNLOAD_TXT,
        ".text": URLType.DOWNLOAD_TXT,
        ".html": URLType.DOWNLOAD_HTML,
        ".htm": URLType.DOWNLOAD_HTML,
        ".git": URLType.CODE_REPOSITORY,
    }

    # Content-Type to URL type mapping
    CONTENT_TYPE_MAP = {
        "application/pdf": URLType.DOWNLOAD_PDF,
        "text/markdown": URLType.DOWNLOAD_MD,
        "text/plain": URLType.DOWNLOAD_TXT,
        "text/html": URLType.WEBPAGE,
        "application/xhtml+xml": URLType.WEBPAGE,
    }

    async def detect(self, url: str, timeout: float = 10.0) -> Tuple[URLType, Dict[str, Any]]:
        """
        Detect URL content type.

        Args:
            url: URL to detect
            timeout: HTTP request timeout

        Returns:
            (URLType, metadata dict)
        """
        meta = {"url": url, "detected_by": "unknown"}
        parsed = urlparse(url)
        path_lower = parsed.path.lower()

        # 0. Check for code repository URLs first
        if self._is_code_repository_url(url):
            meta["detected_by"] = "code_repository_pattern"
            return URLType.CODE_REPOSITORY, meta

        # 1. Check extension first
        for ext, url_type in self.EXTENSION_MAP.items():
            if path_lower.endswith(ext):
                meta["detected_by"] = "extension"
                meta["extension"] = ext
                return url_type, meta

        # 2. Send HEAD request to check Content-Type
        try:
            httpx = lazy_import("httpx")
            async with httpx.AsyncClient(timeout=timeout, follow_redirects=True) as client:
                response = await client.head(url)
                content_type = response.headers.get("content-type", "").lower()

                # Remove charset info
                if ";" in content_type:
                    content_type = content_type.split(";")[0].strip()

                meta["content_type"] = content_type
                meta["detected_by"] = "content_type"
                meta["status_code"] = response.status_code

                # Map content type
                for ct_prefix, url_type in self.CONTENT_TYPE_MAP.items():
                    if content_type.startswith(ct_prefix):
                        return url_type, meta

                # Default to webpage for HTML-like content
                if "html" in content_type or "xml" in content_type:
                    return URLType.WEBPAGE, meta

        except Exception as e:
            meta["detection_error"] = str(e)

        # 3. Default: assume webpage
        return URLType.WEBPAGE, meta

    def _is_code_repository_url(self, url: str) -> bool:
        """
        Check if URL is a code repository URL.

        Args:
            url: URL to check

        Returns:
            True if URL matches code repository patterns
        """
        import re

        # Repository URL patterns (from README)
        repo_patterns = [
            r"^https?://github\.com/[^/]+/[^/]+/?$",
            r"^https?://gitlab\.com/[^/]+/[^/]+/?$",
            r"^.*\.git$",
            r"^git@",
        ]

        # Check for URL patterns
        for pattern in repo_patterns:
            if re.match(pattern, url):
                return True

        return False


class HTMLParser(BaseParser):
    """
    Unified parser for HTML files and URLs.

    Features:
    - Parse local HTML files
    - Fetch and parse web pages
    - Detect and handle download links
    - Build hierarchy based on heading tags (h1-h6)
    - Filter out navigation, ads, and boilerplate
    - Extract tables and preserve structure
    """

    DEFAULT_USER_AGENT = (
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/120.0.0.0 Safari/537.36"
    )

    def __init__(
        self,
        timeout: float = 30.0,
        user_agent: Optional[str] = None,
    ):
        """Initialize HTML parser."""
        self.timeout = timeout
        self.user_agent = user_agent or self.DEFAULT_USER_AGENT
        self._url_detector = URLTypeDetector()

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
        Unified parse method for HTML files and URLs.

        Args:
            source: HTML file path or URL
            instruction: Processing instruction, guides LLM how to understand the resource
            **kwargs: Additional options

        Returns:
            ParseResult with document tree
        """
        start_time = time.time()
        source_str = str(source)

        # Detect if source is a URL
        if source_str.startswith(("http://", "https://")):
            return await self._parse_url(source_str, start_time, **kwargs)
        else:
            return await self._parse_local_file(Path(source), start_time, **kwargs)

    async def _parse_url(self, url: str, start_time: float, **kwargs) -> ParseResult:
        """
        Parse URL (webpage or download link).

        Args:
            url: URL to parse
            start_time: Parse start timestamp

        Returns:
            ParseResult
        """
        # Detect URL type
        url_type, meta = await self._url_detector.detect(url, timeout=self.timeout)

        if url_type == URLType.WEBPAGE:
            # Fetch and parse as webpage
            return await self._parse_webpage(url, start_time, meta, **kwargs)

        elif url_type == URLType.DOWNLOAD_PDF:
            # Download and delegate to PDF parser
            return await self._handle_download_link(url, "pdf", start_time, meta, **kwargs)

        elif url_type == URLType.DOWNLOAD_MD:
            # Download and delegate to Markdown parser
            return await self._handle_download_link(url, "markdown", start_time, meta, **kwargs)

        elif url_type == URLType.DOWNLOAD_TXT:
            # Download and delegate to Text parser
            return await self._handle_download_link(url, "text", start_time, meta, **kwargs)

        elif url_type == URLType.DOWNLOAD_HTML:
            # Download HTML file and parse
            return await self._handle_download_link(url, "html", start_time, meta, **kwargs)

        elif url_type == URLType.CODE_REPOSITORY:
            # Delegate to CodeRepositoryParser
            return await self._handle_code_repository(url, start_time, meta, **kwargs)

        else:
            # Unknown type - try as webpage
            return await self._parse_webpage(url, start_time, meta, **kwargs)

    async def _parse_webpage(
        self, url: str, start_time: float, meta: Dict[str, Any], **kwargs
    ) -> ParseResult:
        """
        Fetch and parse a webpage.

        Args:
            url: URL to fetch
            start_time: Parse start time
            meta: Detection metadata

        Returns:
            ParseResult
        """
        try:
            # Fetch HTML
            html_content = await self._fetch_html(url)

            # Convert to Markdown
            markdown_content = self._html_to_markdown(html_content, base_url=url)

            # Parse using MarkdownParser
            from openviking.parse.parsers.markdown import MarkdownParser

            md_parser = MarkdownParser()
            result = await md_parser.parse_content(markdown_content, source_path=url, **kwargs)

            # Update metadata
            result.source_format = "html"
            result.parser_name = "HTMLParser"
            result.parse_time = time.time() - start_time
            result.parse_timestamp = None  # Will be set by __post_init__
            result.meta.update(meta)
            result.meta["url_type"] = "webpage"
            result.meta["intermediate_markdown"] = markdown_content[:500]  # Preview

            return result

        except Exception as e:
            return create_parse_result(
                root=ResourceNode(type=NodeType.ROOT, content_path=None),
                source_path=url,
                source_format="html",
                parser_name="HTMLParser",
                parse_time=time.time() - start_time,
                warnings=[f"Failed to fetch webpage: {e}"],
            )

    async def _handle_download_link(
        self, url: str, file_type: str, start_time: float, meta: Dict[str, Any], **kwargs
    ) -> ParseResult:
        """
        Download file and delegate to appropriate parser.

        Args:
            url: URL to download
            file_type: File type ("pdf", "markdown", "text", "html")
            start_time: Parse start time
            meta: Detection metadata

        Returns:
            ParseResult from delegated parser
        """
        temp_path = None
        try:
            # Download to temporary file
            temp_path = await self._download_file(url)

            # Get appropriate parser
            if file_type == "pdf":
                from openviking.parse.parsers.pdf import PDFParser

                parser = PDFParser()
                result = await parser.parse(temp_path)
            elif file_type == "markdown":
                from openviking.parse.parsers.markdown import MarkdownParser

                parser = MarkdownParser()
                result = await parser.parse(temp_path)
            elif file_type == "text":
                from openviking.parse.parsers.text import TextParser

                parser = TextParser()
                result = await parser.parse(temp_path)
            elif file_type == "html":
                # Parse downloaded HTML locally
                return await self._parse_local_file(Path(temp_path), start_time, **kwargs)
            else:
                raise ValueError(f"Unsupported file type: {file_type}")

            result.meta.update(meta)
            result.meta["downloaded_from"] = url
            result.meta["url_type"] = f"download_{file_type}"
            return result

        except Exception as e:
            return create_parse_result(
                root=ResourceNode(type=NodeType.ROOT, content_path=None),
                source_path=url,
                source_format=file_type,
                parser_name="HTMLParser",
                parse_time=time.time() - start_time,
                warnings=[f"Failed to download/parse link: {e}"],
            )
        finally:
            if temp_path:
                try:
                    p = Path(temp_path)
                    if p.exists():
                        p.unlink()
                except Exception:
                    pass

    async def _handle_code_repository(
        self, url: str, start_time: float, meta: Dict[str, Any], **kwargs
    ) -> ParseResult:
        """
        Handle code repository URL by delegating to CodeRepositoryParser.
        """
        try:
            from openviking.parse.parsers.code import CodeRepositoryParser

            parser = CodeRepositoryParser()
            result = await parser.parse(url, **kwargs)
            result.meta.update(meta)
            result.meta["downloaded_from"] = url
            result.meta["url_type"] = "code_repository"

            return result

        except Exception as e:
            return create_parse_result(
                root=ResourceNode(type=NodeType.ROOT, content_path=None),
                source_path=url,
                source_format="code_repository",
                parser_name="HTMLParser",
                parse_time=time.time() - start_time,
                warnings=[f"Failed to parse code repository: {e}"],
            )

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
            result.parser_version = "2.0"

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

    async def _fetch_html(self, url: str) -> str:
        """
        Fetch HTML content from URL.

        Args:
            url: URL to fetch

        Returns:
            HTML content string

        Raises:
            Exception: If fetch fails
        """
        httpx = lazy_import("httpx")

        async with httpx.AsyncClient(timeout=self.timeout, follow_redirects=True) as client:
            headers = {"User-Agent": self.user_agent}
            response = await client.get(url, headers=headers)
            response.raise_for_status()
            return response.text

    def _convert_to_raw_url(self, url: str) -> str:
        """Convert GitHub/GitLab blob URL to raw URL."""
        parsed = urlparse(url)

        if parsed.netloc == "github.com":
            path_parts = parsed.path.strip("/").split("/")
            if len(path_parts) >= 4 and path_parts[2] == "blob":
                # Remove 'blob'
                new_path = "/".join(path_parts[:2] + path_parts[3:])
                return f"https://raw.githubusercontent.com/{new_path}"

        if parsed.netloc == "gitlab.com" and "/blob/" in parsed.path:
            return url.replace("/blob/", "/raw/")

        return url

    async def _download_file(self, url: str) -> str:
        """
        Download file from URL to temporary location.

        Args:
            url: URL to download

        Returns:
            Path to downloaded temporary file

        Raises:
            Exception: If download fails
        """
        httpx = lazy_import("httpx")

        url = self._convert_to_raw_url(url)

        # Determine file extension from URL
        parsed = urlparse(url)
        ext = Path(parsed.path).suffix or ".tmp"

        # Create temp file
        temp_file = tempfile.NamedTemporaryFile(delete=False, suffix=ext)
        temp_path = temp_file.name
        temp_file.close()

        # Download
        async with httpx.AsyncClient(timeout=self.timeout, follow_redirects=True) as client:
            headers = {"User-Agent": self.user_agent}
            response = await client.get(url, headers=headers)
            response.raise_for_status()

            # Write to temp file
            Path(temp_path).write_bytes(response.content)

        return temp_path

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

        Converts HTML to Markdown and delegates to MarkdownParser (three-phase architecture).

        Args:
            content: HTML content string
            source_path: Optional source path for reference

        Returns:
            ParseResult with document tree
        """
        # Convert HTML to Markdown
        markdown_content = self._html_to_markdown(content, base_url=source_path or "")

        # Delegate to MarkdownParser (using three-phase architecture)
        from openviking.parse.parsers.markdown import MarkdownParser

        md_parser = MarkdownParser()
        result = await md_parser.parse_content(markdown_content, source_path=source_path, **kwargs)

        # Update metadata
        result.source_format = "html"
        result.parser_name = "HTMLParser"

        return result

    def _sanitize_for_path(self, text: str, max_length: int = 50) -> str:
        """Sanitize text for use in file path, hash & shorten if too long."""
        safe = re.sub(
            r"[^\w\u4e00-\u9fff\u3040-\u309f\u30a0-\u30ff\uac00-\ud7af\u3400-\u4dbf\U00020000-\U0002a6df\s-]",
            "",
            text,
        )
        safe = re.sub(r"\s+", "_", safe)
        safe = safe.strip("_")
        if not safe:
            return "section"
        if len(safe) > max_length:
            hash_suffix = hashlib.sha256(text.encode()).hexdigest()[:8]
            return f"{safe[: max_length - 9]}_{hash_suffix}"
        return safe
