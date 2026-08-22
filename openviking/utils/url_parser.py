# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0
"""Structured URL parsing with full component extraction."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Optional
from urllib.parse import urlparse, urlunparse


@dataclass(frozen=True)
class ParsedURL:
    """Fully parsed URL with all components."""

    scheme: Optional[str] = None
    username: Optional[str] = None
    password: Optional[str] = None
    hostname: Optional[str] = None
    port: Optional[int] = None
    path: Optional[str] = None
    query: Optional[str] = None
    fragment: Optional[str] = None

    def strip_userinfo(self) -> "ParsedURL":
        """Return a copy with username and password removed."""
        return ParsedURL(
            scheme=self.scheme,
            hostname=self.hostname,
            port=self.port,
            path=self.path,
            query=self.query,
            fragment=self.fragment,
        )

    def to_url_without_credentials(self) -> str:
        """Reconstruct the URL without embedded credentials."""
        netloc = self.hostname or ""
        if self.port is not None:
            netloc = f"{netloc}:{self.port}"
        return urlunparse((
            self.scheme or "",
            netloc,
            self.path or "",
            "",
            self.query or "",
            self.fragment or "",
        ))


def parse_url(url: str) -> Optional[ParsedURL]:
    """Parse a URL into structured components using urllib.parse.urlparse.

    Args:
        url: URL string to parse

    Returns:
        ParsedURL with all components, or None if the URL is malformed
    """
    if not url or not isinstance(url, str):
        return None

    try:
        result = urlparse(url.strip())
    except ValueError:
        return None

    # Extract username and password from netloc
    username = result.username
    password = result.password
    hostname = result.hostname
    port = result.port

    # urlparse requires a scheme to parse userinfo; handle schemeless URLs
    if "@" in result.netloc and not result.scheme:
        userinfo_netloc = result.netloc.split("@", 1)
        if len(userinfo_netloc) == 2:
            userinfo = userinfo_netloc[0]
            remainder = userinfo_netloc[1]
            username, _, password = userinfo.partition(":")
            username = username or None
            password = password if password else None

    return ParsedURL(
        scheme=result.scheme or None,
        username=username or None,
        password=password or None,
        hostname=hostname,
        port=port,
        path=result.path or None,
        query=result.query or None,
        fragment=result.fragment or None,
    )


def normalize_repo_url(url: str) -> str:
    """Normalize supported Git URL forms into a stable host/path locator.

    Strips scheme, credentials, port, .git suffix, and trailing slash.
    Handles ssh://, git://, http://, https://, and SCP-style (git@host:path).

    Args:
        url: Git repository URL

    Returns:
        Normalized locator like 'lily.thenovel.org/gitlab/subgroup/repo'
    """
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

    # Strip userinfo (user:pass@) before host
    if "@" in value:
        user, remainder = value.split("@", 1)
        if user and "/" not in user:
            value = remainder

    # Handle SCP-style: git@host:path -> host/path
    if protocol is None:
        slash = value.find("/")
        colon = value.find(":")
        if colon >= 0 and (slash < 0 or colon < slash):
            value = f"{value[:colon]}/{value[colon + 1 :]}"

    # Collapse double slashes
    while "//" in value:
        value = value.replace("//", "/")

    # Strip port from host
    if "/" in value:
        host, path = value.split("/", 1)
        head, separator, tail = host.rpartition(":")
        if separator and tail and tail.isascii() and tail.isdigit():
            host = head
        value = f"{host.lower()}/{path}"
    else:
        head, separator, tail = value.rpartition(":")
        if separator and tail and tail.isascii() and tail.isdigit():
            value = head
        value = value.lower()

    # Remove .git suffix and trailing slash
    if value.lower().endswith(".git"):
        value = value[:-4]
    return value.rstrip("/")
