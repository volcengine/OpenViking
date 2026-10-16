# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: Apache-2.0
"""Pluggable network clients for model providers.

The public OpenViking package owns only this neutral extension point. Internal
distributions may register an adapter for additional URL schemes without
making upstream model providers depend on private networking packages.
"""

from __future__ import annotations

from threading import RLock
from typing import Any, Mapping, Protocol, Sequence
from urllib.parse import urlparse

import httpx
import requests
from requests.adapters import HTTPAdapter

EventHooks = Mapping[str, Sequence[Any]]


class ModelNetworkAdapter(Protocol):
    """Adapter implemented by a distribution-specific model network layer."""

    def supports(self, url: str) -> bool: ...

    def create_sync_httpx_client(
        self,
        url: str,
        *,
        client_cls: type[httpx.Client],
        event_hooks: EventHooks | None = None,
        **client_kwargs: Any,
    ) -> httpx.Client: ...

    def create_async_httpx_client(
        self,
        url: str,
        *,
        client_cls: type[httpx.AsyncClient],
        event_hooks: EventHooks | None = None,
        **client_kwargs: Any,
    ) -> httpx.AsyncClient: ...

    def create_requests_session(
        self,
        url: str,
        *,
        retry: Any = None,
        mount_retry: bool = True,
    ) -> requests.Session: ...


_SERVICE_DISCOVERY_SCHEMES = frozenset({"http+sd", "https+sd"})
_adapter: ModelNetworkAdapter | None = None
_lock = RLock()


def register_model_network_adapter(adapter: ModelNetworkAdapter) -> None:
    """Register the process-wide model network adapter.

    Registering the same object repeatedly is idempotent. Replacing an existing
    adapter is rejected so unrelated extensions cannot silently take ownership.
    """

    global _adapter
    with _lock:
        if _adapter is adapter:
            return
        if _adapter is not None:
            raise RuntimeError("A model network adapter is already registered")
        _adapter = adapter


def reset_model_network_adapter() -> None:
    """Reset the adapter. Intended for tests and runtime reinitialization."""

    global _adapter
    with _lock:
        _adapter = None


def is_model_network_endpoint(url: str | None) -> bool:
    """Return whether *url* explicitly requests an extended model scheme."""

    if not url:
        return False
    return urlparse(str(url)).scheme.lower() in _SERVICE_DISCOVERY_SCHEMES


def _adapter_for(url: str) -> ModelNetworkAdapter | None:
    with _lock:
        adapter = _adapter
    if adapter is not None and adapter.supports(url):
        return adapter
    if is_model_network_endpoint(url):
        if adapter is None:
            raise RuntimeError(
                "Model service discovery URL requires a registered model network adapter"
            )
        raise RuntimeError(
            f"Registered model network adapter does not support URL scheme "
            f"'{urlparse(url).scheme}'"
        )
    return None


def create_optional_sync_httpx_client(
    url: str | None,
    *,
    client_cls: type[httpx.Client] = httpx.Client,
    event_hooks: EventHooks | None = None,
    **client_kwargs: Any,
) -> httpx.Client | None:
    """Create an injected sync client only for an extended model endpoint."""

    if not url:
        return None
    adapter = _adapter_for(url)
    if adapter is None:
        return None
    return adapter.create_sync_httpx_client(
        url,
        client_cls=client_cls,
        event_hooks=event_hooks,
        **client_kwargs,
    )


def create_optional_async_httpx_client(
    url: str | None,
    *,
    client_cls: type[httpx.AsyncClient] = httpx.AsyncClient,
    event_hooks: EventHooks | None = None,
    **client_kwargs: Any,
) -> httpx.AsyncClient | None:
    """Create an injected async client only for an extended model endpoint."""

    if not url:
        return None
    adapter = _adapter_for(url)
    if adapter is None:
        return None
    return adapter.create_async_httpx_client(
        url,
        client_cls=client_cls,
        event_hooks=event_hooks,
        **client_kwargs,
    )


def create_model_requests_session(
    url: str,
    *,
    retry: Any = None,
    mount_retry: bool = True,
) -> requests.Session:
    """Create a requests session suitable for the model endpoint."""

    adapter = _adapter_for(url)
    if adapter is not None:
        return adapter.create_requests_session(
            url,
            retry=retry,
            mount_retry=mount_retry,
        )

    session = requests.Session()
    if mount_retry and retry is not None:
        http_adapter = HTTPAdapter(max_retries=retry)
        session.mount("http://", http_adapter)
        session.mount("https://", http_adapter)
    return session
