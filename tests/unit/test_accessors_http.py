# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0
"""Unit tests for HTTPAccessor."""

from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from openviking.parse.accessors import AccessorRegistry, GitAccessor, HTTPAccessor
from openviking.parse.accessors.http_accessor import URLType, URLTypeDetector


def _mock_config():
    return SimpleNamespace(
        code=SimpleNamespace(
            github_domains=["github.com", "www.github.com"],
            gitlab_domains=["gitlab.com", "www.gitlab.com"],
            azure_devops_domains=[
                "dev.azure.com",
                "ssh.dev.azure.com",
                "vs-ssh.visualstudio.com",
            ],
            code_hosting_domains=["github.com", "gitlab.com"],
        )
    )


class TestHTTPAccessor:
    """Tests for HTTPAccessor."""

    @pytest.fixture
    def accessor(self) -> HTTPAccessor:
        """Create a HTTPAccessor instance."""
        return HTTPAccessor()

    def test_priority(self, accessor: HTTPAccessor) -> None:
        """HTTPAccessor should have correct priority."""
        assert accessor.priority == 50

    @pytest.mark.parametrize(
        "source",
        [
            "https://example.com/page.html",
            "http://example.com/document.pdf",
            "https://example.org/file.md",
        ],
    )
    def test_can_handle_http_urls(self, accessor: HTTPAccessor, source: str) -> None:
        """HTTPAccessor should handle regular HTTP/HTTPS URLs."""
        assert accessor.can_handle(source) is True

    @pytest.mark.parametrize(
        "source",
        [
            "/path/to/file.html",
            "git@github.com:org/repo.git",
            "plain text content",
        ],
    )
    def test_cannot_handle_non_http(self, accessor: HTTPAccessor, source: str) -> None:
        """HTTPAccessor should NOT handle non-HTTP sources."""
        assert accessor.can_handle(source) is False

    @pytest.mark.parametrize(
        "url, expected",
        [
            ("https://example.com/path/file.html", "file.html"),
            ("https://example.com/path/doc.pdf", "doc.pdf"),
            ("https://example.com/path/", "path"),
            ("https://example.com", "download"),
        ],
    )
    def test_extract_filename_from_url(self, url: str, expected: str) -> None:
        """Test filename extraction from URLs."""
        assert HTTPAccessor._extract_filename_from_url(url) == expected


class TestHTTPAccessorPriorityRouting:
    """Tests that verify HTTPAccessor works correctly with priority-based routing."""

    @pytest.fixture(autouse=True)
    def _patch_config(self):
        with patch(
            "openviking_cli.utils.config.open_viking_config.OpenVikingConfigSingleton.get_instance",
            side_effect=_mock_config,
        ):
            yield

    def test_git_url_routed_to_git_accessor(self) -> None:
        """Git URLs should be routed to GitAccessor, not HTTPAccessor."""
        registry = AccessorRegistry(register_default=False)
        http = HTTPAccessor()
        git = GitAccessor()
        registry.register(http)
        registry.register(git)

        test_url = "https://github.com/volcengine/OpenViking"

        # Both can handle the URL individually (this is OK!)
        assert git.can_handle(test_url) is True
        assert http.can_handle(test_url) is True

        # But registry picks the higher priority one (GitAccessor)
        accessor = registry.get_accessor(test_url)
        assert accessor is not None
        assert accessor.__class__.__name__ == "GitAccessor"

    def test_azure_devops_git_url_routed_to_git_accessor(self) -> None:
        """Azure DevOps repo URLs should be routed to GitAccessor."""
        registry = AccessorRegistry(register_default=False)
        http = HTTPAccessor()
        git = GitAccessor()
        registry.register(http)
        registry.register(git)

        test_url = "https://dev.azure.com/org/project/_git/repo"

        assert git.can_handle(test_url) is True
        assert http.can_handle(test_url) is True

        accessor = registry.get_accessor(test_url)
        assert accessor is not None
        assert accessor.__class__.__name__ == "GitAccessor"

    def test_regular_http_url_routed_to_http_accessor(self) -> None:
        """Regular HTTP URLs should be routed to HTTPAccessor."""
        registry = AccessorRegistry(register_default=False)
        http = HTTPAccessor()
        git = GitAccessor()
        registry.register(http)
        registry.register(git)

        test_url = "https://example.com/page.html"

        # Only HTTPAccessor can handle this
        assert git.can_handle(test_url) is False
        assert http.can_handle(test_url) is True

        # Registry picks HTTPAccessor
        accessor = registry.get_accessor(test_url)
        assert accessor is not None
        assert accessor.__class__.__name__ == "HTTPAccessor"

    def test_azure_devops_browse_url_routed_to_http_accessor(self) -> None:
        """Azure DevOps browse URLs should stay with HTTPAccessor."""
        registry = AccessorRegistry(register_default=False)
        http = HTTPAccessor()
        git = GitAccessor()
        registry.register(http)
        registry.register(git)

        test_url = "https://dev.azure.com/org/project/_git/repo?path=/README.md"

        assert git.can_handle(test_url) is False
        assert http.can_handle(test_url) is True

        accessor = registry.get_accessor(test_url)
        assert accessor is not None
        assert accessor.__class__.__name__ == "HTTPAccessor"

    def test_accessor_priority_order(self) -> None:
        """Accessors should be registered in descending priority order."""
        registry = AccessorRegistry(register_default=False)
        http = HTTPAccessor()
        git = GitAccessor()

        # Register in any order
        registry.register(http)
        registry.register(git)

        accessors = registry.list_accessors()

        # GitAccessor (priority 80) should come before HTTPAccessor (priority 50)
        assert len(accessors) == 2
        assert accessors[0].__class__.__name__ == "GitAccessor"
        assert accessors[1].__class__.__name__ == "HTTPAccessor"


def _mock_async_httpx(head_status: int, head_headers: dict) -> MagicMock:
    """Build a context-manager AsyncClient mock that returns the given HEAD response."""
    response = MagicMock()
    response.status_code = head_status
    response.headers = head_headers

    client = MagicMock()
    client.head = AsyncMock(return_value=response)

    async_cm = MagicMock()
    async_cm.__aenter__ = AsyncMock(return_value=client)
    async_cm.__aexit__ = AsyncMock(return_value=None)

    httpx_module = MagicMock()
    httpx_module.AsyncClient = MagicMock(return_value=async_cm)
    return httpx_module


class TestURLTypeDetectorBinaryURLs:
    """Tests covering non-text binary URLs (image/audio/video) and signed-URL edge cases.

    Regression: an Aliyun OSS GET-signed PNG URL was being treated as text. The
    URL ended with `.png?OSSAccessKeyId=...&Signature=...`, HEAD failed 403 with
    `Content-Type: application/xml` (OSS error doc), and the detector defaulted
    to WEBPAGE/.html so the binary PNG was chunked by the text parser.
    """

    @pytest.mark.parametrize(
        "ext",
        [".png", ".jpg", ".jpeg", ".gif", ".webp", ".svg", ".mp3", ".mp4", ".mov"],
    )
    def test_extension_map_routes_media_to_binary(self, ext: str) -> None:
        assert URLTypeDetector.EXTENSION_MAP[ext] == URLType.DOWNLOAD_BINARY

    def test_url_type_to_ext_has_binary_fallback(self) -> None:
        assert URLTypeDetector.URL_TYPE_TO_EXT[URLType.DOWNLOAD_BINARY] == ".bin"

    @pytest.mark.parametrize(
        "content_type",
        ["image/png", "image/jpeg", "audio/mpeg", "video/mp4", "application/octet-stream"],
    )
    def test_media_type_map_routes_binary_content_types(self, content_type: str) -> None:
        detector = URLTypeDetector()
        meta: dict = {}
        assert detector._detect_from_media_type(content_type, meta) == URLType.DOWNLOAD_BINARY

    def test_determine_file_extension_preserves_png_for_signed_url(self) -> None:
        """Signed OSS URL ending with `.png?Signature=...` must keep its `.png` extension."""
        from openviking.parse.accessors.http_accessor import HTTPAccessor

        accessor = HTTPAccessor()
        url = (
            "http://yunweitool.oss-cn-shenzhen.aliyuncs.com/yunwei/abc.png"
            "?OSSAccessKeyId=LTAI&Expires=1779533415&Signature=R0kmK%3D"
        )
        # Even if upstream wrongly classified as WEBPAGE, the URL path extension
        # is now a recognised media extension, so it should win.
        ext = accessor._determine_file_extension(url, URLType.WEBPAGE, detect_meta={})
        assert ext == ".png"

    @pytest.mark.anyio
    async def test_detect_extension_fast_path_skips_head(self) -> None:
        """URLs whose path ends in a known media extension must not require HEAD."""
        detector = URLTypeDetector()
        # No httpx patch — if it tries HEAD, the test fails (lazy_import will succeed,
        # but the call wouldn't be intercepted, and any network/extra branch is unwanted).
        # We force `lazy_import` to raise to assert HEAD path isn't taken.
        with patch(
            "openviking.parse.accessors.http_accessor.lazy_import",
            side_effect=AssertionError("HEAD should not be attempted"),
        ):
            url_type, meta = await detector.detect(
                "http://example.com/yunwei/abc.png?Signature=R0kmK"
            )
        assert url_type == URLType.DOWNLOAD_BINARY
        assert meta["detected_by"] == "extension"
        assert meta["extension"] == ".png"

    @pytest.mark.anyio
    async def test_detect_ignores_non_2xx_head_headers(self) -> None:
        """When HEAD returns 4xx/5xx, its headers describe the error response, not the
        real file — they must not be trusted for type detection."""
        detector = URLTypeDetector()
        # OSS-style 403 with error-doc Content-Type
        httpx_mock = _mock_async_httpx(
            head_status=403,
            head_headers={"content-type": "application/xml"},
        )
        with patch(
            "openviking.parse.accessors.http_accessor.lazy_import",
            return_value=httpx_mock,
        ):
            # Use a URL with no extension so step 1 doesn't short-circuit
            url_type, meta = await detector.detect("http://example.com/opaque-id")

        # Detector must NOT trust the 403's application/xml content-type
        assert meta.get("head_status_skipped") == 403
        assert "content_type_raw" not in meta
        assert url_type == URLType.WEBPAGE  # default fallback when no signal is trustworthy

    @pytest.mark.anyio
    async def test_detect_image_content_type_routes_to_binary(self) -> None:
        """A 2xx HEAD with `Content-Type: image/png` must route to DOWNLOAD_BINARY."""
        detector = URLTypeDetector()
        httpx_mock = _mock_async_httpx(
            head_status=200,
            head_headers={"content-type": "image/png"},
        )
        with patch(
            "openviking.parse.accessors.http_accessor.lazy_import",
            return_value=httpx_mock,
        ):
            url_type, meta = await detector.detect("http://example.com/opaque-image-id")

        assert url_type == URLType.DOWNLOAD_BINARY
        assert meta["content_type_raw"] == "image/png"


class TestHTTPAccessorActualTypeReconciliation:
    """Tests for post-GET auto-detection: even when HEAD lies and the URL path
    has no usable extension, the response body's magic bytes / Content-Type are
    the source of truth."""

    @pytest.mark.parametrize(
        "head, ext",
        [
            (b"\x89PNG\r\n\x1a\n" + b"\x00" * 16, ".png"),
            (b"\xff\xd8\xff\xe0" + b"\x00" * 12, ".jpg"),
            (b"GIF89a" + b"\x00" * 16, ".gif"),
            (b"%PDF-1.4\n" + b"\x00" * 16, ".pdf"),
            (b"PK\x03\x04" + b"\x00" * 16, ".zip"),
            (b"\x1f\x8b\x08\x00" + b"\x00" * 16, ".gz"),
            (b"RIFF\x00\x00\x00\x00WEBP" + b"\x00" * 8, ".webp"),
            (b"RIFF\x00\x00\x00\x00WAVE" + b"\x00" * 8, ".wav"),
            (b"\x00\x00\x00\x20ftypmp42" + b"\x00" * 8, ".mp4"),
        ],
    )
    def test_sniff_magic_extension(self, head: bytes, ext: str) -> None:
        assert HTTPAccessor._sniff_magic_extension(head) == ext

    def test_sniff_magic_extension_returns_none_for_html(self) -> None:
        assert HTTPAccessor._sniff_magic_extension(b"<!DOCTYPE html>\n<html>") is None

    def test_reconcile_renames_html_temp_to_png_when_bytes_are_png(self, tmp_path) -> None:
        """The signature regression: temp file was created as .html (because HEAD
        returned an OSS error doc), but the GET body is PNG. Reconcile must rename
        to .png and switch url_type to DOWNLOAD_BINARY."""
        # Create a fake temp file with .html suffix
        png_bytes = b"\x89PNG\r\n\x1a\n" + b"\x00" * 32
        wrong_temp = tmp_path / "fake.html"
        wrong_temp.write_bytes(png_bytes)

        meta: dict = {"extension": ".html"}
        new_path, new_url_type, new_ext = HTTPAccessor._reconcile_actual_type(
            temp_path=str(wrong_temp),
            current_ext=".html",
            current_url_type=URLType.WEBPAGE,
            response_content_type="application/xml",  # OSS error mime, untrusted
            content=png_bytes,
            meta=meta,
        )

        assert new_ext == ".png"
        assert new_url_type == URLType.DOWNLOAD_BINARY
        assert new_path.endswith(".png")
        assert not wrong_temp.exists()  # original path removed via rename
        assert meta["extension"] == ".png"
        assert meta["extension_corrected_from"] == ".html"
        assert meta["url_type_corrected_from"] == "webpage"

    def test_reconcile_no_change_when_extension_already_correct(self, tmp_path) -> None:
        """A correctly-typed download must not be renamed (no churn)."""
        png_bytes = b"\x89PNG\r\n\x1a\n" + b"\x00" * 32
        good_temp = tmp_path / "ok.png"
        good_temp.write_bytes(png_bytes)

        meta: dict = {"extension": ".png"}
        new_path, new_url_type, new_ext = HTTPAccessor._reconcile_actual_type(
            temp_path=str(good_temp),
            current_ext=".png",
            current_url_type=URLType.DOWNLOAD_BINARY,
            response_content_type="image/png",
            content=png_bytes,
            meta=meta,
        )

        assert new_ext == ".png"
        assert new_url_type == URLType.DOWNLOAD_BINARY
        assert new_path == str(good_temp)
        assert good_temp.exists()
        assert "extension_corrected_from" not in meta

    def test_reconcile_uses_response_content_type_when_no_magic_signature(self, tmp_path) -> None:
        """For payloads without magic bytes (e.g. plain text), fall back to the
        GET response's Content-Type."""
        text_bytes = b"some random text without any signature"
        wrong_temp = tmp_path / "fake.html"
        wrong_temp.write_bytes(text_bytes)

        meta: dict = {"extension": ".html"}
        new_path, new_url_type, new_ext = HTTPAccessor._reconcile_actual_type(
            temp_path=str(wrong_temp),
            current_ext=".html",
            current_url_type=URLType.WEBPAGE,
            response_content_type="text/plain; charset=utf-8",
            content=text_bytes,
            meta=meta,
        )

        # text/plain → .txt
        assert new_ext == ".txt"
        assert new_path.endswith(".txt")
        assert meta["extension_corrected_from"] == ".html"
