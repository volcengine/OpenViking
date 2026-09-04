# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0

"""Concurrency primitives used by the task tracker."""

import asyncio
import threading
import time
from concurrent.futures import Future as ConcurrentFuture
from concurrent.futures import InvalidStateError
from contextlib import asynccontextmanager
from dataclasses import dataclass
from typing import Any, AsyncIterator, Awaitable, Callable, Coroutine, Generic, TypeVar

from openviking_cli.utils.logger import get_logger

_KeyT = TypeVar("_KeyT")
_ResultT = TypeVar("_ResultT")

logger = get_logger(__name__)


async def run_to_completion(
    factory: Callable[[], Awaitable[_ResultT]],
) -> _ResultT:
    """Finish started work before propagating caller cancellation.

    ``asyncio.to_thread`` cancellation cannot stop the underlying synchronous
    call. Waiting for the work to settle keeps lock and concurrency accounting
    aligned with the physical I/O lifetime.
    """
    work: asyncio.Future[_ResultT] = asyncio.ensure_future(factory())
    caller = asyncio.current_task()
    cancellation: asyncio.CancelledError | None = None
    while not work.done():
        try:
            await asyncio.shield(work)
        except asyncio.CancelledError as exc:
            if work.cancelled():
                raise
            cancellation = exc
        except BaseException:
            # A completion exception can race with delivery of caller
            # cancellation. Honour the already-requested cancellation below.
            caller_with_cancellation_count: Any = caller
            if (
                caller is None
                or not hasattr(caller, "cancelling")
                or caller_with_cancellation_count.cancelling() == 0
            ):
                raise
            cancellation = asyncio.CancelledError()

    if cancellation is not None:
        if not work.cancelled():
            # Caller cancellation wins once it has been observed. Consume a
            # later work failure so asyncio does not report it as unhandled.
            work.exception()
        raise cancellation
    return work.result()


class OwnerLoopDispatcher:
    """Run coroutine factories on one event loop, including foreign callers."""

    def __init__(self) -> None:
        self._owner_loop: asyncio.AbstractEventLoop | None = None
        self._bind_lock = threading.Lock()

    def bind_current_loop(self) -> asyncio.AbstractEventLoop:
        current_loop = asyncio.get_running_loop()
        with self._bind_lock:
            if self._owner_loop is None:
                self._owner_loop = current_loop
            elif self._owner_loop is not current_loop:
                raise RuntimeError("owner event loop is already bound")
            return self._owner_loop

    async def run(
        self,
        factory: Callable[[], Coroutine[Any, Any, _ResultT]],
    ) -> _ResultT:
        current_loop = asyncio.get_running_loop()
        with self._bind_lock:
            owner_loop = self._owner_loop
            if owner_loop is None:
                self._owner_loop = current_loop
                owner_loop = current_loop

        if owner_loop.is_closed():
            raise RuntimeError("owner event loop is closed")
        if current_loop is owner_loop:
            return await factory()
        if not owner_loop.is_running():
            raise RuntimeError("owner event loop is not running")

        future: ConcurrentFuture[_ResultT] = ConcurrentFuture()
        owner_task: list[asyncio.Task[_ResultT]] = []

        def transfer_result(task: asyncio.Task[_ResultT]) -> None:
            if future.cancelled():
                if not task.cancelled():
                    task.exception()
                return
            try:
                if task.cancelled():
                    future.cancel()
                elif (error := task.exception()) is not None:
                    future.set_exception(error)
                else:
                    future.set_result(task.result())
            except InvalidStateError:
                pass

        def submit_to_owner() -> None:
            if future.cancelled():
                return
            try:
                task = owner_loop.create_task(factory())
            except BaseException as exc:
                try:
                    future.set_exception(exc)
                except InvalidStateError:
                    pass
                return
            owner_task.append(task)
            task.add_done_callback(transfer_result)

        def cancel_owner_task() -> None:
            if owner_task:
                owner_task[0].cancel()

        def propagate_cancellation(completed: ConcurrentFuture[_ResultT]) -> None:
            if not completed.cancelled():
                return
            try:
                owner_loop.call_soon_threadsafe(cancel_owner_task)
            except RuntimeError:
                pass

        future.add_done_callback(propagate_cancellation)
        try:
            owner_loop.call_soon_threadsafe(submit_to_owner)
        except BaseException:
            future.cancel()
            raise
        wrapped = asyncio.wrap_future(future)
        cancellation: asyncio.CancelledError | None = None
        while True:
            try:
                done, _ = await asyncio.wait((wrapped,), timeout=0.1)
            except asyncio.CancelledError as exc:
                cancellation = cancellation or exc
                try:
                    owner_loop.call_soon_threadsafe(cancel_owner_task)
                except RuntimeError:
                    pass
                continue
            if done:
                if cancellation is not None:
                    if not wrapped.cancelled():
                        wrapped.exception()
                    raise cancellation
                return wrapped.result()
            if not owner_loop.is_running():
                if owner_task and owner_task[0].done():
                    try:
                        return owner_task[0].result()
                    finally:
                        future.cancel()
                if future.cancel():
                    if owner_task and owner_task[0].done():
                        return owner_task[0].result()
                    raise RuntimeError("owner event loop stopped while dispatch was pending")
                return future.result()


class StoreIOLimiter:
    """Bound task-store concurrency and expose low-cardinality diagnostics."""

    def __init__(self, max_concurrent: int, slow_threshold_seconds: float = 1.0) -> None:
        if max_concurrent <= 0:
            raise ValueError("max_concurrent must be positive")
        self._semaphore = asyncio.Semaphore(max_concurrent)
        self._slow_threshold_seconds = slow_threshold_seconds
        self._inflight = 0
        self._max_observed_inflight = 0

    @property
    def inflight(self) -> int:
        return self._inflight

    @property
    def max_observed_inflight(self) -> int:
        return self._max_observed_inflight

    async def run(
        self,
        operation: str,
        factory: Callable[[], Awaitable[_ResultT]],
    ) -> _ResultT:
        started_at = time.monotonic()
        async with self._semaphore:
            self._inflight += 1
            self._max_observed_inflight = max(
                self._max_observed_inflight,
                self._inflight,
            )
            try:
                return await factory()
            finally:
                self._inflight -= 1
                duration_seconds = time.monotonic() - started_at
                if duration_seconds >= self._slow_threshold_seconds:
                    logger.warning(
                        "Slow task store operation: operation=%s duration_ms=%.1f",
                        operation,
                        duration_seconds * 1000,
                    )


@dataclass
class _LockEntry:
    lock: asyncio.Lock
    users: int = 0


class KeyedAsyncLockPool(Generic[_KeyT]):
    """Serialize work by key without retaining idle lock objects."""

    def __init__(self) -> None:
        self._entries: dict[_KeyT, _LockEntry] = {}

    @property
    def entry_count(self) -> int:
        return len(self._entries)

    @asynccontextmanager
    async def acquire(self, key: _KeyT) -> AsyncIterator[None]:
        entry = self._entries.get(key)
        if entry is None:
            entry = _LockEntry(lock=asyncio.Lock())
            self._entries[key] = entry
        entry.users += 1

        acquired = False
        try:
            await entry.lock.acquire()
            acquired = True
            yield
        finally:
            if acquired:
                entry.lock.release()
            entry.users -= 1
            if entry.users == 0 and self._entries.get(key) is entry:
                del self._entries[key]
