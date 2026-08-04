# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0
"""DNS resolver that reuses addresses approved by the crawler's SSRF guard."""

from __future__ import annotations

import ipaddress
from collections.abc import Sequence
from typing import Any

from scrapy.resolver import CachingThreadedResolver
from twisted.internet import defer

from openviking.utils.network_guard import (
    RequestValidationResult,
    normalize_verified_addresses,
)

_VERIFIED_ADDRESSES: dict[str, str] = {}


def _normalize_host(host: str) -> str:
    return host.rstrip(".").lower()


def pin_verified_address(host: str, addresses: RequestValidationResult) -> None:
    """Pin a hostname to one address approved by the request validator."""
    normalized_addresses = normalize_verified_addresses(addresses)
    if normalized_addresses is None:
        return

    # Scrapy's threaded resolver exposes a single-address, IPv4-oriented
    # interface. Prefer an approved IPv4 address and retain IPv6-only support.
    selected_address = next(
        (address for address in normalized_addresses if ipaddress.ip_address(address).version == 4),
        normalized_addresses[0],
    )
    _VERIFIED_ADDRESSES[_normalize_host(host)] = selected_address


class ValidatedAddressResolver(CachingThreadedResolver):
    """Resolve guarded crawler requests to their previously validated address."""

    def getHostByName(self, name: str, timeout: Sequence[int] = ()):
        verified_address = _VERIFIED_ADDRESSES.get(_normalize_host(name))
        if verified_address is not None:
            return defer.succeed(verified_address)
        return super().getHostByName(name, timeout)

    def _cache_result(self, result: Any, name: str) -> Any:
        # A validated address always takes precedence over Scrapy's regular DNS
        # cache, so a later lookup cannot replace the pinned destination.
        verified_address = _VERIFIED_ADDRESSES.get(_normalize_host(name))
        if verified_address is not None:
            return verified_address
        return super()._cache_result(result, name)
