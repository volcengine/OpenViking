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
from urllib.parse import urlparse

import httpx

from openviking.utils.git_auth import append_git_process_config
from openviking.utils.network_guard import RequestValidator


class _ValidatedRedirectHandler(urllib.request.HTTPRedirectHandler):
    """Validate archive redirects before urllib sends the next request."""

    handler_order = 100  # Run before an adapter opener's default redirect handler.

    def __init__(self, request_validator: RequestValidator):
        self.request_validator = request_validator

    def redirect_request(self, req, fp, code, msg, headers, newurl):
        try:
            self.request_validator(newurl)
        except Exception:
            fp.close()
            raise
        redirected = super().redirect_request(req, fp, code, msg, headers, newurl)
        old_origin = urlparse(req.full_url)
        new_origin = urlparse(newurl)
        if redirected is not None and (old_origin.scheme, old_origin.netloc) != (
            new_origin.scheme,
            new_origin.netloc,
        ):
            redirected.remove_header("Authorization")
        return redirected


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
    *,
    request_validator: RequestValidator | None = None,
    **open_kwargs: Any,
):
    """Open a parser URL, validating the initial target and every redirect."""

    adapter = _adapter_for(context)
    opener = adapter.create_urllib_opener(context) if adapter is not None else None
    if request_validator is not None:
        source = request.full_url if isinstance(request, urllib.request.Request) else request
        request_validator(source)
        handler = _ValidatedRedirectHandler(request_validator)
        if opener is None:
            opener = urllib.request.build_opener(handler)
        else:
            opener.add_handler(handler)
    if opener is not None:
        return opener.open(request, **open_kwargs)
    return urllib.request.urlopen(request, **open_kwargs)


def overlay_parser_subprocess_env(
    context: ParserNetworkContext,
    base_env: Mapping[str, str] | None,
    *,
    request_validator: RequestValidator | None = None,
) -> dict[str, str] | None:
    """Apply parser routing and caller-owned Git safeguards to a child environment."""

    adapter = _adapter_for(context)
    overlay = dict(adapter.subprocess_env_overlay(context)) if adapter is not None else {}
    if not all(isinstance(key, str) and isinstance(value, str) for key, value in overlay.items()):
        raise TypeError("Parser subprocess environment overlay must contain string pairs")

    if request_validator is None and not overlay:
        return base_env if isinstance(base_env, dict) or base_env is None else dict(base_env)
    merged = dict(os.environ if base_env is None else base_env)
    merged.update(overlay)
    if request_validator is not None:
        request_validator(context.source_url)
        # Git cannot invoke a Python validator on redirects. Refuse them at the
        # same URL-specific precedence as credentials, including inherited config.
        append_git_process_config(merged, f"http.{context.source_url}.followRedirects", "false")
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
