# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0
"""Tests for code_hosting_utils git SSH URL support (Issue #317)."""

import importlib
import importlib.util
import sys
from pathlib import Path
from types import ModuleType, SimpleNamespace
from unittest.mock import patch

import pytest


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
            code_hosting_domains=[
                "github.com",
                "gitlab.com",
                "gitcode.com",
                "gitee.com",
                "bitbucket.org",
                "codeberg.org",
                "gitea.com",
                "atomgit.com",
                "git.sr.ht",
            ],
        )
    )


# Ensure openviking_cli.utils.config is importable (stub if needed)
_config_mod_name = "openviking_cli.utils.config"
try:
    importlib.import_module(_config_mod_name)
except Exception:
    for mod_name in ("openviking_cli", "openviking_cli.utils", _config_mod_name):
        if mod_name not in sys.modules:
            m = ModuleType(mod_name)
            sys.modules[mod_name] = m
    sys.modules[_config_mod_name].get_openviking_config = _mock_config  # type: ignore[attr-defined]

# Load code_hosting_utils directly from file to avoid the heavy openviking/__init__.py chain
_module_path = (
    Path(__file__).resolve().parents[1] / "openviking" / "utils" / "code_hosting_utils.py"
)
_spec = importlib.util.spec_from_file_location("openviking.utils.code_hosting_utils", _module_path)
_module = importlib.util.module_from_spec(_spec)
sys.modules["openviking.utils.code_hosting_utils"] = _module
_spec.loader.exec_module(_module)

parse_code_hosting_url = _module.parse_code_hosting_url
parse_git_repo_url = _module.parse_git_repo_url
is_github_url = _module.is_github_url
is_gitlab_url = _module.is_gitlab_url
is_code_hosting_url = _module.is_code_hosting_url
is_git_repo_url = _module.is_git_repo_url
validate_git_ssh_uri = _module.validate_git_ssh_uri


@pytest.fixture(autouse=True)
def _patch_config():
    with patch.object(_module, "get_openviking_config", side_effect=_mock_config):
        yield


# --- parse_code_hosting_url ---


def test_parse_code_hosting_url_git_ssh():
    assert parse_code_hosting_url("git@github.com:org/repo.git") == "org/repo"


def test_parse_code_hosting_url_git_ssh_no_dotgit():
    assert parse_code_hosting_url("git@github.com:org/repo") == "org/repo"


def test_parse_code_hosting_url_git_ssh_unknown_host():
    assert parse_code_hosting_url("git@unknown.com:org/repo.git") is None


def test_parse_code_hosting_url_git_ssh_single_segment():
    assert parse_code_hosting_url("git@github.com:repo") is None


@pytest.mark.parametrize(
    "url",
    [
        "git@git.example.com:repo.git",
        "git@git.example.com:repo",
        "ssh://git@git.example.com/repo.git",
        "ssh://git@git.example.com/repo",
        "git://git.example.com/repo.git",
        "git://git.example.com/repo",
    ],
)
def test_parse_code_hosting_url_single_segment_protocol_keeps_legacy_none(url):
    config = _mock_config()
    config.code.github_domains = []
    config.code.gitlab_domains = []
    config.code.azure_devops_domains = []
    config.code.code_hosting_domains = ["git.example.com"]

    with patch.object(_module, "get_openviking_config", return_value=config):
        assert parse_code_hosting_url(url) is None


def test_parse_code_hosting_url_https():
    assert parse_code_hosting_url("https://github.com/org/repo") == "org/repo"


def test_parse_code_hosting_url_https_dotgit():
    assert parse_code_hosting_url("https://github.com/org/repo.git") == "org/repo"


def test_parse_code_hosting_url_ssh_url_with_userinfo():
    assert parse_code_hosting_url("ssh://git@github.com/org/repo.git") == "org/repo"


def test_parse_code_hosting_url_gitlab_ssh_url_with_userinfo():
    assert parse_code_hosting_url("ssh://git@gitlab.com/group/repo.git") == "group/repo"


def test_parse_code_hosting_url_https_with_port():
    assert parse_code_hosting_url("https://github.com:443/org/repo") == "org/repo"


def test_parse_code_hosting_url_azure_devops_repo():
    assert (
        parse_code_hosting_url("https://dev.azure.com/org/project/_git/repo") == "org/project/repo"
    )


def test_parse_code_hosting_url_azure_devops_repo_dotgit():
    assert (
        parse_code_hosting_url("https://dev.azure.com/org/project/_git/repo.git")
        == "org/project/repo"
    )


def test_parse_code_hosting_url_azure_devops_repo_with_encoded_spaces():
    assert (
        parse_code_hosting_url("https://dev.azure.com/org/my%20project/_git/repo")
        == "org/my_project/repo"
    )


def test_parse_code_hosting_url_azure_devops_ssh_repo():
    assert parse_code_hosting_url("git@ssh.dev.azure.com:v3/org/project/repo") == "org/project/repo"


def test_parse_code_hosting_url_azure_devops_ssh_scheme_repo():
    assert (
        parse_code_hosting_url("ssh://git@ssh.dev.azure.com/v3/org/project/repo.git")
        == "org/project/repo"
    )


def test_parse_code_hosting_url_azure_devops_legacy_ssh_repo():
    assert (
        parse_code_hosting_url("git@vs-ssh.visualstudio.com:v3/org/project/repo")
        == "org/project/repo"
    )


def test_parse_code_hosting_url_azure_rule_not_applied_to_github():
    assert parse_code_hosting_url("https://github.com/org/repo/tree/main/_git/config") == "org/repo"


def test_parse_code_hosting_url_azure_devops_non_repo_page():
    assert parse_code_hosting_url("https://dev.azure.com/org/project/_build") is None


def test_parse_code_hosting_url_azure_devops_pull_request_page():
    assert (
        parse_code_hosting_url("https://dev.azure.com/org/project/_git/repo/pullrequest/123")
        is None
    )


# --- validate_git_ssh_uri ---


def test_validate_git_ssh_uri_valid():
    validate_git_ssh_uri("git@github.com:org/repo.git")  # should not raise


def test_validate_git_ssh_uri_not_git():
    with pytest.raises(ValueError, match="Not a git@ SSH URI"):
        validate_git_ssh_uri("https://github.com/org/repo")


def test_validate_git_ssh_uri_no_colon():
    with pytest.raises(ValueError, match="missing colon or empty path"):
        validate_git_ssh_uri("git@github.com")


def test_validate_git_ssh_uri_empty_path():
    with pytest.raises(ValueError, match="missing colon or empty path"):
        validate_git_ssh_uri("git@github.com:")


# --- is_code_hosting_url ---


def test_is_code_hosting_url_git_ssh():
    assert is_code_hosting_url("git@github.com:org/repo.git") is True


def test_is_code_hosting_url_git_ssh_no_colon():
    assert is_code_hosting_url("git@github.com") is False


def test_is_code_hosting_url_https():
    assert is_code_hosting_url("https://github.com/org/repo") is True


def test_is_code_hosting_url_ssh_url_with_userinfo():
    assert is_code_hosting_url("ssh://git@github.com/org/repo.git") is True


def test_is_code_hosting_url_azure_devops_ssh():
    assert is_code_hosting_url("git@ssh.dev.azure.com:v3/org/project/repo") is True


def test_is_code_hosting_url_https_with_explicit_port():
    assert is_code_hosting_url("https://github.com:443/org/repo") is True


# --- is_github_url / is_gitlab_url ---


def test_is_github_url_ssh_url_with_userinfo():
    assert is_github_url("ssh://git@github.com/org/repo.git") is True


def test_is_gitlab_url_ssh_url_with_userinfo():
    assert is_gitlab_url("ssh://git@gitlab.com/group/repo.git") is True


# --- is_git_repo_url ---


def test_is_git_repo_url_git_ssh():
    assert is_git_repo_url("git@github.com:org/repo.git") is True


def test_is_git_repo_url_https_repo():
    assert is_git_repo_url("https://github.com/org/repo") is True


def test_is_git_repo_url_ssh_url_with_userinfo():
    assert is_git_repo_url("ssh://git@github.com/org/repo.git") is True


def test_is_git_repo_url_https_with_port():
    assert is_git_repo_url("https://github.com:443/org/repo") is True


def test_is_git_repo_url_azure_devops_repo():
    assert is_git_repo_url("https://dev.azure.com/org/project/_git/repo") is True


def test_is_git_repo_url_azure_devops_repo_dotgit():
    assert is_git_repo_url("https://dev.azure.com/org/project/_git/repo.git") is True


def test_is_git_repo_url_azure_devops_ssh_repo():
    assert is_git_repo_url("git@ssh.dev.azure.com:v3/org/project/repo") is True


def test_is_git_repo_url_azure_devops_ssh_scheme_repo():
    assert is_git_repo_url("ssh://git@ssh.dev.azure.com/v3/org/project/repo.git") is True


def test_is_git_repo_url_azure_devops_legacy_ssh_repo():
    assert is_git_repo_url("git@vs-ssh.visualstudio.com:v3/org/project/repo") is True


def test_is_git_repo_url_azure_devops_non_repo_page():
    assert is_git_repo_url("https://dev.azure.com/org/project/_build") is False


def test_is_git_repo_url_azure_devops_browse_url_with_path_query():
    assert is_git_repo_url("https://dev.azure.com/org/project/_git/repo?path=/README.md") is False


def test_is_git_repo_url_azure_devops_pull_request_page():
    assert is_git_repo_url("https://dev.azure.com/org/project/_git/repo/pullrequest/123") is False


def test_is_git_repo_url_azure_devops_commit_page():
    assert is_git_repo_url("https://dev.azure.com/org/project/_git/repo/commit/abc1234") is False


def test_is_git_repo_url_https_issues():
    assert is_git_repo_url("https://github.com/org/repo/issues/123") is False


def test_is_git_repo_url_https_pull():
    assert is_git_repo_url("https://github.com/org/repo/pull/456") is False


def test_is_git_repo_url_https_blob():
    assert is_git_repo_url("https://github.com/org/repo/blob/main/file.py") is False


def test_is_git_repo_url_github_tree_path_with__git_segment():
    # A subdirectory browse URL is ambiguous with a slashed branch name, so it
    # must stay on the web path. The _git segment must not trigger Azure rules.
    assert is_git_repo_url("https://github.com/org/repo/tree/main/_git/config") is False


def test_is_git_repo_url_unknown_domain():
    assert is_git_repo_url("https://example.com/org/repo") is False


def test_is_git_repo_url_single_segment():
    assert is_git_repo_url("https://github.com/org") is False


@pytest.mark.parametrize(
    "url",
    [
        "git@git.example.com:repo.git",
        "git@git.example.com:repo",
        "ssh://git@git.example.com/repo.git",
        "ssh://git@git.example.com/repo",
        "git://git.example.com/repo.git",
        "git://git.example.com/repo",
    ],
)
def test_is_git_repo_url_single_segment_protocol_repo(url):
    config = _mock_config()
    config.code.github_domains = []
    config.code.gitlab_domains = []
    config.code.azure_devops_domains = []
    config.code.code_hosting_domains = ["git.example.com"]

    with patch.object(_module, "get_openviking_config", return_value=config):
        parsed = parse_git_repo_url(url)

        assert parsed is not None
        assert parsed.clone_url == url
        assert parsed.repo_path == "repo"
        assert parsed.route_kind == "clone"
        assert is_git_repo_url(url) is True


def test_is_git_repo_url_single_segment_https_repo_remains_rejected():
    config = _mock_config()
    config.code.github_domains = []
    config.code.gitlab_domains = []
    config.code.azure_devops_domains = []
    config.code.code_hosting_domains = ["git.example.com"]

    with patch.object(_module, "get_openviking_config", return_value=config):
        assert is_git_repo_url("https://git.example.com/repo.git") is False


@pytest.mark.parametrize(
    ("url", "repo_path"),
    [
        ("git@git.example.com:org/subgroup/repo", "org/subgroup/repo"),
        ("ssh://git@git.example.com/org/subgroup/repo", "org/subgroup/repo"),
        ("git://git.example.com/org/subgroup/repo", "org/subgroup/repo"),
        ("ssh://git@git.example.com/org/repo/tree/main", "org/repo/tree/main"),
    ],
)
def test_explicit_git_transport_preserves_nested_path_without_dotgit(url, repo_path):
    config = _mock_config()
    config.code.github_domains = []
    config.code.gitlab_domains = []
    config.code.azure_devops_domains = []
    config.code.code_hosting_domains = ["git.example.com"]

    with patch.object(_module, "get_openviking_config", return_value=config):
        parsed = parse_git_repo_url(url)

        assert parsed is not None
        assert parsed.clone_url == url
        assert parsed.repo_path == repo_path
        assert parsed.route_kind == "clone"
        assert parsed.branch is None
        assert parsed.commit is None
        assert is_git_repo_url(url) is True
        assert parse_code_hosting_url(url) == repo_path


# --- is_git_repo_url: nested groups and .git clone URLs ---


def test_is_git_repo_url_gitcode_nested_dotgit():
    assert is_git_repo_url("https://gitcode.com/GitHub_Trending/bl/black.git") is True


def test_gitcode_nested_blob_file_ending_dotgit_is_not_a_repo():
    url = "https://gitcode.com/GitHub_Trending/no/nocobase/blob/bcfdc2/examples/base/config.git"

    assert parse_git_repo_url(url) is None
    assert is_git_repo_url(url) is False
    assert parse_code_hosting_url(url) == "GitHub_Trending/no/nocobase"


def test_gitcode_tree_browse_file_ending_dotgit_is_not_a_repo():
    url = "https://gitcode.com/ploc-org/CNPL/tree/master/projects/sample/config.git"

    assert parse_git_repo_url(url) is None
    assert is_git_repo_url(url) is False
    assert parse_code_hosting_url(url) == "ploc-org/CNPL"


def test_is_git_repo_url_gitcode_tree():
    assert is_git_repo_url("https://gitcode.com/ploc-org/CNPL/tree/master") is True


def test_is_git_repo_url_gitee_repo():
    assert is_git_repo_url("https://gitee.com/mindspore/mindspore") is True


def test_is_git_repo_url_gitee_tree():
    assert is_git_repo_url("https://gitee.com/mindspore/mindspore/tree/master") is True


def test_is_git_repo_url_gitee_organization_page():
    url = "https://gitee.com/organizations/mindspore"

    assert parse_git_repo_url(url) is None
    assert is_git_repo_url(url) is False


def test_gitee_tree_browse_file_ending_dotgit_is_not_a_repo():
    url = "https://gitee.com/mindspore/mindspore/tree/master/config.git"

    assert parse_git_repo_url(url) is None
    assert is_git_repo_url(url) is False
    assert parse_code_hosting_url(url) == "mindspore/mindspore"


def test_is_git_repo_url_gitlab_subgroup_dotgit():
    assert is_git_repo_url("https://gitlab.com/org/subgroup/repo.git") is True


def test_parse_git_repo_url_gitlab_subgroup_named_tree():
    url = "https://gitlab.com/org/subgroup/tree/repo.git"

    parsed = parse_git_repo_url(url)

    assert parsed is not None
    assert parsed.clone_url == url
    assert parsed.repo_path == "org/subgroup/tree/repo"
    assert parsed.route_kind == "clone"
    assert parsed.branch is None
    assert parsed.commit is None
    assert parse_code_hosting_url(url) == "org/subgroup/tree/repo"


def test_is_git_repo_url_gitlab_subgroup_without_dotgit_rejected():
    # Ambiguous with subgroup pages; only .git or /-/ forms disambiguate
    assert is_git_repo_url("https://gitlab.com/org/subgroup/repo") is False


def test_is_git_repo_url_nested_repo_named_blob_dotgit():
    # A nested repo literally named "blob" must not be taken as a browse page
    assert is_git_repo_url("https://gitlab.com/org/subgroup/blob.git") is True


def test_is_git_repo_url_github_nested_dotgit_rejected():
    # GitHub has no subgroup clone form; accepting this would make the archive
    # fast path fetch org/repo while recording a different nested source.
    assert is_git_repo_url("https://github.com/org/repo/not-a-repo.git") is False


def test_is_git_repo_url_dotgit_only_segment():
    assert is_git_repo_url("https://github.com/org/.git") is False


# --- is_git_repo_url: GitLab "/-/" browse URLs ---


def test_is_git_repo_url_gitlab_dash_tree():
    assert is_git_repo_url("https://gitlab.com/gitlab-org/gitlab/-/tree/master") is True


def test_is_git_repo_url_gitlab_dash_tree_nested():
    assert is_git_repo_url("https://gitlab.com/org/subgroup/repo/-/tree/main") is True


def test_is_git_repo_url_gitlab_dash_commit_sha():
    assert (
        is_git_repo_url(
            "https://gitlab.com/org/repo/-/commit/1234567890abcdef1234567890abcdef12345678"
        )
        is True
    )


def test_is_git_repo_url_gitlab_dash_issues():
    assert is_git_repo_url("https://gitlab.com/org/repo/-/issues/1") is False


def test_is_git_repo_url_gitlab_dash_blob():
    assert is_git_repo_url("https://gitlab.com/org/repo/-/blob/main/file.py") is False


# --- is_git_repo_url: commit pins and tree refs ---


def test_is_git_repo_url_github_commit_sha():
    assert is_git_repo_url("https://github.com/org/repo/commit/abc1234") is True


def test_is_git_repo_url_github_commit_non_sha():
    assert is_git_repo_url("https://github.com/org/repo/commit/not-a-sha") is False


def test_is_git_repo_url_tree_ref_with_slash_rejected_as_ambiguous():
    assert is_git_repo_url("https://github.com/org/repo/tree/feature/foo") is False


def test_is_git_repo_url_tree_subdirectory_rejected_as_ambiguous():
    assert is_git_repo_url("https://github.com/org/repo/tree/main/src") is False


def test_is_git_repo_url_gitlab_dash_tree_with_slash_rejected_as_ambiguous():
    assert is_git_repo_url("https://gitlab.com/org/repo/-/tree/feature/foo") is False


# --- is_git_repo_url: reserved platform pages ---


def test_is_git_repo_url_reserved_topics():
    assert is_git_repo_url("https://github.com/topics/python") is False


def test_is_git_repo_url_reserved_orgs():
    assert is_git_repo_url("https://github.com/orgs/volcengine") is False


def test_is_git_repo_url_reserved_groups():
    assert is_git_repo_url("https://gitlab.com/groups/gitlab-org") is False


def test_is_git_repo_url_reserved_explore():
    assert is_git_repo_url("https://gitee.com/explore/starred") is False


@pytest.mark.parametrize(
    "url",
    [
        "https://gitcode.com/explore/repos",
        "https://bitbucket.org/product/features",
        "https://codeberg.org/explore/repos",
        "https://gitea.com/explore/repos",
        "https://atomgit.com/explore/repos",
    ],
)
def test_is_git_repo_url_additional_platform_pages(url):
    assert is_git_repo_url(url) is False


def test_generic_host_does_not_inherit_platform_reserved_namespaces():
    config = _mock_config()
    config.code.code_hosting_domains = ["git.example.com"]

    with patch.object(_module, "get_openviking_config", return_value=config):
        assert is_git_repo_url("https://git.example.com/topics/repo") is True
        assert is_git_repo_url("https://git.example.com/groups/repo") is True


def test_is_git_repo_url_azure_project_page():
    # Azure DevOps project pages are not cloneable; only the _git form is
    assert is_git_repo_url("https://dev.azure.com/org/project") is False


# --- is_git_repo_url: ssh/git protocols require a repo path ---


def test_is_git_repo_url_ssh_url_without_path():
    assert is_git_repo_url("ssh://git@github.com/") is False


def test_is_git_repo_url_git_at_without_path():
    assert is_git_repo_url("git@github.com:") is False


def test_is_git_repo_url_git_at_nested_dotgit():
    assert is_git_repo_url("git@gitcode.com:GitHub_Trending/bl/black.git") is True


# --- is_git_repo_url: additional default platforms ---


def test_is_git_repo_url_bitbucket_repo():
    assert is_git_repo_url("https://bitbucket.org/team/repo.git") is True


def test_is_git_repo_url_bitbucket_src_browse_rejected():
    # Bitbucket uses /src/<ref> for browsing; only repo/clone URLs are accepted
    assert is_git_repo_url("https://bitbucket.org/team/repo/src/main/file.py") is False


@pytest.mark.parametrize(
    "url",
    [
        "https://bitbucket.org/team/repo/src/main/config.git",
        "https://codeberg.org/team/repo/src/branch/main/config.git",
        "https://gitea.com/team/repo/src/branch/main/config.git",
    ],
)
def test_is_git_repo_url_src_browse_file_ending_dotgit_rejected(url):
    assert is_git_repo_url(url) is False


def test_parse_code_hosting_url_bitbucket_src_file_ending_dotgit():
    assert (
        parse_code_hosting_url("https://bitbucket.org/team/repo/src/main/config.git") == "team/repo"
    )


def test_is_git_repo_url_codeberg_repo():
    assert is_git_repo_url("https://codeberg.org/forgejo/forgejo") is True


def test_is_git_repo_url_atomgit_repo():
    assert is_git_repo_url("https://atomgit.com/openharmony/docs.git") is True


def test_is_git_repo_url_gitea_repo():
    assert is_git_repo_url("https://gitea.com/gitea/tea") is True


def test_is_git_repo_url_sourcehut_repo():
    assert is_git_repo_url("https://git.sr.ht/~sircmpwn/aerc") is True


@pytest.mark.parametrize(
    ("url", "repo_path"),
    [
        (
            "https://atomgit.com/easyxmen/docs/tree/master/ReleaseNotes/config.git",
            "easyxmen/docs",
        ),
        (
            "https://git.sr.ht/~sircmpwn/aerc/tree/master/item/config.git",
            "_sircmpwn/aerc",
        ),
    ],
)
def test_tree_browse_file_ending_dotgit_rejected_for_default_platforms(url, repo_path):
    assert parse_git_repo_url(url) is None
    assert is_git_repo_url(url) is False
    assert parse_code_hosting_url(url) == repo_path


def test_generic_domain_supports_nested_clone_without_platform_route_semantics():
    config = _mock_config()
    config.code.code_hosting_domains = ["git.example.com"]

    with patch.object(_module, "get_openviking_config", return_value=config):
        assert is_git_repo_url("https://git.example.com/org/subgroup/repo.git") is True
        assert is_git_repo_url("https://git.example.com/team/repo/src/main.git") is True
        assert is_git_repo_url("https://git.example.com/org/subgroup/repo/blob/leaf.git") is True


def test_generic_domain_keeps_nested_path_through_final_dotgit_segment():
    config = _mock_config()
    config.code.code_hosting_domains = ["git.example.com"]
    url = "https://git.example.com/org/archive.git/repo.git"

    with patch.object(_module, "get_openviking_config", return_value=config):
        parsed = parse_git_repo_url(url)

        assert parsed is not None
        assert parsed.clone_url == url
        assert parsed.repo_path == "org/archive_git/repo"
        assert parse_code_hosting_url(url) == "org/archive_git/repo"


def test_generic_domain_with_configured_port_supports_nested_clone():
    config = _mock_config()
    config.code.github_domains = []
    config.code.gitlab_domains = []
    config.code.azure_devops_domains = []
    config.code.code_hosting_domains = ["git.example.com:8443"]
    url = "https://git.example.com:8443/org/subgroup/repo.git"

    with patch.object(_module, "get_openviking_config", return_value=config):
        assert is_code_hosting_url(url) is True
        assert is_git_repo_url(url) is True
        assert parse_code_hosting_url(url) == "org/subgroup/repo"


def test_parse_code_hosting_url_sourcehut_tilde_sanitized():
    assert parse_code_hosting_url("https://git.sr.ht/~sircmpwn/aerc") == "_sircmpwn/aerc"


# --- parse_code_hosting_url: nested groups ---


def test_parse_code_hosting_url_gitcode_nested_dotgit():
    assert (
        parse_code_hosting_url("https://gitcode.com/GitHub_Trending/bl/black.git")
        == "GitHub_Trending/bl/black"
    )


def test_parse_code_hosting_url_gitlab_subgroup_dotgit():
    assert parse_code_hosting_url("https://gitlab.com/org/subgroup/repo.git") == "org/subgroup/repo"


def test_parse_code_hosting_url_gitlab_dash_tree_nested():
    assert (
        parse_code_hosting_url("https://gitlab.com/org/subgroup/repo/-/tree/main")
        == "org/subgroup/repo"
    )


def test_parse_code_hosting_url_git_ssh_nested_dotgit():
    assert (
        parse_code_hosting_url("git@gitcode.com:GitHub_Trending/bl/black.git")
        == "GitHub_Trending/bl/black"
    )


def test_parse_code_hosting_url_blob_file_named_dotgit_not_nested():
    # org/repo/blob/main/file.git is a file browse URL, not a nested repo path
    assert parse_code_hosting_url("https://github.com/org/repo/blob/main/file.git") == "org/repo"


def test_parse_code_hosting_url_github_tree_unchanged():
    assert parse_code_hosting_url("https://github.com/org/repo/tree/main") == "org/repo"


@pytest.mark.parametrize(
    ("url", "clone_url", "repo_path", "branch", "commit"),
    [
        (
            "https://github.com/org/repo/tree/main",
            "https://github.com/org/repo",
            "org/repo",
            "main",
            None,
        ),
        (
            "https://github.com/org/repo/commit/abc1234",
            "https://github.com/org/repo",
            "org/repo",
            None,
            "abc1234",
        ),
        (
            "https://gitlab.com/org/subgroup/repo/-/tree/main",
            "https://gitlab.com/org/subgroup/repo",
            "org/subgroup/repo",
            "main",
            None,
        ),
        (
            "git@ssh.dev.azure.com:v3/org/project/repo",
            "git@ssh.dev.azure.com:v3/org/project/repo",
            "org/project/repo",
            None,
            None,
        ),
    ],
)
def test_parse_git_repo_url_returns_consistent_clone_metadata(
    url, clone_url, repo_path, branch, commit
):
    parsed = parse_git_repo_url(url)

    assert parsed is not None
    assert parsed.clone_url == clone_url
    assert parsed.repo_path == repo_path
    assert parsed.branch == branch
    assert parsed.commit == commit
    assert is_git_repo_url(url) is True
