# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0
"""Unit tests for HTTPAccessor."""

from types import SimpleNamespace
from unittest.mock import patch

import pytest

from openviking.parse.accessors import AccessorRegistry, GitAccessor, HTTPAccessor
from openviking.parse.accessors.http_accessor import URLType


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

    @pytest.fixture(autouse=True)
    def _patch_config(self):
        with patch(
            "openviking_cli.utils.config.open_viking_config.OpenVikingConfigSingleton.get_instance",
            side_effect=_mock_config,
        ):
            yield

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

    @pytest.mark.parametrize("entry_url_type", [URLType.WEBPAGE, URLType.DOWNLOAD_HTML])
    async def test_webpage_uses_web_importer_directory(
        self, accessor, tmp_path, monkeypatch, entry_url_type
    ):
        downloaded = tmp_path / "entry.html"
        downloaded.write_text("<html>entry</html>", encoding="utf-8")
        imported_dir = tmp_path / "web"
        imported_dir.mkdir()

        async def fake_download(url, request_validator=None):
            return str(downloaded), entry_url_type, {"extension": ".html"}

        class FakeImporter:
            async def import_to_directory(self, *, root_url, options, request_validator=None):
                assert root_url == "https://example.com/page"
                assert options.depth == 1
                return SimpleNamespace(
                    path=imported_dir,
                    meta={
                        "web_import": True,
                        "crawl_result": {"total_crawled": 1},
                        "original_filename": "example.com",
                    },
                )

        monkeypatch.setattr(accessor, "_download_url", fake_download)
        monkeypatch.setattr(
            "openviking.parse.accessors.web_importer.WebImporter",
            lambda: FakeImporter(),
        )

        resource = await accessor.access("https://example.com/page", depth=1)

        assert resource.path == imported_dir
        assert resource.path.is_dir()
        assert resource.meta["url_type"] == "webpage"
        assert resource.meta["web_import"] is True
        assert not downloaded.exists()

    async def test_download_file_does_not_use_web_importer(self, accessor, tmp_path, monkeypatch):
        downloaded = tmp_path / "file.pdf"
        downloaded.write_bytes(b"%PDF-test")
        called = False

        async def fake_download(url, request_validator=None):
            return str(downloaded), URLType.DOWNLOAD_PDF, {"extension": ".pdf"}

        class FakeImporter:
            async def import_to_directory(self, **kwargs):
                nonlocal called
                called = True

        monkeypatch.setattr(accessor, "_download_url", fake_download)
        monkeypatch.setattr(
            "openviking.parse.accessors.web_importer.WebImporter",
            lambda: FakeImporter(),
        )

        resource = await accessor.access("https://example.com/file.pdf", depth=1)

        assert resource.path == downloaded
        assert resource.meta["url_type"] == "download_pdf"
        assert called is False

    @pytest.mark.parametrize(
        "url",
        [
            "https://github.com/org/repo/blob/main/x.html",
            "https://raw.githubusercontent.com/org/repo/main/x.html",
            "https://gitlab.com/org/repo/blob/main/x.html",
        ],
    )
    async def test_code_hosting_single_file_does_not_use_web_importer(
        self, accessor, tmp_path, monkeypatch, url
    ):
        """Code-hosting single-file URLs (blob/raw) stay on the single-file path.

        Even though the download resolves to an ``.html`` file (DOWNLOAD_HTML),
        these URLs are semantically one file, not a crawlable site, so they must
        NOT be routed through WebImporter.
        """
        downloaded = tmp_path / "x.html"
        downloaded.write_text("<html>file</html>", encoding="utf-8")
        called = False

        async def fake_download(u, request_validator=None):
            return str(downloaded), URLType.DOWNLOAD_HTML, {"extension": ".html"}

        class FakeImporter:
            async def import_to_directory(self, **kwargs):
                nonlocal called
                called = True

        monkeypatch.setattr(accessor, "_download_url", fake_download)
        monkeypatch.setattr(
            "openviking.parse.accessors.web_importer.WebImporter",
            lambda: FakeImporter(),
        )

        resource = await accessor.access(url, depth=1)

        assert resource.path == downloaded
        assert resource.meta["url_type"] == "download_html"
        assert called is False


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
