# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0
"""Account-isolated cache adapter for lark-oapi tenant tokens."""

from __future__ import annotations

import hashlib
import threading
import time
from contextlib import contextmanager
from contextvars import ContextVar
from typing import Generator

from lark_oapi.core.cache import ICache

_SDK_TOKEN_MANAGER_LOCK = threading.RLock()


class InMemoryFeishuTokenCache(ICache):
    """Thread-safe default storage for namespaced tenant tokens."""

    def __init__(self) -> None:
        self._entries: dict[str, tuple[str, int]] = {}
        self._lock = threading.Lock()

    def get(self, key: str) -> str | None:
        with self._lock:
            entry = self._entries.get(key)
            if entry is None:
                return None
            token, expire = entry
            if expire <= time.time():
                del self._entries[key]
                return None
            return token

    def set(self, key: str, value: str, expire: int) -> None:
        with self._lock:
            self._entries[key] = (value, expire)


class FeishuTenantTokenCache(ICache):
    """lark-oapi ``ICache`` adapter with credential-scoped keys.

    lark-oapi only passes ``self_tenant_token:{app_id}`` to ``ICache`` and
    stores one process-global cache instance. Callers bind the active
    credential namespace around SDK calls, so one adapter safely serves all
    Accounts without exposing a raw secret in the storage key.
    """

    def __init__(self, backend: ICache | None = None) -> None:
        self._backend = backend if backend is not None else InMemoryFeishuTokenCache()
        self._namespace: ContextVar[str | None] = ContextVar(
            "feishu_tenant_token_namespace",
            default=None,
        )

    @contextmanager
    def bind(
        self,
        *,
        app_id: str,
        app_secret: str,
        domain: str,
    ) -> Generator[None, None, None]:
        material = "\0".join((domain.rstrip("/"), app_id, app_secret)).encode("utf-8")
        token = self._namespace.set(hashlib.sha256(material).hexdigest())
        try:
            yield
        finally:
            self._namespace.reset(token)

    @contextmanager
    def sdk_scope(
        self,
        *,
        app_id: str,
        app_secret: str,
        domain: str,
    ) -> Generator[None, None, None]:
        """Install this adapter while lark-oapi obtains a tenant token.

        lark-oapi 1.5 stores its cache in the process-global
        ``TokenManager.cache`` rather than on ``Config``. Keep this adapter
        installed so concurrent SDK calls share its ContextVar isolation
        without serializing their network requests.
        """
        from lark_oapi.core.token import TokenManager

        with self.bind(
            app_id=app_id,
            app_secret=app_secret,
            domain=domain,
        ):
            with _SDK_TOKEN_MANAGER_LOCK:
                TokenManager.cache = self
            yield

    def get(self, key: str) -> str | None:
        namespace = self._namespace.get()
        return self._backend.get(f"{namespace}:{key}") if namespace is not None else None

    def set(self, key: str, value: str, expire: int) -> None:
        namespace = self._namespace.get()
        if namespace is not None:
            self._backend.set(f"{namespace}:{key}", value, expire)


_DEFAULT_TENANT_TOKEN_CACHE = FeishuTenantTokenCache()


def default_feishu_tenant_token_cache() -> FeishuTenantTokenCache:
    """Return the process-shared, credential-scoped SDK cache adapter."""
    return _DEFAULT_TENANT_TOKEN_CACHE


def resolve_feishu_tenant_token_cache(
    cache: ICache | FeishuTenantTokenCache | None,
) -> FeishuTenantTokenCache:
    """Return an adapter, using a supplied SDK cache as its backend."""
    if cache is None:
        return default_feishu_tenant_token_cache()
    if isinstance(cache, FeishuTenantTokenCache):
        return cache
    return FeishuTenantTokenCache(cache)
