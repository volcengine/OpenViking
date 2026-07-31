# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0
"""Network target validation helpers for server-side remote fetches."""

from __future__ import annotations

import ipaddress
import socket
from collections.abc import Callable
from typing import Optional
from urllib.parse import urlparse

import httpx

from openviking_cli.exceptions import PermissionDeniedError
from openviking_cli.utils.config import get_openviking_config

RequestValidator = Callable[[str], Optional[str]]

_LOCAL_HOSTNAMES = {
    "localhost",
    "localhost.localdomain",
}


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


def ensure_public_remote_target(source: str) -> Optional[str]:
    """Reject loopback, link-local, private, and other non-public targets.

    Returns one validated address so guarded HTTP transports can connect to
    the exact address that passed validation instead of resolving the hostname
    a second time. Returns ``None`` only when private networks are explicitly
    enabled in configuration.
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
        return None

    resolved_addresses = _resolve_host_addresses(host)
    if not resolved_addresses:
        raise PermissionDeniedError(
            "HTTP server could not resolve the remote resource destination; "
            "the request was blocked because its network target could not be verified."
        )

    non_public = sorted(addr for addr in resolved_addresses if not _is_public_ip(addr))
    if non_public:
        raise PermissionDeniedError(
            "HTTP server only accepts public remote resource targets; "
            f"host '{host}' resolves to non-public address '{non_public[0]}'. "
            "To allow private destinations, set allow_private_networks=true in your ov.conf."
        )
    # Scrapy's threaded resolver exposes an IPv4-only interface. Prefer a
    # validated IPv4 address when the host publishes both families, while
    # retaining IPv6 for IPv6-only destinations and HTTPX callers.
    return sorted(
        resolved_addresses,
        key=lambda address: (ipaddress.ip_address(address).version, address),
    )[0]


class ValidatedAsyncHTTPTransport(httpx.AsyncBaseTransport):
    """Resolve, validate, and pin every HTTP request to the verified address."""

    def __init__(
        self,
        request_validator: RequestValidator,
        *,
        transport: Optional[httpx.AsyncBaseTransport] = None,
    ) -> None:
        self._request_validator = request_validator
        self._transport = transport
        self._origin_transports: dict[
            tuple[str, str, Optional[int], Optional[str]], httpx.AsyncBaseTransport
        ] = {}

    def _transport_for(
        self,
        url: httpx.URL,
        verified_ip: Optional[str],
    ) -> httpx.AsyncBaseTransport:
        if self._transport is not None:
            return self._transport
        key = (url.scheme, url.host, url.port, verified_ip)
        transport = self._origin_transports.get(key)
        if transport is None:
            transport = httpx.AsyncHTTPTransport()
            self._origin_transports[key] = transport
        return transport

    async def handle_async_request(self, request: httpx.Request) -> httpx.Response:
        original_url = request.url
        verified_ip = self._request_validator(str(original_url))
        transport = self._transport_for(original_url, verified_ip)
        if verified_ip is None:
            return await transport.handle_async_request(request)

        original_extensions = request.extensions
        request.url = original_url.copy_with(host=verified_ip)
        request.extensions = dict(original_extensions)
        if original_url.scheme == "https":
            request.extensions["sni_hostname"] = original_url.host
        try:
            return await transport.handle_async_request(request)
        finally:
            request.url = original_url
            request.extensions = original_extensions

    async def aclose(self) -> None:
        if self._transport is not None:
            await self._transport.aclose()
        for transport in self._origin_transports.values():
            await transport.aclose()
        self._origin_transports.clear()


def build_httpx_secure_transport(
    request_validator: Optional[RequestValidator],
) -> Optional[httpx.AsyncBaseTransport]:
    """Build a transport that pins each validated request, including redirects."""
    if request_validator is None:
        return None
    return ValidatedAsyncHTTPTransport(request_validator)


def build_httpx_request_validation_hooks(
    request_validator: Optional[RequestValidator],
) -> Optional[dict[str, list[Callable]]]:
    """Build httpx request hooks that validate every outbound request URL."""
    if request_validator is None:
        return None

    async def _validate_request(request) -> None:
        request_validator(str(request.url))

    return {"request": [_validate_request]}
