# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0
"""Event-loop scoped cache for reusable async clients."""

from __future__ import annotations

import asyncio
import inspect
import threading
import weakref
from collections.abc import Callable
from typing import Any, Protocol


class Closeable(Protocol):
    def close(self) -> Any: ...


class AsyncCloseable(Protocol):
    def aclose(self) -> Any: ...


def _close_client(client: Closeable) -> Any:
    return client.close()


def _aclose_client(client: AsyncCloseable) -> Any:
    return client.aclose()


class LoopScopedAsyncClientCache:
    """Cache async clients per running event loop.

    Clients such as httpx.AsyncClient and OpenAI AsyncOpenAI can bind internal
    asyncio primitives to the loop that first uses them. Sharing one client
    across worker threads with separate event loops can then fail at runtime.
    """

    def __init__(self) -> None:
        self._clients_by_loop: weakref.WeakKeyDictionary[asyncio.AbstractEventLoop, Any] = (
            weakref.WeakKeyDictionary()
        )
        self._fallback_client: Any = None
        self._lock = threading.Lock()

    def get(self, factory: Callable[[], Any]) -> Any:
        """Return a client for the current loop, creating it with factory if needed."""
        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:
            with self._lock:
                if self._fallback_client is None:
                    self._fallback_client = factory()
                return self._fallback_client

        with self._lock:
            client = self._clients_by_loop.get(loop)
            if client is None:
                client = factory()
                self._clients_by_loop[loop] = client
            return client

    def pop_all(self) -> list[Any]:
        """Remove and return all cached clients."""
        with self._lock:
            clients = list(self._clients_by_loop.values())
            self._clients_by_loop.clear()
            if self._fallback_client is not None:
                clients.append(self._fallback_client)
                self._fallback_client = None
            return clients

    @staticmethod
    async def _close_clients(clients: list[Any], close_client: Callable[[Any], Any]) -> None:
        seen: set[int] = set()
        for client in clients:
            client_id = id(client)
            if client_id in seen:
                continue
            seen.add(client_id)

            result = close_client(client)
            if inspect.isawaitable(result):
                await result

    def close_all(self, close_client: Callable[[Any], Any]) -> None:
        """Close and clear all cached clients on a best-effort basis."""
        clients = self.pop_all()
        if not clients:
            return

        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:
            loop = None

        if loop and loop.is_running():
            loop.create_task(self._close_clients(clients, close_client))
        else:
            asyncio.run(self._close_clients(clients, close_client))

    def close_all_with_close(self) -> None:
        self.close_all(_close_client)

    def close_all_with_aclose(self) -> None:
        self.close_all(_aclose_client)
