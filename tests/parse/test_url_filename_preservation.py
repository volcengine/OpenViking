"""Tests for URL filename preservation when importing resources via URL.

Verifies fix for https://github.com/volcengine/OpenViking/issues/251:
- Original filename preserved (not temp file name)
- File extension preserved (.py stays .py, not converted to .md)
- URL-encoded characters decoded properly
- Code file extensions routed to download, not webpage parse
"""

import pytest

from openviking.parse.accessors.http_accessor import HTTPAccessor, URLType, URLTypeDetector


class TestExtractFilenameFromUrl:
    """Test HTTPAccessor._extract_filename_from_url."""

    def test_simple_filename(self):
        url = "https://example.com/path/to/schemas.py"
        assert HTTPAccessor._extract_filename_from_url(url) == "schemas.py"

    def test_url_encoded_path(self):
        url = "https://example.com/%E7%99%BE%E5%BA%A64/src/baidu_search/schemas.py"
        assert HTTPAccessor._extract_filename_from_url(url) == "schemas.py"

    def test_url_encoded_filename(self):
        url = "https://example.com/path/%E6%96%87%E4%BB%B6.py"
        assert HTTPAccessor._extract_filename_from_url(url) == "\u6587\u4ef6.py"

    def test_query_params_ignored(self):
        url = "https://example.com/file.py?version=2&token=abc"
        assert HTTPAccessor._extract_filename_from_url(url) == "file.py"

    def test_no_filename_fallback(self):
        url = "https://example.com/"
        assert HTTPAccessor._extract_filename_from_url(url) == "download"

    def test_cos_url(self):
        url = (
            "https://cos.ap-beijing.myqcloud.com/bucket/"
            "%E7%99%BE%E5%BA%A64/src/baidu_search/schemas.py"
        )
        assert HTTPAccessor._extract_filename_from_url(url) == "schemas.py"

    def test_markdown_extension(self):
        url = "https://example.com/docs/README.md"
        assert HTTPAccessor._extract_filename_from_url(url) == "README.md"

    def test_no_extension(self):
        url = "https://example.com/path/Makefile"
        assert HTTPAccessor._extract_filename_from_url(url) == "Makefile"


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

    @pytest.mark.asyncio
    async def test_signed_png_extension_detected_without_head(self):
        url = (
            "https://example.com/assets/image.png?"
            "OSSAccessKeyId=key&Expires=1779533415&Signature=abc%3D"
        )
        url_type, meta = await self.detector.detect(url)
        assert url_type == URLType.DOWNLOAD_IMAGE
        assert meta["detected_by"] == "extension"

    @pytest.mark.asyncio
    async def test_signed_docx_extension_detected_without_head(self):
        url = (
            "https://example.com/docs/report.docx?"
            "OSSAccessKeyId=key&Expires=1779533415&Signature=abc%3D"
        )
        url_type, meta = await self.detector.detect(url)
        assert url_type == URLType.DOWNLOAD_DOCUMENT
        assert meta["detected_by"] == "extension"

    @pytest.mark.asyncio
    async def test_signed_doc_extension_detected_without_head(self):
        url = (
            "https://example.com/docs/report.doc?"
            "OSSAccessKeyId=key&Expires=1779533415&Signature=abc%3D"
        )
        url_type, meta = await self.detector.detect(url)
        assert url_type == URLType.DOWNLOAD_DOCUMENT
        assert meta["detected_by"] == "extension"


class TestHTTPAccessorGetFallback:
    """Test GET header/content fallback for signed URLs that reject HEAD."""

    @pytest.mark.asyncio
    async def test_get_content_disposition_refines_extensionless_docx_url(self, monkeypatch):
        _patch_httpx_client(
            monkeypatch,
            headers={
                "content-disposition": 'attachment; filename="report.docx"',
                "content-type": "application/octet-stream",
            },
            content=_zip_bytes({"word/document.xml": b"<w:document />"}),
        )

        accessor = HTTPAccessor()
        temp_path, url_type, meta = await accessor._download_url(
            "https://example.com/download?id=1"
        )

        assert url_type == URLType.DOWNLOAD_DOCUMENT
        assert temp_path.endswith(".docx")
        assert meta["original_filename"] == "report.docx"
        assert meta["detected_by"] == "get_content_disposition"
        assert meta["refined_by_get_headers"] is True

    @pytest.mark.asyncio
    async def test_get_content_type_refines_extensionless_png_url(self, monkeypatch):
        _patch_httpx_client(
            monkeypatch,
            headers={"content-type": "image/png"},
            content=b"\x89PNG\r\n\x1a\nimage",
        )

        accessor = HTTPAccessor()
        temp_path, url_type, meta = await accessor._download_url(
            "https://example.com/download?id=2"
        )

        assert url_type == URLType.DOWNLOAD_IMAGE
        assert temp_path.endswith(".png")
        assert meta["original_filename"] == "download.png"
        assert meta["detected_by"] == "get_media_type_pattern"
        assert meta["refined_by_get_headers"] is True

    @pytest.mark.asyncio
    async def test_magic_bytes_refines_generic_extensionless_legacy_doc_url(self, monkeypatch):
        _patch_httpx_client(
            monkeypatch,
            headers={"content-type": "application/octet-stream"},
            content=b"\xd0\xcf\x11\xe0\xa1\xb1\x1a\xe1legacy-doc",
        )

        accessor = HTTPAccessor()
        temp_path, url_type, meta = await accessor._download_url(
            "https://example.com/download?id=3"
        )

        assert url_type == URLType.DOWNLOAD_DOCUMENT
        assert temp_path.endswith(".doc")
        assert meta["original_filename"] == "download.doc"
        assert meta["detected_by"] == "magic_bytes"
        assert meta["refined_by_magic_bytes"] is True

    @pytest.mark.asyncio
    async def test_magic_bytes_distinguishes_extensionless_xlsx_url(self, monkeypatch):
        _patch_httpx_client(
            monkeypatch,
            headers={"content-type": "application/octet-stream"},
            content=_zip_bytes({"xl/workbook.xml": b"<workbook />"}),
        )

        accessor = HTTPAccessor()
        temp_path, url_type, meta = await accessor._download_url(
            "https://example.com/download?id=4"
        )

        assert url_type == URLType.DOWNLOAD_DOCUMENT
        assert temp_path.endswith(".xlsx")
        assert meta["original_filename"] == "download.xlsx"

    @pytest.mark.asyncio
    async def test_magic_bytes_distinguishes_extensionless_zip_url(self, monkeypatch):
        _patch_httpx_client(
            monkeypatch,
            headers={"content-type": "application/octet-stream"},
            content=_zip_bytes({"files/readme.txt": b"hello"}),
        )

        accessor = HTTPAccessor()
        temp_path, url_type, meta = await accessor._download_url(
            "https://example.com/download?id=5"
        )

        assert url_type == URLType.DOWNLOAD_DOCUMENT
        assert temp_path.endswith(".zip")
        assert meta["original_filename"] == "download.zip"

    @pytest.mark.asyncio
    async def test_non_2xx_head_headers_do_not_pollute_get_detection(self, monkeypatch):
        _patch_httpx_client(
            monkeypatch,
            headers={"content-type": "image/png"},
            content=b"\x89PNG\r\n\x1a\nimage",
            head_headers={"content-type": "application/xml"},
            head_status_code=403,
            fail_head=False,
        )

        accessor = HTTPAccessor()
        temp_path, url_type, meta = await accessor._download_url(
            "https://example.com/download?id=head-403"
        )

        assert url_type == URLType.DOWNLOAD_IMAGE
        assert temp_path.endswith(".png")
        assert meta["head_status_skipped"] == 403
        assert meta["detected_by"] == "get_media_type_pattern"
        assert meta["content_type_raw"] != "application/xml"

    @pytest.mark.asyncio
    @pytest.mark.parametrize(
        "content, expected_url_type, expected_ext",
        [
            (b"BM" + b"\x00" * 14, URLType.DOWNLOAD_IMAGE, ".bmp"),
            (b"RIFF" + b"\x00" * 4 + b"WEBP" + b"data", URLType.DOWNLOAD_IMAGE, ".webp"),
            (b"RIFF" + b"\x00" * 4 + b"WAVE" + b"data", URLType.DOWNLOAD_AUDIO, ".wav"),
            (b"RIFF" + b"\x00" * 4 + b"AVI " + b"data", URLType.DOWNLOAD_VIDEO, ".avi"),
            (b"ID3" + b"\x00" * 13, URLType.DOWNLOAD_AUDIO, ".mp3"),
            (b"\x00\x00\x00\x18ftypisom" + b"\x00" * 4, URLType.DOWNLOAD_VIDEO, ".mp4"),
            (b"\x00\x00\x00\x14ftypqt  " + b"\x00" * 4, URLType.DOWNLOAD_VIDEO, ".mov"),
            (b'<?xml version="1.0"?><svg></svg>', URLType.DOWNLOAD_IMAGE, ".svg"),
        ],
    )
    async def test_magic_bytes_detect_common_media_types(
        self, monkeypatch, content, expected_url_type, expected_ext
    ):
        _patch_httpx_client(
            monkeypatch,
            headers={"content-type": "application/octet-stream"},
            content=content,
        )

        accessor = HTTPAccessor()
        temp_path, url_type, meta = await accessor._download_url(
            "https://example.com/download?id=media"
        )

        assert url_type == expected_url_type
        assert temp_path.endswith(expected_ext)
        assert meta["original_filename"] == f"download{expected_ext}"
        assert meta["detected_by"] == "magic_bytes"

    @pytest.mark.asyncio
    async def test_magic_bytes_recognizes_gzip_without_routing_to_document(self, monkeypatch):
        _patch_httpx_client(
            monkeypatch,
            headers={"content-type": "application/octet-stream"},
            content=b"\x1f\x8b\x08\x00gzip",
        )

        accessor = HTTPAccessor()
        temp_path, url_type, meta = await accessor._download_url(
            "https://example.com/download?id=gzip"
        )

        assert url_type == URLType.WEBPAGE
        assert temp_path.endswith(".html")
        assert meta["resolved_url_type"] == URLType.WEBPAGE
        assert meta["detected_by"] == "default"

    def test_zip_detection_falls_back_to_zip_on_parse_error(self):
        assert HTTPAccessor._detect_zip_based_extension(b"PK\x03\x04not-a-valid-zip") == ".zip"

    def test_document_default_extension_is_zip(self):
        assert URLTypeDetector().get_extension_for_type(URLType.DOWNLOAD_DOCUMENT) == ".zip"


def _patch_httpx_client(
    monkeypatch,
    headers,
    content: bytes,
    *,
    head_headers=None,
    head_status_code: int = 200,
    fail_head: bool = True,
) -> None:
    """Patch httpx.AsyncClient used by HTTPAccessor for HEAD and GET."""

    class FakeResponse:
        def __init__(self, response_headers, response_content, status_code: int = 200):
            self.status_code = status_code
            self.headers = response_headers
            self.content = response_content

        def raise_for_status(self):
            return None

    response_headers = headers
    response_head_headers = head_headers if head_headers is not None else headers

    class FakeAsyncClient:
        def __init__(self, **_kwargs):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *_args):
            return False

        async def head(self, _url):
            if fail_head:
                raise RuntimeError("SignatureDoesNotMatch")
            return FakeResponse(response_head_headers, b"", head_status_code)

        async def get(self, _url, headers=None):
            return FakeResponse(response_headers, content)

    import httpx

    monkeypatch.setattr(httpx, "AsyncClient", FakeAsyncClient)


def _zip_bytes(files):
    from io import BytesIO
    from zipfile import ZipFile

    buffer = BytesIO()
    with ZipFile(buffer, "w") as zf:
        for name, content in files.items():
            zf.writestr(name, content)
    return buffer.getvalue()
