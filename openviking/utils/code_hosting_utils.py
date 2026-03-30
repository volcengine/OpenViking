# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0
"""
Utilities for code hosting platform URL parsing.

This module provides shared functionality for parsing URLs from code hosting
platforms like GitHub and GitLab.
"""

from typing import Optional
from urllib.parse import urlparse

from openviking_cli.utils.config import get_openviking_config


def parse_code_hosting_url(url: str) -> Optional[str]:
    """Parse code hosting platform URL to get org/repo path.

    Args:
        url: Code hosting URL like https://github.com/volcengine/OpenViking
             or git@github.com:volcengine/OpenViking.git

    Returns:
        org/repo path like "volcengine/OpenViking" or None if not a valid
        code hosting URL
    """
    config = get_openviking_config()
    all_domains = list(
        set(
            config.code.github_domains
            + config.code.gitlab_domains
            + config.code.code_hosting_domains
        )
    )

    # Handle git@ SSH URLs: git@host:org/repo.git
    if url.startswith("git@"):
        if ":" not in url[4:]:
            return None
        host_part, path_part = url[4:].split(":", 1)
        if host_part not in all_domains:
            return None
        path_parts = [p for p in path_part.split("/") if p]
        if len(path_parts) < 2:
            return None
        # Take only first 2 segments (consistent with HTTP branch)
        org = path_parts[0]
        repo = path_parts[1]
        if repo.endswith(".git"):
            repo = repo[:-4]
        org = "".join(c if c.isalnum() or c in "-_" else "_" for c in org)
        repo = "".join(c if c.isalnum() or c in "-_" else "_" for c in repo)
        return f"{org}/{repo}"

    if not url.startswith(("http://", "https://", "git://", "ssh://")):
        return None

    parsed = urlparse(url)
    path_parts = [p for p in parsed.path.split("/") if p]

    # For GitHub/GitLab URLs with org/repo structure
    if (
        parsed.netloc in config.code.github_domains + config.code.gitlab_domains
        and len(path_parts) >= 2
    ):
        # Take first two parts: org/repo
        org = path_parts[0]
        repo = path_parts[1]
        if repo.endswith(".git"):
            repo = repo[:-4]
        # Sanitize both parts
        org = "".join(c if c.isalnum() or c in "-_" else "_" for c in org)
        repo = "".join(c if c.isalnum() or c in "-_" else "_" for c in repo)
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
    return urlparse(url).netloc in config.code.github_domains


def is_gitlab_url(url: str) -> bool:
    """Check if a URL is a GitLab URL.

    Args:
        url: URL to check

    Returns:
        True if the URL is a GitLab URL
    """
    config = get_openviking_config()
    return urlparse(url).netloc in config.code.gitlab_domains


def is_code_hosting_url(url: str) -> bool:
    """Check if a URL is a code hosting platform URL.

    Args:
        url: URL to check

    Returns:
        True if the URL is a code hosting platform URL
    """
    config = get_openviking_config()
    all_domains = list(
        set(
            config.code.github_domains
            + config.code.gitlab_domains
            + config.code.code_hosting_domains
        )
    )

    # Handle git@ SSH URLs
    if url.startswith("git@"):
        if ":" not in url[4:]:
            return False
        host_part = url[4:].split(":", 1)[0]
        return host_part in all_domains

    return urlparse(url).netloc in all_domains


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
        all_domains = list(
            set(
                config.code.github_domains
                + config.code.gitlab_domains
                + config.code.code_hosting_domains
            )
        )
        parsed = urlparse(url)
        if parsed.netloc not in all_domains:
            return False
        path_parts = [p for p in parsed.path.split("/") if p]
        # Strip .git suffix from last part for counting
        if path_parts and path_parts[-1].endswith(".git"):
            path_parts[-1] = path_parts[-1][:-4]
        # owner/repo
        if len(path_parts) == 2:
            return True
        # owner/repo/tree/<ref> (branch name or commit SHA)
        if len(path_parts) == 4 and path_parts[2] == "tree":
            return True
        return False

    return False
