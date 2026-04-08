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

RequestValidator = Callable[[str], None]

_LOCAL_HOSTNAMES = {
    "localhost",
    "localhost.localdomain",
}


def extract_remote_host(source: str) -> Optional[str]:
    """Extract the destination host from a remote resource source."""
    if source.startswith("git@"):
        rest = source[4:]
        if ":" not in rest:
            return None
        return rest.split(":", 1)[0].strip().strip("[]")

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
    """Reject loopback, link-local, private, and other non-public targets."""
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

    resolved_addresses = _resolve_host_addresses(host)
    if not resolved_addresses:
        return

    non_public = sorted(addr for addr in resolved_addresses if not _is_public_ip(addr))
    if non_public:
        raise PermissionDeniedError(
            "HTTP server only accepts public remote resource targets; "
            f"host '{host}' resolves to non-public address '{non_public[0]}'."
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
