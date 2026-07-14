# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0
"""Request-scoped resolution for dynamically configured outbound headers."""

from __future__ import annotations

import re
from collections.abc import Iterator, Mapping
from contextlib import contextmanager
from contextvars import ContextVar
from types import MappingProxyType
from typing import Optional

_REQUEST_HEADER_MARKER = re.compile(r"^@request\.header\.([!#$%&'*+\-.^_`|~0-9A-Za-z]+)$")


class _RequestHeadersState:
    __slots__ = ("headers", "active")

    def __init__(self, headers: Mapping[str, str]) -> None:
        self.headers = headers
        self.active = True

    def expire(self) -> None:
        self.headers = MappingProxyType({})
        self.active = False


_REQUEST_HEADERS: ContextVar[Optional[_RequestHeadersState]] = ContextVar(
    "openviking_request_headers",
    default=None,
)


def _request_header_source(value: str) -> Optional[str]:
    match = _REQUEST_HEADER_MARKER.fullmatch(value)
    return match.group(1) if match else None


def get_static_extra_headers(configured: Optional[Mapping[str, str]]) -> dict[str, str]:
    """Return configured headers whose values are not dynamic markers."""
    if not configured:
        return {}
    return {
        target: value
        for target, value in configured.items()
        if _request_header_source(value) is None
    }


def resolve_dynamic_extra_headers(
    configured: Optional[Mapping[str, str]],
) -> dict[str, str]:
    """Resolve only dynamic headers from the active request context."""
    snapshot = get_request_headers_snapshot()
    if not configured or snapshot is None:
        return {}

    resolved: dict[str, str] = {}
    for target, value in configured.items():
        source = _request_header_source(value)
        if source is not None:
            resolved[target] = snapshot.get(source.lower(), "")
    return resolved


def resolve_extra_headers(configured: Optional[Mapping[str, str]]) -> dict[str, str]:
    """Return static headers plus dynamic headers resolved for the active request."""
    return {
        **get_static_extra_headers(configured),
        **resolve_dynamic_extra_headers(configured),
    }


@contextmanager
def bind_request_headers(headers: Mapping[str, str]) -> Iterator[None]:
    """Bind a lowercase immutable copy of headers for the current context."""
    snapshot = MappingProxyType({name.lower(): value for name, value in headers.items()})
    state = _RequestHeadersState(snapshot)
    token = _REQUEST_HEADERS.set(state)
    try:
        yield
    finally:
        state.expire()
        _REQUEST_HEADERS.reset(token)


def get_request_headers_snapshot() -> Optional[Mapping[str, str]]:
    """Return the current immutable request-header snapshot, if one is bound."""
    state = _REQUEST_HEADERS.get()
    if state is None or not state.active:
        return None
    return state.headers
