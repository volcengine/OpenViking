# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0
"""Shared event-loop runtime for asyncio Redis account-store clients."""

from __future__ import annotations

import asyncio
import threading
from collections.abc import Awaitable, Callable
from concurrent.futures import Future
from threading import Event, Thread
from typing import TypeVar

T = TypeVar("T")
_Operation = Callable[[], Awaitable[T]]


async def wait_redis_cleanup(operation: Awaitable[T]) -> T:
    """Wait for Redis cleanup to finish before propagating cancellation."""
    cleanup = asyncio.ensure_future(operation)
    try:
        return await asyncio.shield(cleanup)
    except asyncio.CancelledError:
        while not cleanup.done():
            try:
                await asyncio.shield(cleanup)
            except asyncio.CancelledError:
                continue
            except BaseException:
                break
        cleanup.result()
        raise


class RedisRuntime:
    """Run Redis account-store operations on one private event loop."""

    def __init__(self) -> None:
        self._state_lock = threading.Lock()
        self._loop: asyncio.AbstractEventLoop | None = None
        self._thread: Thread | None = None
        self._ready: Event | None = None
        self._stopped: Event | None = None
        self._stopping = False
        self._leases = 0
        self._operations = 0

    async def run(self, operation: _Operation[T]) -> T:
        """Run an operation, including cancellation cleanup, on the private loop."""
        caller_loop = asyncio.get_running_loop()
        with self._state_lock:
            self._operations += 1
            on_runtime = caller_loop is self._loop
        if on_runtime:
            try:
                return await operation()
            finally:
                self._operation_finished()

        result: Future[T] = Future()
        task: asyncio.Task[T] | None = None
        cancel_requested = False

        def finished(completed: asyncio.Task[T]) -> None:
            self._operation_finished()
            try:
                result.set_result(completed.result())
            except BaseException as exc:
                result.set_exception(exc)

        def start() -> None:
            nonlocal task
            try:
                task = loop.create_task(self._invoke(operation))
            except BaseException as exc:
                self._operation_finished()
                result.set_exception(exc)
                return
            task.add_done_callback(finished)
            if cancel_requested:
                task.cancel()

        def cancel() -> None:
            nonlocal cancel_requested
            cancel_requested = True
            if task is not None and not task.done():
                task.cancel()

        try:
            while True:
                loop = await self._ensure_loop()
                with self._state_lock:
                    if loop is self._loop and not self._stopping:
                        loop.call_soon_threadsafe(start)
                        break
        except BaseException:
            self._operation_finished()
            raise

        wrapped = asyncio.wrap_future(result)
        try:
            return await asyncio.shield(wrapped)
        except asyncio.CancelledError:
            with self._state_lock:
                if loop is self._loop and not self._stopping:
                    loop.call_soon_threadsafe(cancel)
            while not wrapped.done():
                try:
                    await asyncio.shield(wrapped)
                except asyncio.CancelledError:
                    continue
                except BaseException:
                    break
            try:
                wrapped.result()
            except BaseException:
                pass
            raise

    async def acquire(self) -> None:
        """Keep the private loop alive for one loaded account store."""
        await self.run(self._acquire_on_loop)

    async def release(self) -> None:
        """Release one store and stop the loop after admitted operations finish."""
        await self.run(self._release_on_loop)

    async def _invoke(self, operation: _Operation[T]) -> T:
        return await operation()

    async def _acquire_on_loop(self) -> None:
        with self._state_lock:
            self._leases += 1

    async def _release_on_loop(self) -> None:
        with self._state_lock:
            if self._leases <= 0:
                raise RuntimeError("RedisRuntime lease underflow")
            self._leases -= 1

    def _operation_finished(self) -> None:
        with self._state_lock:
            self._operations -= 1
            self._stop_if_idle_locked()

    def _stop_if_idle_locked(self) -> None:
        if self._leases or self._operations or self._loop is None or self._stopping:
            return
        self._stopping = True
        self._loop.call_soon_threadsafe(self._loop.stop)

    async def _ensure_loop(self) -> asyncio.AbstractEventLoop:
        while True:
            wait_ready: Event | None = None
            wait_stopped: Event | None = None
            with self._state_lock:
                if self._loop is not None and self._loop.is_running() and not self._stopping:
                    return self._loop
                if self._stopping:
                    wait_stopped = self._stopped
                elif self._thread is not None:
                    wait_ready = self._ready
                else:
                    ready = Event()
                    stopped = Event()
                    thread = Thread(
                        target=self._thread_main,
                        args=(ready, stopped),
                        name="openviking-redis",
                        daemon=True,
                    )
                    self._ready = ready
                    self._stopped = stopped
                    self._thread = thread
                    thread.start()
                    wait_ready = ready

            if wait_stopped is not None:
                await asyncio.to_thread(wait_stopped.wait)
            elif wait_ready is not None:
                await asyncio.to_thread(wait_ready.wait)

    def _thread_main(self, ready: Event, stopped: Event) -> None:
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        with self._state_lock:
            self._loop = loop
            self._stop_if_idle_locked()
        loop.call_soon(ready.set)
        try:
            loop.run_forever()
        finally:
            pending = asyncio.all_tasks(loop)
            for task in pending:
                task.cancel()
            if pending:
                loop.run_until_complete(asyncio.gather(*pending, return_exceptions=True))
            loop.run_until_complete(loop.shutdown_asyncgens())
            loop.close()
            with self._state_lock:
                if self._loop is loop:
                    self._loop = None
                    self._thread = None
                    self._ready = None
                    self._stopped = None
                    self._stopping = False
            stopped.set()


_shared_runtime: RedisRuntime | None = None
_shared_runtime_lock = threading.Lock()


def get_shared_redis_runtime() -> RedisRuntime:
    """Return the process-wide loop runtime used by Redis account stores."""
    global _shared_runtime
    with _shared_runtime_lock:
        if _shared_runtime is None:
            _shared_runtime = RedisRuntime()
        return _shared_runtime
