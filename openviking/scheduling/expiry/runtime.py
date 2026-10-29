# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0
"""Expiry lifecycle and dedicated Redis event loop."""

from __future__ import annotations

import asyncio
import concurrent.futures
import logging
import threading
import time
from dataclasses import dataclass, field
from typing import Any, Awaitable, Callable, Coroutine, TypeVar

from .api import ExpiryScheduler
from .config import ExpiryConfig
from .consumer import ExpiryConsumer
from .keys import HASH_NAME, HASH_SEED, IDENTITY_ENCODING, ExpiryKeys, encode_component
from .leases import ShardLeaseCoordinator
from .metrics import ExpiryMetrics
from .models import NamespaceSpec, StoreOperationError
from .publisher import ExpiryPublisher
from .redis_store import RedisExpiryStore
from .registry import HandlerRegistry
from .service import ExpiryRuntimeState, _ExpirySchedulerFacade
from .shard_runner import ShardRunner

T = TypeVar("T")
logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class ExpiryRuntimeRoles:
    report: bool = False
    publish: bool = False
    consume: bool = False

    def __post_init__(self) -> None:
        if self.consume and not self.report:
            raise ValueError("Expiry consumer requires reporting for deferred callbacks")


@dataclass(eq=False)
class _RedisRequest:
    # Unlike run_coroutine_threadsafe's Future, this future is completed only
    # when the Redis-loop task AND its operation have actually terminated.
    finished: concurrent.futures.Future = field(default_factory=concurrent.futures.Future)
    cancelled: threading.Event = field(default_factory=threading.Event)
    task: asyncio.Task | None = None  # Read and written only on the Redis loop.


class RedisLoopExecutor:
    def __init__(self, *, max_inflight: int, request_budget_seconds: float) -> None:
        self._max_inflight = max_inflight
        self._request_budget_seconds = request_budget_seconds
        self._loop: asyncio.AbstractEventLoop | None = None
        self._thread: threading.Thread | None = None
        self._gate: asyncio.Semaphore | None = None
        self._accepting = False
        self._ready = threading.Event()
        self._requests: set[_RedisRequest] = set()
        self._requests_lock = threading.Lock()
        self._stop_requested = False

    async def start(self) -> None:
        if self._accepting:
            return
        if self._thread is not None:
            raise self._not_stopped()
        self._ready.clear()
        self._stop_requested = False
        self._thread = threading.Thread(
            target=self._thread_main, name="openviking-redis-expiry", daemon=True
        )
        self._thread.start()
        ready = await asyncio.to_thread(self._ready.wait, 5.0)
        if not ready or self._loop is None:
            raise RuntimeError("Expiry Redis loop failed to start")
        with self._requests_lock:
            self._accepting = True

    async def run(self, operation: Callable[[], Awaitable[T]]) -> T:
        loop = self._loop
        # Nested store.close calls during run_cleanup are already covered by
        # their tracked parent request, and must be allowed after quiescing.
        if loop is not None and asyncio.get_running_loop() is loop:
            return await self._execute(operation)
        request = self._submit(operation)
        finished = asyncio.wrap_future(request.finished)
        try:
            return await asyncio.shield(finished)
        except asyncio.CancelledError:
            self._cancel(request)
            # Keep the caller visible to ShardRunner until the actual operation
            # exits. Repeated cancellation must not sever this lifetime bridge.
            await self._join(finished)
            raise

    async def quiesce(self, timeout_seconds: float) -> None:
        """Reject new work and observe actual remote termination before closing Redis."""
        with self._requests_lock:
            self._accepting = False
            requests = list(self._requests)
        if requests:
            futures = {asyncio.wrap_future(request.finished) for request in requests}
            done, pending = await asyncio.wait(futures, timeout=max(0.0, timeout_seconds))
            for future in done:
                if not future.cancelled():
                    future.exception()
            # A caller might have already stopped awaiting a failed request.
            for future in pending:
                future.add_done_callback(self._consume_exception)
            if pending:
                raise self._not_stopped()

    async def run_cleanup(self, operation: Callable[[], Awaitable[T]], timeout_seconds: float) -> T:
        """Run storage cleanup on its own loop without reopening public admission."""
        request = self._submit(operation, cleanup=True)
        finished = asyncio.wrap_future(request.finished)
        try:
            done, _ = await asyncio.wait({finished}, timeout=max(0.0, timeout_seconds))
        except BaseException:
            self._cancel(request)
            finished.add_done_callback(self._consume_exception)
            raise
        if finished not in done:
            self._cancel(request)
            finished.add_done_callback(self._consume_exception)
            raise self._not_stopped()
        return finished.result()

    async def close(self, timeout_seconds: float) -> None:
        deadline = time.monotonic() + max(0.0, timeout_seconds)
        await self.quiesce(timeout_seconds)
        loop, thread = self._loop, self._thread
        if loop is None and thread is not None and thread.is_alive():
            # start() may have been cancelled before the new thread published
            # its loop. Wait only within the close budget, then stop that loop.
            await asyncio.to_thread(self._ready.wait, max(0.0, deadline - time.monotonic()))
            loop = self._loop
        if loop is not None and not self._stop_requested:
            self._stop_requested = True
            loop.call_soon_threadsafe(loop.stop)
        if thread is not None:
            await asyncio.to_thread(thread.join, max(0.0, deadline - time.monotonic()))
            if thread.is_alive():
                # Retain both references so callers can retry, and cannot start
                # another I/O thread while the old one still owns dependencies.
                raise self._not_stopped()
        self._loop = None
        self._thread = None
        self._gate = None

    def _submit(
        self, operation: Callable[[], Awaitable[T]], *, cleanup: bool = False
    ) -> _RedisRequest:
        with self._requests_lock:
            loop = self._loop
            if (not cleanup and not self._accepting) or loop is None or self._stop_requested:
                raise StoreOperationError("not_started", "Redis expiry I/O loop is not running")
            request = _RedisRequest()
            # Register before scheduling: shutdown must also see not-yet-started I/O.
            self._requests.add(request)

            async def cleanup_operation() -> T:
                return await operation()

            def start_request() -> None:
                request.task = asyncio.create_task(
                    cleanup_operation() if cleanup else self._execute(operation)
                )
                request.task.add_done_callback(lambda task: self._request_done(request, task))
                if request.cancelled.is_set():
                    request.task.cancel()

            try:
                loop.call_soon_threadsafe(start_request)
            except BaseException:
                self._requests.discard(request)
                raise
        return request

    def _cancel(self, request: _RedisRequest) -> None:
        if request.cancelled.is_set():
            return
        request.cancelled.set()

        def cancel_request() -> None:
            if request.task is not None and not request.task.done():
                request.task.cancel()

        loop = self._loop
        if loop is not None:
            loop.call_soon_threadsafe(cancel_request)

    def _request_done(self, request: _RedisRequest, task: asyncio.Task) -> None:
        if task.cancelled():
            request.finished.set_exception(asyncio.CancelledError())
        else:
            error = task.exception()
            if error is not None:
                request.finished.set_exception(error)
            else:
                request.finished.set_result(task.result())
        with self._requests_lock:
            self._requests.discard(request)

    @staticmethod
    def _consume_exception(future: asyncio.Future) -> None:
        if not future.cancelled():
            future.exception()

    @staticmethod
    async def _join(future: asyncio.Future) -> None:
        while not future.done():
            try:
                await asyncio.shield(future)
            except (asyncio.CancelledError, Exception):
                pass
        if not future.cancelled():
            future.exception()

    @staticmethod
    def _not_stopped() -> StoreOperationError:
        return StoreOperationError(
            "shard_not_stopped", "Redis expiry work or its I/O thread has not terminated"
        )

    def _thread_main(self) -> None:
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        self._loop = loop
        self._gate = asyncio.Semaphore(self._max_inflight)
        self._ready.set()
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

    async def _execute(self, operation: Callable[[], Awaitable[T]]) -> T:
        gate = self._gate
        if gate is None:
            raise StoreOperationError("not_started", "Redis expiry I/O loop is not ready")

        async def guarded() -> T:
            async with gate:
                return await operation()

        task = asyncio.create_task(guarded())
        try:
            done, _ = await asyncio.wait({task}, timeout=self._request_budget_seconds)
            if task not in done:
                task.cancel()
                await self._join(task)
                raise StoreOperationError(
                    "redis_timeout",
                    "Redis expiry request exceeded its budget",
                    retryable=True,
                    outcome_unknown=True,
                )
            return task.result()
        finally:
            if not task.done():
                task.cancel()
                await self._join(task)
            if not task.cancelled():
                task.exception()


class ExpiryRuntime:
    def __init__(
        self,
        config: ExpiryConfig,
        *,
        roles: ExpiryRuntimeRoles,
        metrics: ExpiryMetrics | None = None,
    ) -> None:
        self._config = config
        self._roles = roles
        self._metrics = metrics or ExpiryMetrics()
        self._keys = ExpiryKeys(encode_component(config.service_name))
        self._io = RedisLoopExecutor(
            max_inflight=config.redis.max_inflight_requests,
            request_budget_seconds=config.redis.request_budget_seconds,
        )
        self._store = RedisExpiryStore(config.redis, self._keys, self._io, self._metrics)
        self._registry = HandlerRegistry()
        self._state = ExpiryRuntimeState()
        self._facade = _ExpirySchedulerFacade(
            self._store,
            self._registry,
            self._state,
            shard_count=config.shard_count,
            reporting_enabled=roles.report,
            metrics=self._metrics,
            on_report=self._start_consumer,
        )
        self._leases = ShardLeaseCoordinator(self._store, config, self._metrics)
        self._shard_runner = ShardRunner(self._leases, config, self._metrics)
        self._publisher: ExpiryPublisher | None = None
        self._consumers: dict[str, ExpiryConsumer] = {}
        self._tasks: dict[str, asyncio.Task[None]] = {}
        self._role_loop: asyncio.AbstractEventLoop | None = None
        self._stop = asyncio.Event()
        self._storage_started = False
        self._storage_closed = False
        # Readiness is not ownership: cancellation can leave resources behind
        # before start_storage has completed.
        self._storage_owned = False
        self._store_open_attempted = False
        self._storage_start_task: asyncio.Task[None] | None = None

    @property
    def scheduler(self) -> ExpiryScheduler:
        return self._facade

    async def start_storage(self) -> None:
        if self._storage_started:
            return
        if self._storage_owned or self._storage_start_task is not None:
            raise StoreOperationError(
                "shard_not_stopped", "Previous expiry storage startup has not been cleaned up"
            )
        self._storage_owned = True
        self._storage_closed = False
        # Keep the actual startup operation visible if it resists cancellation.
        # The caller's cancellation must not make close assume it has stopped.
        startup = asyncio.create_task(self._initialize_storage())
        startup.add_done_callback(RedisLoopExecutor._consume_exception)
        self._storage_start_task = startup
        try:
            await asyncio.shield(startup)
        except BaseException:
            if not startup.done():
                startup.cancel()
            try:
                await self.close()
            except BaseException:
                # Preserve the original failure/cancellation. Ownership and the
                # startup task remain available for a later close retry.
                logger.warning("Expiry partial startup cleanup did not complete", exc_info=True)
            raise
        self._storage_start_task = None
        self._storage_started = True
        self._state.storage_ready = True

    async def _initialize_storage(self) -> None:
        await self._io.start()
        self._store_open_attempted = True
        await self._store.open()
        await self._store.ensure_namespace(
            NamespaceSpec(
                self._config.shard_count,
                HASH_NAME,
                HASH_SEED,
                IDENTITY_ENCODING,
            )
        )

    async def _settle_storage_start(self) -> None:
        startup = self._storage_start_task
        if startup is None:
            return
        if not startup.done():
            startup.cancel()
            done, _ = await asyncio.wait({startup}, timeout=self._config.shutdown_timeout_seconds)
            if startup not in done:
                raise StoreOperationError(
                    "shard_not_stopped", "Expiry storage startup is still running"
                )
        if not startup.cancelled():
            startup.exception()
        self._storage_start_task = None

    async def start_roles(self) -> None:
        if not self._storage_started:
            raise RuntimeError("Expiry storage is not started")
        if self._state.accepting_calls:
            return
        bindings = self._registry.freeze()
        self._role_loop = asyncio.get_running_loop()
        self._stop.clear()
        if self._roles.publish:
            self._publisher = ExpiryPublisher(
                self._store, self._shard_runner, self._config, self._metrics
            )
            self._spawn_role("publisher", self._publisher.run(self._stop))
        if self._roles.consume:
            for item_type, binding in bindings.items():
                if not binding.options.lazy_start or self._store.consumer_enabled(item_type):
                    self._start_consumer(item_type)
        self._state.accepting_calls = True

    def _start_consumer(self, item_type: str) -> None:
        # Reports can arrive from QueueManager loops; roles belong to startup's loop.
        if self._role_loop is not None and asyncio.get_running_loop() is not self._role_loop:
            self._role_loop.call_soon_threadsafe(self._start_consumer, item_type)
            return
        name = f"callback:{item_type}"
        if not self._roles.consume or self._stop.is_set() or name in self._tasks:
            return
        binding = self._registry.get(item_type)
        if binding is None:
            return
        consumer = ExpiryConsumer(
            self._store, self._shard_runner, item_type, binding, self._config, self._metrics
        )
        self._consumers[item_type] = consumer
        self._spawn_role(name, consumer.run(self._stop))

    async def wait(self) -> None:
        if not self._tasks:
            await self._stop.wait()
            return
        done, _ = await asyncio.wait(self._tasks.values(), return_when=asyncio.FIRST_COMPLETED)
        for task in done:
            await task

    async def stop_roles(self, *, keep_reporting: bool = False) -> None:
        # A producer drain may still need durable reports after callbacks stop.
        if not keep_reporting:
            self._state.accepting_calls = False
        self._stop.set()
        tasks = set(self._tasks.values())
        if tasks:
            _, pending = await asyncio.wait(tasks, timeout=self._config.shutdown_timeout_seconds)
            for task in pending:
                task.cancel()
            if pending:
                await asyncio.wait(pending, timeout=self._config.redis.request_budget_seconds)
            for name, task in list(self._tasks.items()):
                if task.done():
                    if not task.cancelled():
                        task.exception()
                    self._tasks.pop(name, None)
        settled = await self._shard_runner.wait_pending(self._config.redis.request_budget_seconds)
        if self._tasks or not settled:
            raise StoreOperationError(
                "shard_not_stopped", "Expiry work is still running; dependencies remain open"
            )

    async def close(self) -> None:
        await self.stop_roles()
        if not self._storage_owned:
            return
        await self._settle_storage_start()
        # Stop admission and check real Redis-loop tasks, not just their
        # application-loop wrappers, before closing any dependency.
        await self._io.quiesce(self._config.shutdown_timeout_seconds)
        if not self._storage_closed:
            if self._store_open_attempted:
                await self._io.run_cleanup(self._store.close, self._config.shutdown_timeout_seconds)
            self._storage_closed = True
        # Keep ownership if client cleanup or thread termination failed. Once
        # client cleanup succeeds, retries only observe the I/O thread join.
        await self._io.close(self._config.shutdown_timeout_seconds)
        self._storage_owned = False
        self._store_open_attempted = False
        self._storage_started = False
        self._state.storage_ready = False

    def _spawn_role(self, name: str, operation: Coroutine[Any, Any, None]) -> None:
        task: asyncio.Task[None] = asyncio.create_task(operation, name=f"expiry-{name}")
        self._tasks[name] = task
