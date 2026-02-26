# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: Apache-2.0
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

    Returns:
        org/repo path like "volcengine/OpenViking" or None if not a valid
        code hosting URL
    """
    if not url.startswith(("http://", "https://", "git://", "ssh://")):
        return None

    parsed = urlparse(url)
    path_parts = [p for p in parsed.path.split("/") if p]

    config = get_openviking_config()

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
    return urlparse(url).netloc in all_domains
