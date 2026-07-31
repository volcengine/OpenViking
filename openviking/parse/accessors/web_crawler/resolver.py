# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0
"""DNS resolver that reuses addresses approved by the crawler's SSRF guard."""

from __future__ import annotations

from collections.abc import Sequence
from typing import Any

from scrapy.resolver import CachingThreadedResolver
from twisted.internet import defer

_VERIFIED_ADDRESSES: dict[str, str] = {}


def _normalize_host(host: str) -> str:
    return host.rstrip(".").lower()


def pin_verified_address(host: str, address: str) -> None:
    """Pin a hostname to the address returned by the request validator."""
    _VERIFIED_ADDRESSES[_normalize_host(host)] = address


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
