# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0
"""Unit tests for GitAccessor."""

import asyncio
import base64
import os
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock, patch

import pytest

from openviking.parse.accessors import GitAccessor
from openviking.parse.parsers.directory import DirectoryParser
from openviking.utils import code_hosting_utils
from openviking.utils.git_auth import (
    build_git_http_auth_env,
    git_http_basic_auth_header,
    parse_git_http_auth_config,
)
from openviking_cli.exceptions import InvalidArgumentError

_GENERIC_CODE_HOSTING_DOMAINS = [
    "github.com",
    "gitlab.com",
    "gitcode.com",
    "gitee.com",
    "bitbucket.org",
    "codeberg.org",
    "gitea.com",
    "atomgit.com",
    "git.sr.ht",
]


def _git_config_entries(env: dict[str, str]) -> dict[str, str]:
    count = int(env["GIT_CONFIG_COUNT"])
    return {
        env[f"GIT_CONFIG_KEY_{index}"]: env[f"GIT_CONFIG_VALUE_{index}"]
        for index in range(count)
    }


def test_git_http_auth_config_defaults_username_and_builds_isolated_env() -> None:
    base_env = {"EXISTING": "kept"}
    config = parse_git_http_auth_config(
        {"token": "secret-token"},
        "https://git.example.com/org/private.git",
    )

    assert config is not None
    assert config.username == "oauth2"
    assert config.token == "secret-token"
    assert "secret-token" not in repr(config)
    assert git_http_basic_auth_header(config) == (
        "Authorization: Basic "
        + base64.b64encode(b"oauth2:secret-token").decode("ascii")
    )

    repo_url = "https://git.example.com/org/private.git"
    env = build_git_http_auth_env(config, repo_url, base_env=base_env)
    assert base_env == {"EXISTING": "kept"}
    assert env["EXISTING"] == "kept"
    assert env["GIT_TERMINAL_PROMPT"] == "0"
    assert env["GCM_INTERACTIVE"] == "Never"
    assert _git_config_entries(env) == {
        "credential.helper": "",
        f"credential.{repo_url}.helper": "",
        "http.extraHeader": "",
        f"http.{repo_url}.extraHeader": git_http_basic_auth_header(config),
        f"http.{repo_url}.followRedirects": "false",
    }
    assert env["GIT_CONFIG_COUNT"] == "6"
    assert env["GIT_CONFIG_KEY_3"] == f"http.{repo_url}.extraHeader"
    assert env["GIT_CONFIG_VALUE_3"] == ""
    assert env["GIT_CONFIG_KEY_4"] == f"http.{repo_url}.extraHeader"


@pytest.mark.parametrize(
    ("value", "repo_url", "message"),
    [
        ("secret-token", "https://git.example.com/org/repo.git", "object"),
        ({}, "https://git.example.com/org/repo.git", "token"),
        ({"token": "   "}, "https://git.example.com/org/repo.git", "token"),
        ({"token": 123}, "https://git.example.com/org/repo.git", "token"),
        (
            {"token": "secret-token", "username": "   "},
            "https://git.example.com/org/repo.git",
            "username",
        ),
        (
            {"token": "secret-token", "password": "wrong-field"},
            "https://git.example.com/org/repo.git",
            "unsupported",
        ),
        (
            {"token": "secret-token"},
            "git@git.example.com:org/repo.git",
            "HTTPS",
        ),
        (
            {"token": "secret-token"},
            "http://git.example.com/org/repo.git",
            "HTTPS",
        ),
    ],
)
def test_git_http_auth_config_rejects_invalid_values(value, repo_url: str, message: str) -> None:
    with pytest.raises(InvalidArgumentError, match=message) as exc_info:
        parse_git_http_auth_config(value, repo_url)

    assert "secret-token" not in str(exc_info.value)


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
            code_hosting_domains=_GENERIC_CODE_HOSTING_DOMAINS,
        )
    )


@pytest.fixture(autouse=True)
def _patch_config():
    with patch.object(code_hosting_utils, "get_openviking_config", side_effect=_mock_config):
        yield


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

    @pytest.mark.parametrize(
        "source",
        [
            "git@git.example.com:repo.git",
            "git@git.example.com:repo",
            "ssh://git@git.example.com/repo.git",
            "ssh://git@git.example.com/repo",
            "git://git.example.com/repo.git",
            "git://git.example.com/repo",
        ],
    )
    def test_can_handle_single_segment_protocol_repo(
        self, accessor: GitAccessor, source: str
    ) -> None:
        config = _mock_config()
        config.code.github_domains = []
        config.code.gitlab_domains = []
        config.code.azure_devops_domains = []
        config.code.code_hosting_domains = ["git.example.com"]

        with patch.object(
            code_hosting_utils,
            "get_openviking_config",
            return_value=config,
        ):
            assert accessor.can_handle(source) is True

    @pytest.mark.parametrize(
        "source",
        [
            "git@git.example.com:org/subgroup/repo",
            "ssh://git@git.example.com/org/subgroup/repo",
            "git://git.example.com/org/subgroup/repo",
        ],
    )
    def test_can_handle_nested_protocol_repo_without_dotgit(
        self, accessor: GitAccessor, source: str
    ) -> None:
        config = _mock_config()
        config.code.github_domains = []
        config.code.gitlab_domains = []
        config.code.azure_devops_domains = []
        config.code.code_hosting_domains = ["git.example.com"]

        with patch.object(
            code_hosting_utils,
            "get_openviking_config",
            return_value=config,
        ):
            assert accessor.can_handle(source) is True
            assert accessor._normalize_repo_url(source) == source
            assert accessor._get_repo_name(source) == "org/subgroup/repo"

    @pytest.mark.parametrize(
        "source",
        [
            "ssh://git@github.com/",
            "git://github.com",
        ],
    )
    def test_cannot_handle_git_protocol_without_repo_path(
        self, accessor: GitAccessor, source: str
    ) -> None:
        assert accessor.can_handle(source) is False

    def test_ssh_transport_does_not_apply_http_tree_route_rules(
        self, accessor: GitAccessor
    ) -> None:
        """Explicit Git transports preserve paths that resemble HTTP browse routes."""
        assert (
            accessor._normalize_repo_url("ssh://git@github.com:443/volcengine/OpenViking/tree/main")
            == "ssh://git@github.com:443/volcengine/OpenViking/tree/main"
        )

    @pytest.mark.parametrize(
        "source",
        [
            "https://gitlab.com/org/subgroup/repo.git",
            "https://gitlab.com/org/subgroup/repo/-/tree/main",
        ],
    )
    def test_can_handle_gitlab_nested_urls(self, accessor: GitAccessor, source: str) -> None:
        """GitAccessor should handle GitLab subgroup clone URLs and /-/ tree URLs."""
        assert accessor.can_handle(source) is True

    def test_normalize_repo_url_gitlab_dash_tree(self, accessor: GitAccessor) -> None:
        """Normalization must cut at the GitLab /-/ separator, keeping the nested path."""
        assert (
            accessor._normalize_repo_url("https://gitlab.com/org/subgroup/repo/-/tree/main")
            == "https://gitlab.com/org/subgroup/repo"
        )

    def test_normalize_repo_url_gitlab_subgroup_dotgit(self, accessor: GitAccessor) -> None:
        """Nested .git clone URLs must not be truncated to the first two segments."""
        assert (
            accessor._normalize_repo_url("https://gitlab.com/org/subgroup/repo.git")
            == "https://gitlab.com/org/subgroup/repo.git"
        )

    def test_parse_repo_source_gitlab_project_named_commit(self, accessor: GitAccessor) -> None:
        """A project named commit must not shadow the /-/commit route marker."""
        source = "https://gitlab.com/org/commit/-/commit/1234567"

        assert accessor._parse_repo_source(source) == (
            "https://gitlab.com/org/commit",
            None,
            "1234567",
        )

    def test_parse_repo_source_gitlab_project_named_tree(self, accessor: GitAccessor) -> None:
        """A project named tree must not shadow the /-/tree route marker."""
        source = "https://gitlab.com/org/tree/-/tree/main"

        assert accessor._parse_repo_source(source) == (
            "https://gitlab.com/org/tree",
            "main",
            None,
        )

    def test_parse_repo_source_gitlab_subgroup_named_tree(self, accessor: GitAccessor) -> None:
        """A trailing .git keeps every nested namespace and does not imply a branch."""
        source = "https://gitlab.com/org/subgroup/tree/repo.git"

        assert accessor._parse_repo_source(source) == (source, None, None)

    def test_gitee_tree_browse_file_ending_dotgit_is_rejected(self, accessor: GitAccessor) -> None:
        source = "https://gitee.com/org/repo/tree/main/config.git"

        assert accessor.can_handle(source) is False
        with pytest.raises(ValueError, match="Unsupported Git repository URL"):
            accessor._parse_repo_source(source)

    def test_gitee_organization_page_is_not_handled(self, accessor: GitAccessor) -> None:
        assert accessor.can_handle("https://gitee.com/organizations/mindspore") is False

    def test_normalize_repo_url_rejects_github_nested_dotgit(self, accessor: GitAccessor) -> None:
        with pytest.raises(ValueError, match="Unsupported Git repository URL"):
            accessor._normalize_repo_url("https://github.com/org/repo/not-a-repo.git")

    def test_normalize_repo_url_gitcode_nested_dotgit(self, accessor: GitAccessor) -> None:
        assert (
            accessor._normalize_repo_url("https://gitcode.com/GitHub_Trending/bl/black.git")
            == "https://gitcode.com/GitHub_Trending/bl/black.git"
        )

    def test_gitcode_nested_blob_file_ending_dotgit_is_rejected(
        self, accessor: GitAccessor
    ) -> None:
        source = (
            "https://gitcode.com/GitHub_Trending/no/nocobase/blob/bcfdc2/examples/base/config.git"
        )

        assert accessor.can_handle(source) is False
        with pytest.raises(ValueError, match="Unsupported Git repository URL"):
            accessor._parse_repo_source(source)

    def test_gitcode_tree_file_ending_dotgit_is_rejected(self, accessor: GitAccessor) -> None:
        source = "https://gitcode.com/ploc-org/CNPL/tree/master/projects/sample/config.git"

        assert accessor.can_handle(source) is False
        with pytest.raises(ValueError, match="Unsupported Git repository URL"):
            accessor._parse_repo_source(source)

    @pytest.mark.parametrize(
        "source",
        [
            "https://atomgit.com/easyxmen/docs/tree/master/ReleaseNotes/config.git",
            "https://git.sr.ht/~sircmpwn/aerc/tree/master/item/config.git",
        ],
    )
    def test_default_platform_tree_file_ending_dotgit_is_rejected(
        self,
        accessor: GitAccessor,
        source: str,
    ) -> None:
        assert accessor.can_handle(source) is False
        with pytest.raises(ValueError, match="Unsupported Git repository URL"):
            accessor._parse_repo_source(source)

    def test_normalize_repo_url_uses_final_dotgit_segment(self, accessor: GitAccessor) -> None:
        config = _mock_config()
        config.code.github_domains = []
        config.code.gitlab_domains = []
        config.code.azure_devops_domains = []
        config.code.code_hosting_domains = ["git.example.com"]
        source = "https://git.example.com/org/archive.git/repo.git"

        with patch.object(
            code_hosting_utils,
            "get_openviking_config",
            return_value=config,
        ):
            assert accessor._normalize_repo_url(source) == source
            assert accessor._parse_repo_source(source) == (source, None, None)

    @pytest.mark.parametrize(
        ("source", "expected"),
        [
            ("https://gitlab.com/org/repo.git", True),
            ("https://gitlab.com/org/subgroup/repo.git", False),
        ],
    )
    def test_can_use_gitlab_zip(self, accessor: GitAccessor, source: str, expected: bool) -> None:
        assert accessor._can_use_gitlab_zip(source) is expected

    async def test_nested_gitlab_url_skips_zip_fast_path(
        self, accessor: GitAccessor, tmp_path: Path
    ) -> None:
        source = "https://gitlab.com/org/subgroup/repo.git"
        with (
            patch(
                "openviking.parse.accessors.git_accessor.tempfile.mkdtemp",
                return_value=str(tmp_path),
            ),
            patch.object(
                accessor,
                "_gitlab_zip_download",
                new_callable=AsyncMock,
            ) as zip_download,
            patch.object(
                accessor,
                "_git_clone",
                new_callable=AsyncMock,
                return_value="org/subgroup/repo",
            ) as git_clone,
        ):
            await accessor.access(source)

        zip_download.assert_not_awaited()
        git_clone.assert_awaited_once()
        assert git_clone.await_args.args[0] == source

    @pytest.mark.parametrize(
        ("source", "zip_method"),
        [
            ("https://github.com/org/private.git", "_github_zip_download"),
            ("https://gitlab.com/org/private.git", "_gitlab_zip_download"),
        ],
    )
    async def test_explicit_http_auth_skips_archive_fast_path_and_reaches_clone_env(
        self,
        accessor: GitAccessor,
        tmp_path: Path,
        source: str,
        zip_method: str,
    ) -> None:
        token = "private-token"
        with (
            patch(
                "openviking.parse.accessors.git_accessor.tempfile.mkdtemp",
                return_value=str(tmp_path),
            ),
            patch.object(accessor, zip_method, new_callable=AsyncMock) as zip_download,
            patch.object(
                accessor,
                "_git_clone",
                new_callable=AsyncMock,
                return_value="org/private",
            ) as git_clone,
        ):
            resource = await accessor.access(
                source,
                auth_config={"token": token, "username": "git-user"},
            )

        zip_download.assert_not_awaited()
        git_clone.assert_awaited_once()
        clone_call = git_clone.await_args
        assert clone_call.args[0] == source
        env = clone_call.kwargs["env"]
        assert token not in " ".join(str(arg) for arg in clone_call.args)
        assert _git_config_entries(env)[f"http.{source}.extraHeader"] == (
            "Authorization: Basic "
            + base64.b64encode(f"git-user:{token}".encode()).decode("ascii")
        )
        assert resource.original_source == source
        assert token not in str(resource.meta)

    async def test_embedded_http_credentials_are_passed_through_unchanged(
        self,
        accessor: GitAccessor,
        tmp_path: Path,
    ) -> None:
        source = "https://user:embedded-token@codeberg.org/org/private.git"
        with (
            patch(
                "openviking.parse.accessors.git_accessor.tempfile.mkdtemp",
                return_value=str(tmp_path),
            ),
            patch.object(
                accessor,
                "_git_clone",
                new_callable=AsyncMock,
                return_value="org/private",
            ) as git_clone,
        ):
            resource = await accessor.access(source)

        git_clone.assert_awaited_once()
        call = git_clone.await_args
        assert call.args[0] == source
        assert call.kwargs["env"] is None
        assert resource.original_source == source

    def test_normalize_repo_url_github_commit_page(self, accessor: GitAccessor) -> None:
        """Commit-pin URLs normalize to the bare repo; the ref is extracted separately."""
        assert (
            accessor._normalize_repo_url("https://github.com/org/repo/commit/abc1234")
            == "https://github.com/org/repo"
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

    async def test_git_clone_reuses_auth_env_for_clone_and_commit_fetches(
        self, accessor: GitAccessor, tmp_path: Path
    ) -> None:
        config = parse_git_http_auth_config(
            {"token": "secret-token"},
            "https://git.example.com/org/private.git",
        )
        assert config is not None
        auth_env = build_git_http_auth_env(
            config,
            "https://git.example.com/org/private.git",
            base_env={},
        )

        async def run_git(args, cwd=None, env=None):
            if "rev-parse" in args:
                return "deadbeef"
            return ""

        with patch.object(accessor, "_run_git", new=AsyncMock(side_effect=run_git)) as run_git_mock:
            await accessor._git_clone(
                "https://git.example.com/org/private.git",
                str(tmp_path),
                commit="deadbeef",
                env=auth_env,
            )

        network_calls = [
            call
            for call in run_git_mock.await_args_list
            if "clone" in call.args[0] or "fetch" in call.args[0]
        ]
        assert len(network_calls) >= 2
        assert all(call.kwargs["env"] is auth_env for call in network_calls)

    async def test_authenticated_git_failure_does_not_log_remote_stderr_or_auth(
        self, accessor: GitAccessor, caplog
    ) -> None:
        config = parse_git_http_auth_config(
            {"token": "secret-token"},
            "https://git.example.com/org/private.git",
        )
        assert config is not None
        env = build_git_http_auth_env(
            config,
            "https://git.example.com/org/private.git",
            base_env={},
        )
        encoded = base64.b64encode(b"oauth2:secret-token").decode("ascii")
        process = SimpleNamespace(
            returncode=1,
            communicate=AsyncMock(
                return_value=(
                    b"",
                    f"remote reflected secret-token and {encoded}".encode(),
                )
            ),
        )
        with patch(
            "openviking.parse.accessors.git_accessor.asyncio.create_subprocess_exec",
            new=AsyncMock(return_value=process),
        ):
            with pytest.raises(RuntimeError, match="Git command failed"):
                await accessor._run_git(
                    ["git", "clone", "https://git.example.com/org/private.git"],
                    env=env,
                )

        assert "secret-token" not in caplog.text
        assert encoded not in caplog.text
        assert "remote reflected" not in caplog.text
        assert os.environ.get("GIT_CONFIG_COUNT") != env["GIT_CONFIG_COUNT"]

    async def test_cancelled_git_process_is_killed_and_reaped(
        self,
        accessor: GitAccessor,
    ) -> None:
        process = SimpleNamespace(
            communicate=AsyncMock(side_effect=asyncio.CancelledError),
            kill=Mock(),
            wait=AsyncMock(),
        )
        with patch(
            "openviking.parse.accessors.git_accessor.asyncio.create_subprocess_exec",
            new=AsyncMock(return_value=process),
        ):
            with pytest.raises(asyncio.CancelledError):
                await accessor._run_git(
                    ["git", "clone", "https://git.example.com/org/private.git"]
                )

        process.kill.assert_called_once_with()
        process.wait.assert_awaited_once_with()

    @pytest.mark.parametrize(
        ("source", "repo_name"),
        [
            ("https://gitcode.com/org/repo.git", "org/repo"),
            ("https://gitee.com/org/repo.git", "org/repo"),
            ("https://bitbucket.org/org/repo.git", "org/repo"),
            ("https://codeberg.org/org/repo.git", "org/repo"),
            ("https://gitea.com/org/repo.git", "org/repo"),
            ("https://atomgit.com/org/repo.git", "org/repo"),
            ("https://git.sr.ht/~user/repo", "_user/repo"),
        ],
    )
    async def test_generic_code_hosts_are_marked_for_code_repository_parser(
        self,
        accessor: GitAccessor,
        tmp_path: Path,
        source: str,
        repo_name: str,
    ) -> None:
        with patch.object(accessor, "_run_git", new_callable=AsyncMock):
            assert await accessor._git_clone(source, str(tmp_path)) == repo_name

        assert (tmp_path / ".git_source_repo").read_text(encoding="utf-8") == source
        assert await DirectoryParser._is_git_repository(tmp_path) is True

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
