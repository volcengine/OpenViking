# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0
"""Unit tests for GitAccessor."""

import subprocess
import traceback
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

    def test_normalize_gitlab_nested_tree_url(self, accessor: GitAccessor) -> None:
        assert (
            accessor._normalize_repo_url(
                "https://gitlab.com/group/subgroup/repo/-/tree/main/README.md"
            )
            == "https://gitlab.com/group/subgroup/repo"
        )

    def test_redact_url_credentials(self, accessor: GitAccessor) -> None:
        source = "https://oauth2:secret@gitlab.com/group/subgroup/repo.git"
        assert (
            accessor._redact_url_credentials(source) == "https://gitlab.com/group/subgroup/repo.git"
        )

    def test_redact_url_credentials_preserves_ssh_username(self, accessor: GitAccessor) -> None:
        source = "ssh://git@gitlab.com/group/subgroup/repo.git"
        assert accessor._redact_url_credentials(source) == source

    def test_redact_authorization_header(self, accessor: GitAccessor) -> None:
        assert accessor._redact_credentials_in_text("Authorization: Bearer secret-token") == (
            "Authorization: Bearer [REDACTED]"
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
        self, accessor: GitAccessor, tmp_path: Path, monkeypatch
    ) -> None:
        source = "https://oauth2:secret@gitlab.com/group/subgroup/repo.git"
        global_config = tmp_path / "existing-global.config"
        global_config.write_text("[test]\n\tinherited = preserved\n", encoding="utf-8")
        monkeypatch.setenv("GIT_CONFIG_GLOBAL", str(global_config))
        monkeypatch.setenv("GIT_CONFIG_COUNT", "0")
        observed = {}

        async def _capture_git(args, cwd=None, env=None):
            observed["args"] = args
            auth_config_index = int(env["GIT_CONFIG_COUNT"]) - 1
            assert env[f"GIT_CONFIG_KEY_{auth_config_index}"] == "include.path"
            observed["auth_config"] = Path(env[f"GIT_CONFIG_VALUE_{auth_config_index}"])
            observed["auth_content"] = observed["auth_config"].read_text(encoding="utf-8")
            observed["global_config"] = env["GIT_CONFIG_GLOBAL"]
            observed["inherited"] = subprocess.run(
                ["git", "config", "--get", "test.inherited"],
                env=env,
                check=True,
                capture_output=True,
                text=True,
            ).stdout.strip()
            return ""

        with patch.object(accessor, "_run_git", side_effect=_capture_git):
            await accessor._git_clone(source, str(tmp_path))

        assert source not in observed["args"]
        assert "secret" not in " ".join(observed["args"])
        assert "Authorization: Basic b2F1dGgyOnNlY3JldA==" in observed["auth_content"]
        assert observed["global_config"] == str(global_config)
        assert observed["inherited"] == "preserved"
        assert not observed["auth_config"].exists()
        assert (tmp_path / ".git_source_repo").read_text(encoding="utf-8") == (
            "https://gitlab.com/group/subgroup/repo.git"
        )

    async def test_git_clone_removes_auth_config_when_chmod_fails(
        self, accessor: GitAccessor, tmp_path: Path
    ) -> None:
        source = "https://oauth2:secret@gitlab.com/group/subgroup/repo.git"
        auth_config_path = tmp_path / "auth.config"

        def _open_auth_config(**kwargs):
            del kwargs
            return auth_config_path.open("w", encoding="utf-8")

        with (
            patch(
                "openviking.parse.accessors.git_accessor.tempfile.NamedTemporaryFile",
                side_effect=_open_auth_config,
            ),
            patch(
                "openviking.parse.accessors.git_accessor.os.chmod",
                side_effect=OSError("chmod failed"),
            ),
        ):
            with pytest.raises(OSError, match="chmod failed"):
                await accessor._git_clone(source, str(tmp_path / "repo"))

        assert not auth_config_path.exists()

    async def test_git_clone_preserves_ssh_transport_username(
        self, accessor: GitAccessor, tmp_path: Path
    ) -> None:
        source = "ssh://git@gitlab.com/group/subgroup/repo.git"
        observed = {}

        async def _capture_git(args, cwd=None, env=None):
            del cwd, env
            observed["args"] = args
            return ""

        with patch.object(accessor, "_run_git", side_effect=_capture_git):
            await accessor._git_clone(source, str(tmp_path))

        assert source in observed["args"]
        assert (tmp_path / ".git_source_repo").read_text(encoding="utf-8") == source

    async def test_git_clone_rejects_http_username_without_password(
        self, accessor: GitAccessor, tmp_path: Path
    ) -> None:
        source = "https://alice@gitlab.com/group/subgroup/repo.git"

        with patch.object(accessor, "_run_git", new_callable=AsyncMock) as run_git:
            with pytest.raises(ValueError, match="must also include a password"):
                await accessor._git_clone(source, str(tmp_path))

        run_git.assert_not_awaited()

    async def test_authenticated_commit_checkout_uses_scoped_git_environment(
        self, accessor: GitAccessor, tmp_path: Path
    ) -> None:
        source = "https://oauth2:secret@gitlab.com/group/subgroup/repo.git"
        observed = []

        async def _capture_git(args, cwd=None, env=None):
            del cwd
            observed.append((args, env))
            return ""

        with patch.object(accessor, "_run_git", side_effect=_capture_git):
            await accessor._git_clone(source, str(tmp_path), commit="deadbeef")

        checkout_args, checkout_env = observed[-1]
        assert checkout_args == ["git", "-C", str(tmp_path), "checkout", "deadbeef"]
        assert checkout_env["GIT_TERMINAL_PROMPT"] == "0"
        auth_config_index = int(checkout_env["GIT_CONFIG_COUNT"]) - 1
        assert checkout_env[f"GIT_CONFIG_KEY_{auth_config_index}"] == "include.path"

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
            "openviking.parse.accessors.git_accessor.urllib.request.build_opener"
        ) as build_opener:
            build_opener.return_value.open.side_effect = OSError(
                "failed https://oauth2:secret@gitlab.com/group/subgroup/repo.git"
            )
            with pytest.raises(RuntimeError) as exc_info:
                await accessor._gitlab_zip_download(source, "main", str(tmp_path))

        request = build_opener.return_value.open.call_args.args[0]
        assert request.full_url.endswith(
            "/api/v4/projects/group%2Fsubgroup%2Frepo/repository/archive.zip?sha=main"
        )
        assert "oauth2:secret@" not in request.full_url
        assert request.get_header("Authorization") == "Bearer secret"
        assert "secret" not in str(exc_info.value)
        formatted = "".join(traceback.format_exception(exc_info.value))
        assert "secret" not in formatted

    async def test_gitlab_archive_rejects_control_characters_before_header_creation(
        self, accessor: GitAccessor, tmp_path: Path
    ) -> None:
        source = "https://oauth2:secret%0Dleak@gitlab.com/group/subgroup/repo.git"

        with patch(
            "openviking.parse.accessors.git_accessor.urllib.request.build_opener"
        ) as build_opener:
            with pytest.raises(ValueError, match="Invalid control character") as exc_info:
                await accessor._gitlab_zip_download(source, "main", str(tmp_path))

        build_opener.assert_not_called()
        formatted = "".join(traceback.format_exception(exc_info.value))
        assert "secret" not in str(exc_info.value)
        assert "secret" not in formatted

    async def test_authenticated_gitlab_archive_rejects_cross_origin_redirect(
        self, accessor: GitAccessor, tmp_path: Path
    ) -> None:
        source = "https://oauth2:secret@gitlab.com/group/subgroup/repo.git"

        class RedirectingOpener:
            def __init__(self, redirect_handler):
                self.redirect_handler = redirect_handler

            def open(self, request, timeout):
                del timeout
                return self.redirect_handler.redirect_request(
                    request,
                    None,
                    302,
                    "Found",
                    {},
                    "https://objects.example.test/archive.zip",
                )

        with patch(
            "openviking.parse.accessors.git_accessor.urllib.request.build_opener",
            side_effect=lambda handler: RedirectingOpener(handler),
        ):
            with pytest.raises(RuntimeError, match="Refusing cross-origin redirect") as exc_info:
                await accessor._gitlab_zip_download(source, "main", str(tmp_path))

        assert "secret" not in str(exc_info.value)

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
