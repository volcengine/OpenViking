# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0
"""Network target validation helpers for server-side remote fetches."""

from __future__ import annotations

import ipaddress
import socket
from collections.abc import Callable
from typing import Optional
from urllib.parse import urlparse

from openviking.utils.code_hosting_utils import get_configured_code_hosting_domains
from openviking_cli.exceptions import PermissionDeniedError
from openviking_cli.utils.config import get_openviking_config
from openviking_cli.utils.config.parser_config import CodeHostingConfig

RequestValidator = Callable[[str], None]

_LOCAL_HOSTNAMES = {
    "localhost",
    "localhost.localdomain",
}
# Alibaba ECS and EC2 IPv6 IMDS are outside the link-local address ranges.
_METADATA_ADDRESSES = {
    ipaddress.ip_address("100.100.100.200"),
    ipaddress.ip_address("fd00:ec2::254"),
}


def _get_allowed_code_hosting_domains() -> set[str]:
    """Get allowed code hosting domains from config."""
    try:
        config = get_openviking_config()
        if hasattr(config, "code"):
            return get_configured_code_hosting_domains(config.code)
    except Exception:
        pass

    # Derive the fallback from the config object instead of maintaining a
    # second hard-coded allowlist in the network layer.
    return get_configured_code_hosting_domains(CodeHostingConfig())


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
    """Reject unsafe destinations, including on configured code hosts.

    Configured code hosts and allow_private_networks permit private networks,
    but never loopback, link-local, unspecified, multicast or metadata targets.
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

    resolved_addresses = _resolve_host_addresses(host)
    if not resolved_addresses:
        raise PermissionDeniedError(
            "HTTP server only accepts public remote resource targets; "
            f"host '{host}' could not be resolved to a verifiable public address."
        )

    for address in sorted(resolved_addresses):
        ip = ipaddress.ip_address(address)
        if isinstance(ip, ipaddress.IPv6Address) and ip.ipv4_mapped is not None:
            ip = ip.ipv4_mapped
        if (
            ip.is_loopback
            or ip.is_link_local
            or ip.is_unspecified
            or ip.is_multicast
            or ip.is_reserved
            or ip in _METADATA_ADDRESSES
        ):
            raise PermissionDeniedError(
                "HTTP server rejects unsafe non-public address "
                f"'{address}' resolved from host '{host}'; "
                "code hosting domain lists and allow_private_networks cannot override this restriction."
            )

    allowed_domains = _get_allowed_code_hosting_domains()
    if _is_allow_private_networks() or normalized_host in {
        _normalize_host(d) for d in allowed_domains
    }:
        return

    non_public = sorted(addr for addr in resolved_addresses if not _is_public_ip(addr))
    if non_public:
        raise PermissionDeniedError(
            "HTTP server only accepts public remote resource targets; "
            f"host '{host}' resolves to non-public address '{non_public[0]}'. "
            "To allow this, add the domain to its code.<platform>_domains list "
            "or code.code_hosting_domains "
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
