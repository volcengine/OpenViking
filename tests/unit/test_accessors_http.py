# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0
"""Unit tests for HTTPAccessor."""

from types import SimpleNamespace
from unittest.mock import patch

import pytest

from openviking.parse.accessors import AccessorRegistry, GitAccessor, HTTPAccessor


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
