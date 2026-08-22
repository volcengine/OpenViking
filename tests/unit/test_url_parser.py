# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0
"""Tests for URL parser."""

import pytest

from openviking.utils.url_parser import ParsedURL, normalize_repo_url, parse_url


def _old_normalize_repo_url_buggy(url: str) -> str:
    """Original normalize_repo_url with the ':' not in user bug."""
    value = url.strip()
    if not value:
        return ""
    lowered = value.lower()
    protocol = next(
        (
            prefix
            for prefix in ("ssh://", "git://", "http://", "https://")
            if lowered.startswith(prefix)
        ),
        None,
    )
    if protocol:
        value = value[len(protocol) :]
    if "@" in value:
        user, remainder = value.split("@", 1)
        if user and "/" not in user and ":" not in user:
            value = remainder
    if protocol is None:
        slash = value.find("/")
        colon = value.find(":")
        if colon >= 0 and (slash < 0 or colon < slash):
            value = f"{value[:colon]}/{value[colon + 1 :]}"
    while "//" in value:
        value = value.replace("//", "/")

    def _strip_port(host: str) -> str:
        head, separator, tail = host.rpartition(":")
        if separator and tail and tail.isascii() and tail.isdigit():
            return head
        return host

    if "/" in value:
        host, path = value.split("/", 1)
        value = f"{_strip_port(host).lower()}/{path}"
    else:
        value = _strip_port(value).lower()
    if value.lower().endswith(".git"):
        value = value[:-4]
    return value.rstrip("/")


def test_full_url_with_all_components():
    url = "https://user:pass123@example.com:8080/path/to/page?name=kagi#top"
    parsed = parse_url(url)
    assert parsed.scheme == "https"
    assert parsed.username == "user"
    assert parsed.password == "pass123"
    assert parsed.hostname == "example.com"
    assert parsed.port == 8080
    assert parsed.path == "/path/to/page"
    assert parsed.query == "name=kagi"
    assert parsed.fragment == "top"


def test_gitlab_oauth2_url():
    url = "https://oauth2:password@lily.thenovel.org/gitlab/subgroup/repo.git"
    parsed = parse_url(url)
    assert parsed.scheme == "https"
    assert parsed.username == "oauth2"
    assert parsed.password == "password"
    assert parsed.hostname == "lily.thenovel.org"
    assert parsed.port is None
    assert parsed.path == "/gitlab/subgroup/repo.git"
    assert parsed.query is None
    assert parsed.fragment is None


def test_github_url_without_credentials():
    url = "https://github.com/org/repo.git"
    parsed = parse_url(url)
    assert parsed.scheme == "https"
    assert parsed.username is None
    assert parsed.password is None
    assert parsed.hostname == "github.com"
    assert parsed.port is None
    assert parsed.path == "/org/repo.git"


def test_url_without_scheme():
    # urlparse without scheme treats input as path, not netloc (standard behavior)
    url = "hostname.com/path"
    parsed = parse_url(url)
    assert parsed.scheme is None
    assert parsed.hostname is None
    assert parsed.path == "hostname.com/path"


def test_url_with_query_only():
    url = "http://example.com/search?q=test"
    parsed = parse_url(url)
    assert parsed.scheme == "http"
    assert parsed.query == "q=test"
    assert parsed.fragment is None


def test_url_with_fragment_only():
    url = "https://example.com/page#section"
    parsed = parse_url(url)
    assert parsed.fragment == "section"
    assert parsed.query is None


def test_strip_userinfo():
    url = "https://user:pass@example.com:443/path"
    parsed = parse_url(url)
    stripped = parsed.strip_userinfo()
    assert stripped.username is None
    assert stripped.password is None
    assert stripped.hostname == "example.com"
    assert stripped.path == "/path"


def test_to_url_without_credentials():
    url = "https://user:pass123@example.com:8080/path?q=1#top"
    parsed = parse_url(url)
    reconstructed = parsed.to_url_without_credentials()
    assert reconstructed == "https://example.com:8080/path?q=1#top"


def test_invalid_input():
    assert parse_url("") is None
    assert parse_url(None) is None  # type: ignore[arg-type]


def test_password_with_at_sign():
    # urllib.parse handles @ in password by treating last @ as userinfo separator
    url = "https://user:p@ss@example.com/path"
    parsed = parse_url(url)
    assert parsed.username == "user"
    assert parsed.password == "p@ss"
    assert parsed.hostname == "example.com"
    assert parsed.path == "/path"


# normalize_repo_url tests


def test_normalize_gitlab_oauth2_with_creds():
    url = "https://oauth2:password@lily.thenovel.org/gitlab/subgroup/repo.git"
    assert normalize_repo_url(url) == "lily.thenovel.org/gitlab/subgroup/repo"


def test_normalize_gitlab_oauth2_vs_old_buggy():
    # Old version left userinfo in the locator because of ':' not in user check
    url = "https://oauth2:password@lily.thenovel.org/gitlab/subgroup/repo.git"
    old_result = _old_normalize_repo_url_buggy(url)
    new_result = normalize_repo_url(url)
    assert "@" in old_result  # old version failed to strip userinfo
    assert "@" not in new_result  # new version strips it correctly
    assert new_result == "lily.thenovel.org/gitlab/subgroup/repo"


def test_normalize_github_https():
    url = "https://github.com/org/repo.git"
    assert normalize_repo_url(url) == "github.com/org/repo"


def test_normalize_ssh_url():
    url = "ssh://git@github.com/org/repo.git"
    assert normalize_repo_url(url) == "github.com/org/repo"


def test_normalize_scp_style():
    url = "git@github.com:org/repo.git"
    assert normalize_repo_url(url) == "github.com/org/repo"


def test_normalize_git_protocol():
    url = "git://github.com/org/repo.git"
    assert normalize_repo_url(url) == "github.com/org/repo"


def test_normalize_with_port():
    url = "https://oauth2:token@git.example.com:8443/group/subgroup/repo.git"
    assert normalize_repo_url(url) == "git.example.com/group/subgroup/repo"


def test_normalize_no_creds_no_git_suffix():
    url = "https://gitlab.com/group/project"
    assert normalize_repo_url(url) == "gitlab.com/group/project"


def test_normalize_empty():
    assert normalize_repo_url("") == ""
    assert normalize_repo_url("   ") == ""


def test_normalize_preserves_path_case():
    url = "https://gitlab.com/MyGroup/MyProject.git"
    result = normalize_repo_url(url)
    assert result == "gitlab.com/MyGroup/MyProject"


def test_normalize_complex_creds():
    # Credentials with special characters
    url = "https://user:p%40ss@gitlab.example.com/a/b/c.git"
    assert normalize_repo_url(url) == "gitlab.example.com/a/b/c"


@pytest.mark.parametrize(
    ("url", "expected"),
    [
        ("git@github.com:volcengine/OpenViking.git", "github.com/volcengine/OpenViking"),
        ("https://User@GitHub.com/Org/Repo.git", "github.com/Org/Repo"),
        ("ssh://git@host.com:29418/t/repo", "host.com/t/repo"),
    ],
)
def test_normalize_regression_no_degradation(url: str, expected: str):
    """Ensure new parser matches old parser on all existing test cases."""
    new_result = normalize_repo_url(url)
    old_result = _old_normalize_repo_url_buggy(url)
    assert new_result == old_result
    assert new_result == expected
