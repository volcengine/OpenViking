# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0
"""Local synchronization for async callers running on different threads."""

import asyncio
import threading
from collections import deque
from collections.abc import Awaitable, Callable, Iterable
from concurrent.futures import Future
from typing import TypeVar

T = TypeVar("T")
R = TypeVar("R")


async def bounded_map(
    items: Iterable[T],
    worker: Callable[[T], Awaitable[R]],
    *,
    concurrency: int,
) -> list[R]:
    """Apply ``worker`` with a fixed number of tasks and preserve input order.

    A failed worker prevents new items from being claimed, then all active
    workers are joined before the original failure is re-raised.
    """
    if concurrency <= 0:
        raise ValueError("concurrency must be positive")

    iterator = iter(enumerate(items))
    cursor_lock = asyncio.Lock()
    stop = asyncio.Event()
    results: dict[int, R] = {}

    async def next_item() -> tuple[int, T] | None:
        async with cursor_lock:
            if stop.is_set():
                return None
            try:
                return next(iterator)
            except StopIteration:
                return None

    async def run_worker() -> None:
        while item := await next_item():
            index, value = item
            try:
                results[index] = await worker(value)
            except BaseException:
                stop.set()
                raise

    workers = [asyncio.create_task(run_worker()) for _ in range(concurrency)]
    try:
        await asyncio.gather(*workers)
    except BaseException:
        stop.set()
        await asyncio.gather(*workers, return_exceptions=True)
        raise
    return [results[index] for index in range(len(results))]


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
