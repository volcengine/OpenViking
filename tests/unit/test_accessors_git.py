# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0
"""Unit tests for GitAccessor."""

from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import pytest

from openviking.parse.accessors import GitAccessor
from openviking.utils import code_hosting_utils


def _mock_config():
    return SimpleNamespace(
        code=SimpleNamespace(
            github_domains=["github.com", "www.github.com"],
            gitlab_domains=["gitlab.com", "www.gitlab.com"],
            code_hosting_domains=["github.com", "gitlab.com"],
        )
    )


@pytest.fixture(autouse=True)
def _patch_config():
    with patch.object(code_hosting_utils, "get_openviking_config", side_effect=_mock_config):
        yield


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


class TestGitAccessor:
    """Tests for GitAccessor."""

    @pytest.fixture(autouse=True)
    def _patch_config(self):
        with patch(
            "openviking_cli.utils.config.open_viking_config.OpenVikingConfigSingleton.get_instance",
            side_effect=_mock_config,
        ):
            yield

    @pytest.fixture
    def accessor(self) -> GitAccessor:
        """Create a GitAccessor instance."""
        return GitAccessor()

    def test_priority(self, accessor: GitAccessor) -> None:
        """GitAccessor should have correct priority."""
        assert accessor.priority == 80

    @pytest.mark.parametrize(
        "source",
        [
            "git@github.com:volcengine/OpenViking.git",
            "git@gitlab.com:org/repo.git",
            "git@ssh.dev.azure.com:v3/org/project/repo",
            "ssh://git@ssh.dev.azure.com/v3/org/project/repo.git",
            "git@vs-ssh.visualstudio.com:v3/org/project/repo",
        ],
    )
    def test_can_handle_git_ssh_url(self, accessor: GitAccessor, source: str) -> None:
        """GitAccessor should handle git@ SSH URLs."""
        assert accessor.can_handle(source) is True

    @pytest.mark.parametrize(
        "source",
        [
            "https://github.com/volcengine/OpenViking",
            "https://github.com/volcengine/OpenViking.git",
            "https://gitlab.com/org/repo",
            "http://github.com/org/repo",
            "https://oauth2:secret@gitlab.com/group/subgroup/repo.git",
        ],
    )
    def test_can_handle_github_http_url(self, accessor: GitAccessor, source: str) -> None:
        """GitAccessor should handle GitHub/GitLab HTTP URLs."""
        assert accessor.can_handle(source) is True

    @pytest.mark.parametrize(
        "source",
        [
            "https://github.com/volcengine/OpenViking/tree/main",
            "https://github.com/volcengine/OpenViking/tree/abc1234",
        ],
    )
    def test_can_handle_github_with_ref(self, accessor: GitAccessor, source: str) -> None:
        """GitAccessor should handle GitHub URLs with branch/commit."""
        assert accessor.can_handle(source) is True

    @pytest.mark.parametrize(
        "source",
        [
            "https://dev.azure.com/org/project/_git/repo",
            "https://dev.azure.com/org/project/_git/repo.git",
        ],
    )
    def test_can_handle_azure_devops_http_url(self, accessor: GitAccessor, source: str) -> None:
        """GitAccessor should handle Azure DevOps repository URLs."""
        assert accessor.can_handle(source) is True

    def test_can_handle_git_protocol_url(self, accessor: GitAccessor) -> None:
        """GitAccessor should handle git:// URLs."""
        assert accessor.can_handle("git://github.com/volcengine/OpenViking.git") is True

    def test_normalize_repo_url_ssh_with_userinfo_and_ref(self, accessor: GitAccessor) -> None:
        """GitAccessor should normalize ssh URLs with userinfo using the shared host matcher."""
        assert (
            accessor._normalize_repo_url("ssh://git@github.com:443/volcengine/OpenViking/tree/main")
            == "ssh://git@github.com:443/volcengine/OpenViking"
        )

    def test_normalize_gitlab_nested_namespace_with_oauth(self, accessor: GitAccessor) -> None:
        assert (
            accessor._normalize_repo_url("https://oauth2:secret@gitlab.com/group/subgroup/repo.git")
            == "https://oauth2:secret@gitlab.com/group/subgroup/repo.git"
        )

    def test_redact_url_credentials(self, accessor: GitAccessor) -> None:
        source = "https://oauth2:secret@gitlab.com/group/subgroup/repo.git"
        assert (
            accessor._redact_url_credentials(source) == "https://gitlab.com/group/subgroup/repo.git"
        )

    @pytest.mark.parametrize(
        "source",
        [
            "/path/to/repo.git",
        ],
    )
    def test_can_handle_local_files(self, accessor: GitAccessor, source: str) -> None:
        """GitAccessor should handle local .git files."""
        assert accessor.can_handle(Path(source)) is True

    def test_cannot_handle_local_zip_file(self, accessor: GitAccessor) -> None:
        """GitAccessor should leave local zip files to LocalAccessor/ZipParser."""
        assert accessor.can_handle(Path("/path/to/archive.zip")) is False

    @pytest.mark.parametrize(
        "source",
        [
            "https://example.com/page.html",
            "https://github.com/volcengine/OpenViking/issues/123",
            "https://dev.azure.com/org/project/_build",
            "https://dev.azure.com/org/project/_git/repo?path=/README.md",
            "https://dev.azure.com/org/project/_git/repo/pullrequest/123",
            "https://dev.azure.com/org/project/_git/repo/commit/abc1234",
            "git@example.com:repo",
        ],
    )
    def test_cannot_handle_other_urls(self, accessor: GitAccessor, source: str) -> None:
        """GitAccessor should not handle non-git URLs or files."""
        assert accessor.can_handle(source) is False

    async def test_git_clone_does_not_fetch_submodules(
        self, accessor: GitAccessor, tmp_path: Path
    ) -> None:
        with patch.object(accessor, "_run_git", new_callable=AsyncMock) as run_git:
            await accessor._git_clone("https://github.com/volcengine/OpenViking.git", str(tmp_path))

        clone_args = run_git.await_args.args[0]
        assert "--no-recurse-submodules" in clone_args
        assert "--recursive" not in clone_args

    async def test_git_clone_marker_does_not_store_oauth_credentials(
        self, accessor: GitAccessor, tmp_path: Path
    ) -> None:
        source = "https://oauth2:secret@gitlab.com/group/subgroup/repo.git"
        with patch.object(accessor, "_run_git", new_callable=AsyncMock):
            await accessor._git_clone(source, str(tmp_path))

        assert (tmp_path / ".git_source_repo").read_text(encoding="utf-8") == (
            "https://gitlab.com/group/subgroup/repo.git"
        )

    async def test_access_does_not_propagate_oauth_credentials(
        self, accessor: GitAccessor, tmp_path: Path
    ) -> None:
        source = "https://oauth2:secret@gitlab.com/group/subgroup/repo.git"
        with (
            patch("tempfile.mkdtemp", return_value=str(tmp_path)),
            patch.object(
                accessor,
                "_gitlab_zip_download",
                new=AsyncMock(return_value=(tmp_path, "group/subgroup/repo")),
            ),
        ):
            resource = await accessor.access(source)

        assert resource.original_source == "https://gitlab.com/group/subgroup/repo.git"

    async def test_gitlab_archive_preserves_nested_namespace_and_redacts_errors(
        self, accessor: GitAccessor, tmp_path: Path
    ) -> None:
        source = "https://oauth2:secret@gitlab.com/group/subgroup/repo.git"
        with patch(
            "openviking.parse.accessors.git_accessor.urllib.request.urlopen",
            side_effect=OSError("failed https://oauth2:secret@gitlab.com/group/subgroup/repo.git"),
        ) as urlopen:
            with pytest.raises(RuntimeError) as exc_info:
                await accessor._gitlab_zip_download(source, "main", str(tmp_path))

        request = urlopen.call_args.args[0]
        assert request.full_url.endswith("/group/subgroup/repo/-/archive/main/repo-main.zip")
        assert "oauth2:secret@" not in request.full_url
        assert request.get_header("Authorization") == "Basic b2F1dGgyOnNlY3JldA=="
        assert "oauth2:secret@" not in str(exc_info.value)

    async def test_github_archive_encodes_fragment_in_ref(
        self, accessor: GitAccessor, tmp_path: Path
    ) -> None:
        with patch(
            "openviking.parse.accessors.git_accessor.urllib.request.urlopen",
            side_effect=OSError("stop before network"),
        ) as urlopen:
            with pytest.raises(RuntimeError):
                await accessor._github_zip_download(
                    "https://github.com/example/repo", "test#ssrf", str(tmp_path)
                )

        request = urlopen.call_args.args[0]
        assert request.full_url == "https://github.com/example/repo/archive/test%23ssrf.zip"

    async def test_git_error_does_not_expose_remote_stderr(self, accessor: GitAccessor) -> None:
        process = SimpleNamespace(
            returncode=1,
            communicate=AsyncMock(return_value=(b"", b"remote: internal metadata")),
        )
        with patch(
            "openviking.parse.accessors.git_accessor.asyncio.create_subprocess_exec",
            new=AsyncMock(return_value=process),
        ):
            with pytest.raises(RuntimeError) as exc_info:
                await accessor._run_git(["git", "clone", "https://github.com/example/repo"])

        assert str(exc_info.value) == "Git command failed."
        assert "internal metadata" not in str(exc_info.value)
