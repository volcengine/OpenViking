# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0
"""Stable network extension point for parser resource acquisition."""

from __future__ import annotations

import os
import urllib.request
from dataclasses import dataclass
from enum import Enum
from threading import RLock
from typing import Any, Mapping, Protocol

import httpx


class ParserNetworkOperation(str, Enum):
    """Network operations whose routing may be extended by a distribution."""

    HTTP_RESOURCE = "http_resource"
    GIT_ARCHIVE = "git_archive"
    GIT_COMMAND = "git_command"
    WEB_CRAWL = "web_crawl"


@dataclass(frozen=True)
class ParserNetworkContext:
    """Strongly typed description of one parser network boundary."""

    operation: ParserNetworkOperation
    source_url: str

    def __post_init__(self) -> None:
        if not isinstance(self.operation, ParserNetworkOperation):
            raise TypeError("operation must be a ParserNetworkOperation")
        if not isinstance(self.source_url, str) or not self.source_url.strip():
            raise ValueError("source_url must be a non-empty string")


class ParserNetworkAdapter(Protocol):
    """Distribution-owned routing hooks for parser network operations."""

    def supports(self, context: ParserNetworkContext) -> bool: ...

    def create_httpx_transport(
        self,
        context: ParserNetworkContext,
    ) -> httpx.AsyncBaseTransport | None: ...

    def create_urllib_opener(
        self,
        context: ParserNetworkContext,
    ) -> urllib.request.OpenerDirector | None: ...

    def subprocess_env_overlay(
        self,
        context: ParserNetworkContext,
    ) -> Mapping[str, str]: ...


_adapter: ParserNetworkAdapter | None = None
_lock = RLock()


def register_parser_network_adapter(adapter: ParserNetworkAdapter) -> None:
    """Register one process-wide parser network adapter.

    Re-registering the same object is idempotent. Replacing it is rejected so
    unrelated extensions cannot silently take ownership of parser egress.
    """

    global _adapter
    with _lock:
        if _adapter is adapter:
            return
        if _adapter is not None:
            raise RuntimeError("A parser network adapter is already registered")
        _adapter = adapter


def reset_parser_network_adapter() -> None:
    """Reset the adapter. Intended for tests and runtime reinitialization."""

    global _adapter
    with _lock:
        _adapter = None


def _adapter_for(context: ParserNetworkContext) -> ParserNetworkAdapter | None:
    with _lock:
        adapter = _adapter
    if adapter is not None and adapter.supports(context):
        return adapter
    return None


def create_parser_httpx_client(
    context: ParserNetworkContext,
    *,
    client_cls: type[httpx.AsyncClient] = httpx.AsyncClient,
    **client_kwargs: Any,
) -> httpx.AsyncClient:
    """Create a parser HTTP client while preserving caller-owned safeguards."""

    adapter = _adapter_for(context)
    if adapter is not None:
        transport = adapter.create_httpx_transport(context)
        if transport is not None:
            if client_kwargs.get("transport") is not None:
                raise RuntimeError("Parser network adapter cannot replace an explicit transport")
            client_kwargs["transport"] = transport
            # The adapter transport owns proxy/direct routing. Environment
            # lookup here would reintroduce process-wide proxy side effects.
            client_kwargs["trust_env"] = False
    return client_cls(**client_kwargs)


def open_parser_url(
    context: ParserNetworkContext,
    request: urllib.request.Request | str,
    **open_kwargs: Any,
):
    """Open one parser URL through an adapter opener when available."""

    adapter = _adapter_for(context)
    if adapter is not None:
        opener = adapter.create_urllib_opener(context)
        if opener is not None:
            return opener.open(request, **open_kwargs)
    return urllib.request.urlopen(request, **open_kwargs)


def overlay_parser_subprocess_env(
    context: ParserNetworkContext,
    base_env: Mapping[str, str] | None,
) -> dict[str, str] | None:
    """Merge adapter variables into a child environment without mutating it."""

    adapter = _adapter_for(context)
    if adapter is None:
        return base_env if isinstance(base_env, dict) or base_env is None else dict(base_env)

    overlay = dict(adapter.subprocess_env_overlay(context))
    if not overlay:
        return base_env if isinstance(base_env, dict) or base_env is None else dict(base_env)
    if not all(isinstance(key, str) and isinstance(value, str) for key, value in overlay.items()):
        raise TypeError("Parser subprocess environment overlay must contain string pairs")

    merged = dict(os.environ if base_env is None else base_env)
    merged.update(overlay)
    return merged


__all__ = [
    "ParserNetworkAdapter",
    "ParserNetworkContext",
    "ParserNetworkOperation",
    "create_parser_httpx_client",
    "open_parser_url",
    "overlay_parser_subprocess_env",
    "register_parser_network_adapter",
    "reset_parser_network_adapter",
]
