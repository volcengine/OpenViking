# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0
"""Network target validation helpers for server-side remote fetches."""

from __future__ import annotations

import ipaddress
import socket
from collections.abc import Callable
from typing import Optional
from urllib.parse import urlparse

from openviking_cli.exceptions import PermissionDeniedError
from openviking_cli.utils.config import get_openviking_config

RequestValidator = Callable[[str], None]

_LOCAL_HOSTNAMES = {
    "localhost",
    "localhost.localdomain",
}


def _get_allowed_code_hosting_domains() -> set[str]:
    """Get allowed code hosting domains from config."""
    allowed = set()
    try:
        config = get_openviking_config()
        # Add configured GitHub and GitLab domains
        if hasattr(config, "code"):
            if hasattr(config.code, "github_domains"):
                allowed.update(config.code.github_domains)
            if hasattr(config.code, "gitlab_domains"):
                allowed.update(config.code.gitlab_domains)
            if hasattr(config.code, "code_hosting_domains"):
                allowed.update(config.code.code_hosting_domains)
    except Exception:
        # If config can't be loaded, use defaults
        allowed.update({"github.com", "gitlab.com", "www.github.com", "www.gitlab.com"})
    return allowed


def _is_allow_private_networks() -> bool:
    """Check if private networks are allowed by config."""
    try:
        config = get_openviking_config()
        return getattr(config, "allow_private_networks", False)
    except Exception:
        return False


def extract_remote_host(source: str) -> Optional[str]:
    """Extract the destination host from a remote resource source."""
    if source.startswith("git@"):
        rest = source[4:]
        # Find the colon separator, handling IPv6 addresses in brackets
        if "]:" in rest:
            # IPv6 address: git@[::1]:user/repo.git
            host_part = rest.split("]:", 1)[0] + "]"
        elif ":" in rest:
            # Regular hostname: git@github.com:user/repo.git
            host_part = rest.split(":", 1)[0]
        else:
            return None
        return host_part.strip().strip("[]")

    parsed = urlparse(source)
    if parsed.hostname is None:
        return None
    return parsed.hostname.strip().strip("[]")


def _normalize_host(host: str) -> str:
    return host.rstrip(".").lower()


def _resolve_host_addresses(host: str) -> set[str]:
    try:
        infos = socket.getaddrinfo(host, None, type=socket.SOCK_STREAM)
    except (socket.gaierror, UnicodeError, OSError):
        return set()

    addresses: set[str] = set()
    for family, _, _, _, sockaddr in infos:
        if family not in {socket.AF_INET, socket.AF_INET6}:
            continue
        addr = sockaddr[0]
        if "%" in addr:
            addr = addr.split("%", 1)[0]
        addresses.add(addr)
    return addresses


def _is_public_ip(address: str) -> bool:
    try:
        return ipaddress.ip_address(address).is_global
    except ValueError:
        return False


def ensure_public_remote_target(source: str) -> None:
    """Reject loopback, link-local, private, and other non-public targets.

    Skips validation if:
    - allow_private_networks is True in config
    - Host is in configured github_domains/gitlab_domains/code_hosting_domains
    """
    host = extract_remote_host(source)
    if not host:
        raise PermissionDeniedError(
            "HTTP server only accepts remote resource URLs with a valid destination host."
        )

    normalized_host = _normalize_host(host)
    if normalized_host in _LOCAL_HOSTNAMES or normalized_host.endswith(".localhost"):
        raise PermissionDeniedError(
            "HTTP server only accepts public remote resource targets; "
            "loopback, link-local, private, and otherwise non-public destinations are not allowed."
        )

    # Check if private networks are allowed globally
    if _is_allow_private_networks():
        return

    # Check if host is in allowed code hosting domains
    allowed_domains = _get_allowed_code_hosting_domains()
    normalized_domains = {_normalize_host(d) for d in allowed_domains}
    if normalized_host in normalized_domains:
        return

    resolved_addresses = _resolve_host_addresses(host)
    if not resolved_addresses:
        return

    non_public = sorted(addr for addr in resolved_addresses if not _is_public_ip(addr))
    if non_public:
        raise PermissionDeniedError(
            "HTTP server only accepts public remote resource targets; "
            f"host '{host}' resolves to non-public address '{non_public[0]}'. "
            "To allow this, add the domain to code.gitlab_domains/code.github_domains/code.code_hosting_domains "
            "or set allow_private_networks=true in your ov.conf."
        )


def build_httpx_request_validation_hooks(
    request_validator: Optional[RequestValidator],
) -> Optional[dict[str, list[Callable]]]:
    """Build httpx request hooks that validate every outbound request URL."""
    if request_validator is None:
        return None

    async def _validate_request(request) -> None:
        request_validator(str(request.url))

    return {"request": [_validate_request]}
