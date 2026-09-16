# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0
"""Local synchronization for async callers running on different threads."""

import asyncio
import threading
from collections import deque
from concurrent.futures import Future


class AsyncSemaphore:
    """Limit concurrent operations without binding them to one event loop.

    The thread lock only protects admission and handoff. Contended callers
    await a future on their own loop; no executor thread is occupied waiting.
    """

    def __init__(self, value: int = 1) -> None:
        if value < 0:
            raise ValueError("Semaphore initial value must be >= 0")
        self._value = value
        self._lock = threading.Lock()
        self._waiters: deque[Future[None]] = deque()

    async def acquire(self) -> None:
        with self._lock:
            if self._value:
                self._value -= 1
                return
            waiter: Future[None] = Future()
            self._waiters.append(waiter)
        try:
            await asyncio.wrap_future(waiter)
        except BaseException:
            with self._lock:
                try:
                    self._waiters.remove(waiter)
                except ValueError:
                    # A granted slot must be returned even if cancellation
                    # reached this loop before the grant callback did.
                    granted = not waiter.cancelled()
                else:
                    granted = False
            if granted:
                self.release()
            raise

    def release(self) -> None:
        with self._lock:
            while self._waiters:
                waiter = self._waiters.popleft()
                if waiter.set_running_or_notify_cancel():
                    waiter.set_result(None)
                    return
            self._value += 1

    async def __aenter__(self) -> "AsyncSemaphore":
        await self.acquire()
        return self

    async def __aexit__(self, *exc: object) -> None:
        self.release()
