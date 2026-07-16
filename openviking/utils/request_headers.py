# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0
"""Request-scoped resolution for dynamically configured outbound headers."""

from __future__ import annotations

import asyncio
import re
from collections.abc import Awaitable, Iterator, Mapping, Sequence
from contextlib import contextmanager
from contextvars import ContextVar
from types import MappingProxyType
from typing import Optional, TypeVar

_REQUEST_HEADER_MARKER = re.compile(r"^@request\.header\.([!#$%&'*+\-.^_`|~0-9A-Za-z]+)$")
MODEL_REQUEST_CONTEXT_KEY = "_openviking_model_request_context"

_T = TypeVar("_T")


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
def bind_request_headers(
    headers: Mapping[str, str],
    *,
    source_names: Optional[set[str]] = None,
) -> Iterator[None]:
    """Bind a lowercase immutable copy of headers for the current context."""
    allowed = {name.lower() for name in source_names} if source_names is not None else None
    snapshot = MappingProxyType(
        {
            name.lower(): value
            for name, value in headers.items()
            if allowed is None or name.lower() in allowed
        }
    )
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


def capture_request_headers() -> Optional[dict[str, str]]:
    """Copy the active request headers for an explicit async handoff."""
    snapshot = get_request_headers_snapshot()
    return None if snapshot is None else dict(snapshot)


@contextmanager
def bind_captured_request_headers(
    headers: Optional[Mapping[str, str]],
) -> Iterator[None]:
    """Bind a captured request context, preserving None as no request."""
    if headers is None:
        token = _REQUEST_HEADERS.set(None)
        try:
            yield
        finally:
            _REQUEST_HEADERS.reset(token)
        return
    with bind_request_headers(headers):
        yield


def create_task_with_request_headers(coro: Awaitable[_T]) -> asyncio.Task[_T]:
    """Create a task whose request-header snapshot lasts until the task ends."""
    captured = capture_request_headers()

    async def run() -> _T:
        with bind_captured_request_headers(captured):
            return await coro

    return asyncio.create_task(run())


def collect_dynamic_request_header_names(*configs: object) -> set[str]:
    """Collect source header names referenced by nested model configurations."""
    names: set[str] = set()
    visited: set[int] = set()

    def visit(value: object) -> None:
        if value is None or isinstance(value, (str, bytes, int, float, bool)):
            return
        value_id = id(value)
        if value_id in visited:
            return
        visited.add(value_id)

        model_dump = getattr(value, "model_dump", None)
        if callable(model_dump):
            visit(model_dump(mode="python"))
            return
        if isinstance(value, Mapping):
            extra_headers = value.get("extra_headers")
            if isinstance(extra_headers, Mapping):
                for configured_value in extra_headers.values():
                    if not isinstance(configured_value, str):
                        continue
                    source = _request_header_source(configured_value)
                    if source is not None:
                        names.add(source)
            for nested in value.values():
                visit(nested)
            return
        if isinstance(value, Sequence):
            for nested in value:
                visit(nested)

    for config in configs:
        visit(config)
    return names
