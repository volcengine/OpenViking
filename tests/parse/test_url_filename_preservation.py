"""Tests for URL filename preservation when importing resources via URL.

Verifies fix for https://github.com/volcengine/OpenViking/issues/251:
- Original filename preserved (not temp file name)
- File extension preserved (.py stays .py, not converted to .md)
- URL-encoded characters decoded properly
- Code file extensions routed to download, not webpage parse
"""

import pytest

from openviking.parse.parsers.html import HTMLParser, URLType, URLTypeDetector


class TestExtractFilenameFromUrl:
    """Test HTMLParser._extract_filename_from_url."""

    def test_simple_filename(self):
        url = "https://example.com/path/to/schemas.py"
        assert HTMLParser._extract_filename_from_url(url) == "schemas.py"

    def test_url_encoded_path(self):
        url = "https://example.com/%E7%99%BE%E5%BA%A64/src/baidu_search/schemas.py"
        assert HTMLParser._extract_filename_from_url(url) == "schemas.py"

    def test_url_encoded_filename(self):
        url = "https://example.com/path/%E6%96%87%E4%BB%B6.py"
        assert HTMLParser._extract_filename_from_url(url) == "\u6587\u4ef6.py"

    def test_query_params_ignored(self):
        url = "https://example.com/file.py?version=2&token=abc"
        assert HTMLParser._extract_filename_from_url(url) == "file.py"

    def test_no_filename_fallback(self):
        url = "https://example.com/"
        assert HTMLParser._extract_filename_from_url(url) == "download"

    def test_cos_url(self):
        url = (
            "https://cos.ap-beijing.myqcloud.com/bucket/"
            "%E7%99%BE%E5%BA%A64/src/baidu_search/schemas.py"
        )
        assert HTMLParser._extract_filename_from_url(url) == "schemas.py"

    def test_markdown_extension(self):
        url = "https://example.com/docs/README.md"
        assert HTMLParser._extract_filename_from_url(url) == "README.md"

    def test_no_extension(self):
        url = "https://example.com/path/Makefile"
        assert HTMLParser._extract_filename_from_url(url) == "Makefile"


class TestURLTypeDetectorCodeExtensions:
    """Test that code file extensions are routed to DOWNLOAD_TXT, not WEBPAGE."""

    def setup_method(self):
        self.detector = URLTypeDetector()

    @pytest.mark.asyncio
    async def test_py_extension_detected(self):
        url = "https://example.com/path/schemas.py"
        url_type, meta = await self.detector.detect(url)
        assert url_type == URLType.DOWNLOAD_TXT
        assert meta["detected_by"] == "extension"

    @pytest.mark.asyncio
    async def test_js_extension_detected(self):
        url = "https://example.com/path/index.js"
        url_type, meta = await self.detector.detect(url)
        assert url_type == URLType.DOWNLOAD_TXT

    @pytest.mark.asyncio
    async def test_yaml_extension_detected(self):
        url = "https://example.com/config.yaml"
        url_type, meta = await self.detector.detect(url)
        assert url_type == URLType.DOWNLOAD_TXT

    @pytest.mark.asyncio
    async def test_json_extension_detected(self):
        url = "https://example.com/data.json"
        url_type, meta = await self.detector.detect(url)
        assert url_type == URLType.DOWNLOAD_TXT

    @pytest.mark.asyncio
    async def test_go_extension_detected(self):
        url = "https://example.com/main.go"
        url_type, meta = await self.detector.detect(url)
        assert url_type == URLType.DOWNLOAD_TXT

    @pytest.mark.asyncio
    async def test_rs_extension_detected(self):
        url = "https://example.com/lib.rs"
        url_type, meta = await self.detector.detect(url)
        assert url_type == URLType.DOWNLOAD_TXT

    @pytest.mark.asyncio
    async def test_url_encoded_py_extension(self):
        url = "https://example.com/%E7%99%BE%E5%BA%A64/src/schemas.py"
        url_type, meta = await self.detector.detect(url)
        assert url_type == URLType.DOWNLOAD_TXT

    @pytest.mark.asyncio
    async def test_md_still_routes_to_markdown(self):
        url = "https://example.com/README.md"
        url_type, meta = await self.detector.detect(url)
        assert url_type == URLType.DOWNLOAD_MD

    @pytest.mark.asyncio
    async def test_pdf_still_routes_to_pdf(self):
        url = "https://example.com/paper.pdf"
        url_type, meta = await self.detector.detect(url)
        assert url_type == URLType.DOWNLOAD_PDF

    @pytest.mark.asyncio
    async def test_html_still_routes_to_download_html(self):
        """Ensure .html overrides CODE_EXTENSIONS mapping to DOWNLOAD_TXT."""
        url = "https://example.com/page.html"
        url_type, meta = await self.detector.detect(url)
        assert url_type == URLType.DOWNLOAD_HTML
