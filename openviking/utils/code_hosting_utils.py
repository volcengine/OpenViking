# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0
"""
Utilities for code hosting platform URL parsing.

This module provides shared functionality for parsing URLs from code hosting
platforms like GitHub and GitLab.
"""

from typing import Optional
from urllib.parse import ParseResult, parse_qs, unquote, urlparse

from openviking_cli.utils.config import get_openviking_config


def _domain_matches(parsed: ParseResult, domains: list[str]) -> bool:
    """Return True when parsed URL host matches configured domains.

    ``urlparse().netloc`` includes optional userinfo and port values. Repository
    clone URLs commonly use forms like ``ssh://git@github.com/org/repo.git``,
    where the netloc is ``git@github.com`` but the actual host is
    ``github.com``.
    """
    hostname = parsed.hostname
    if not hostname:
        return False

    normalized_domains = {domain.lower() for domain in domains}
    host = hostname.lower()
    candidates = {host}

    try:
        port = parsed.port
    except ValueError:
        port = None
    if port is not None:
        candidates.add(f"{host}:{port}")

    return any(candidate in normalized_domains for candidate in candidates)


def _extract_host(url: str) -> str:
    """Extract normalized host for supported git/code-hosting URL forms."""
    if url.startswith("git@"):
        rest = url[4:]
        if ":" not in rest:
            return ""
        return rest.split(":", 1)[0].strip().lower()

    parsed = urlparse(url)
    return (parsed.hostname or parsed.netloc or "").strip().lower()


def _get_all_domains() -> list[str]:
    config = get_openviking_config()
    return list(
        set(
            config.code.github_domains
            + config.code.gitlab_domains
            + getattr(config.code, "azure_devops_domains", [])
            + config.code.code_hosting_domains
        )
    )


def _get_azure_devops_domains() -> set[str]:
    config = get_openviking_config()
    return set(getattr(config.code, "azure_devops_domains", []))


def _sanitize_segment(segment: str) -> str:
    decoded_segment = unquote(segment)
    return "".join(c if c.isalnum() or c in "-_" else "_" for c in decoded_segment)


def _extract_azure_devops_repo_parts(path_parts: list[str]) -> Optional[list[str]]:
    """Return Azure DevOps repository path parts ending in repo name."""
    try:
        git_index = path_parts.index("_git")
    except ValueError:
        return None

    if git_index < 2 or git_index + 1 >= len(path_parts) or len(path_parts) != git_index + 2:
        return None

    repo_parts = path_parts[:git_index] + [path_parts[git_index + 1]]
    if not all(repo_parts):
        return None
    return repo_parts


def _extract_azure_devops_ssh_repo_parts(path_parts: list[str]) -> Optional[list[str]]:
    """Return Azure DevOps SSH repository path parts ending in repo name."""
    if len(path_parts) != 4 or path_parts[0] != "v3":
        return None

    repo_parts = path_parts[1:]
    if not all(repo_parts):
        return None
    return repo_parts


def _is_azure_devops_browse_url(query: str) -> bool:
    """Return True for Azure DevOps repo browsing URLs like ?path=/README.md."""
    return "path" in parse_qs(query, keep_blank_values=True)


def parse_code_hosting_url(url: str) -> Optional[str]:
    """Parse code hosting platform URL to get org/repo path.

    Args:
        url: Code hosting URL like https://github.com/volcengine/OpenViking
             or git@github.com:volcengine/OpenViking.git

    Returns:
        org/repo path like "volcengine/OpenViking" or None if not a valid
        code hosting URL
    """
    all_domains = _get_all_domains()
    host = _extract_host(url)

    # Handle git@ SSH URLs: git@host:org/repo.git
    if url.startswith("git@"):
        if ":" not in url[4:]:
            return None
        host_part, path_part = url[4:].split(":", 1)
        if host_part not in all_domains:
            return None
        path_parts = [p for p in path_part.split("/") if p]
        if host_part in _get_azure_devops_domains():
            azure_repo_parts = _extract_azure_devops_ssh_repo_parts(path_parts)
            if azure_repo_parts:
                return "/".join(
                    _sanitize_segment(part.removesuffix(".git")) for part in azure_repo_parts
                )
        if len(path_parts) < 2:
            return None
        # Take only first 2 segments (consistent with HTTP branch)
        org = path_parts[0]
        repo = path_parts[1]
        if repo.endswith(".git"):
            repo = repo[:-4]
        org = _sanitize_segment(org)
        repo = _sanitize_segment(repo)
        return f"{org}/{repo}"

    if not url.startswith(("http://", "https://", "git://", "ssh://")):
        return None

    parsed = urlparse(url)
    path_parts = [p for p in parsed.path.split("/") if p]

    if _domain_matches(parsed, list(_get_azure_devops_domains())):
        azure_repo_parts = _extract_azure_devops_repo_parts(path_parts)
        if azure_repo_parts is None:
            azure_repo_parts = _extract_azure_devops_ssh_repo_parts(path_parts)
        if azure_repo_parts:
            return "/".join(_sanitize_segment(part.removesuffix(".git")) for part in azure_repo_parts)
        return None

    # For code hosting URLs with org/repo structure
    if _domain_matches(parsed, all_domains) and len(path_parts) >= 2:
        # Take first two parts: org/repo
        org = path_parts[0]
        repo = path_parts[1]
        if repo.endswith(".git"):
            repo = repo[:-4]
        # Sanitize both parts
        org = _sanitize_segment(org)
        repo = _sanitize_segment(repo)
        return f"{org}/{repo}"

    return None


def is_github_url(url: str) -> bool:
    """Check if a URL is a GitHub URL.

    Args:
        url: URL to check

    Returns:
        True if the URL is a GitHub URL
    """
    config = get_openviking_config()
    return _extract_host(url) in config.code.github_domains


def is_gitlab_url(url: str) -> bool:
    """Check if a URL is a GitLab URL.

    Args:
        url: URL to check

    Returns:
        True if the URL is a GitLab URL
    """
    config = get_openviking_config()
    return _extract_host(url) in config.code.gitlab_domains


def is_code_hosting_url(url: str) -> bool:
    """Check if a URL is a code hosting platform URL.

    Args:
        url: URL to check

    Returns:
        True if the URL is a code hosting platform URL
    """
    all_domains = _get_all_domains()

    # Handle git@ SSH URLs
    if url.startswith("git@"):
        if ":" not in url[4:]:
            return False
        host_part = url[4:].split(":", 1)[0]
        return host_part in all_domains

    return _domain_matches(urlparse(url), all_domains)


def validate_git_ssh_uri(url: str) -> None:
    """Validate a git@ SSH URI format.

    Args:
        url: URL to validate (e.g. git@github.com:org/repo.git)

    Raises:
        ValueError: If the URL is not a valid git@ SSH URI
    """
    if not url.startswith("git@"):
        raise ValueError(f"Not a git@ SSH URI: {url}")
    rest = url[4:]
    if ":" not in rest or not rest.split(":", 1)[1]:
        raise ValueError(f"Invalid git@ SSH URI (missing colon or empty path): {url}")


def is_git_repo_url(url: str) -> bool:
    """Strict check for cloneable git repository URLs.

    Distinguishes repo URLs (github.com/org/repo) from non-repo URLs
    (github.com/org/repo/issues/123).

    Args:
        url: URL to check

    Returns:
        True if the URL points to a cloneable git repository
    """
    # git@/ssh://git:// protocols: always a repo if the domain matches
    if url.startswith(("git@", "ssh://", "git://")):
        return is_code_hosting_url(url)

    # http/https: check domain AND require exactly 2 path parts (owner/repo)
    if url.startswith(("http://", "https://")):
        config = get_openviking_config()
        all_domains = _get_all_domains()
        parsed = urlparse(url)
        if not _domain_matches(parsed, all_domains):
            return False
        path_parts = [p for p in parsed.path.split("/") if p]
        # Strip .git suffix from last part for counting
        if path_parts and path_parts[-1].endswith(".git"):
            path_parts[-1] = path_parts[-1][:-4]

        if _extract_host(url) in _get_azure_devops_domains():
            azure_repo_parts = _extract_azure_devops_repo_parts(path_parts)
            if azure_repo_parts:
                if _is_azure_devops_browse_url(parsed.query):
                    return False
                return True

        non_repo_paths = {
            "blob",
            "commit",
            "commits",
            "issues",
            "merge_requests",
            "pull",
            "pulls",
            "raw",
            "releases",
            "wiki",
        }
        if (
            _extract_host(url) in config.code.github_domains + config.code.gitlab_domains
            and len(path_parts) >= 3
            and path_parts[2] in non_repo_paths
        ):
            return False

        # owner/repo
        if len(path_parts) == 2:
            return True
        # owner/repo/tree/<ref> (branch name or commit SHA)
        if len(path_parts) == 4 and path_parts[2] == "tree":
            return True
        return False

    return False
