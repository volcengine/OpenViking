# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0
"""Bounded execution of business work under a temporary shard lease."""

from __future__ import annotations

import asyncio
import logging
import time
from typing import Awaitable, Callable

from .config import ExpiryConfig
from .leases import ShardLeaseCoordinator
from .metrics import ExpiryMetrics
from .models import (
    OwnerRole,
    OwnerToken,
    ShardLease,
    StoreOperationError,
    task_is_cancelling,
)

ShardProcessor = Callable[["ShardExecution", asyncio.Event], Awaitable[None]]
RunnerScope = tuple[OwnerRole, str | None, int]

logger = logging.getLogger(__name__)


class ShardExecution:
    """A revocable checkpoint and the strong owner of a processor's children.

    Cancellation only stops local work. It does not prove that an external
    request or callback has stopped, so cancelled work never authorizes release.
    """

    def __init__(self, lease: ShardLease) -> None:
        self._lease = lease
        self._children: set[asyncio.Task[None]] = set()
        self._waiters: set[asyncio.Task[object]] = set()
        self._error: BaseException | None = None
        self._outcome_unknown = False

    @property
    def owner(self) -> OwnerToken:
        return self._lease.owner

    @property
    def shard_id(self) -> int:
        return self.owner.shard_id

    def ensure_active(self) -> None:
        task = asyncio.current_task()
        if task_is_cancelling(task):
            # A handler may swallow child cancellation and return normally. Mark
            # uncertainty before its next store operation, not in a late callback.
            self._outcome_unknown = True
            raise asyncio.CancelledError
        self._lease.ensure_active()

    def spawn(self, operation: Callable[[], Awaitable[None]]) -> asyncio.Task[None]:
        self.ensure_active()

        async def guarded() -> None:
            self.ensure_active()
            await operation()

        task = asyncio.create_task(guarded())
        self._children.add(task)
        task.add_done_callback(self._child_done)
        return task

    async def wait(self, delay_seconds: float, stop: asyncio.Event) -> None:
        """Wait for a poll, stop, or invalidation, checking both sides of the wait."""
        self.ensure_active()
        deadline = time.monotonic() + max(0.0, delay_seconds)
        waiters = {
            asyncio.create_task(stop.wait()),
            asyncio.create_task(self._lease.lost.wait()),
            asyncio.create_task(self._lease.revoked.wait()),
        }
        self._waiters.update(waiters)
        for task in waiters:
            task.add_done_callback(self._waiter_done)
        try:
            while True:
                self.ensure_active()
                now = time.monotonic()
                if stop.is_set() or now >= deadline:
                    return
                await asyncio.wait(
                    waiters,
                    timeout=max(0.0, min(deadline, self._lease.valid_until_monotonic) - now),
                    return_when=asyncio.FIRST_COMPLETED,
                )
                self.ensure_active()
        finally:
            for task in waiters:
                task.cancel()

    def _waiter_done(self, task: asyncio.Task[object]) -> None:
        self._waiters.discard(task)
        _consume_exception(task)

    def _child_done(self, task: asyncio.Task[None]) -> None:
        self._children.discard(task)
        if task.cancelled() or task_is_cancelling(task):
            self._outcome_unknown = True
        if task.cancelled():
            return
        error = task.exception()
        if error is not None:
            self._error = self._error or error
            if isinstance(error, StoreOperationError) and error.outcome_unknown:
                self._outcome_unknown = True

    def _revoke(self) -> None:
        self._lease.revoked.set()


def _consume_exception(task: asyncio.Task[object]) -> None:
    if not task.cancelled():
        task.exception()


class ShardRunner:
    """Run isolated shard executions while retaining work that cannot stop."""

    def __init__(
        self,
        leases: ShardLeaseCoordinator,
        config: ExpiryConfig,
        metrics: ExpiryMetrics,
    ) -> None:
        self._leases = leases
        self._config = config
        self._metrics = metrics
        self._active: set[RunnerScope] = set()
        self._pending: dict[RunnerScope, set[asyncio.Task[object]]] = {}

    @property
    def has_pending(self) -> bool:
        return any(not task.done() for tasks in self._pending.values() for task in tasks)

    async def wait_pending(self, timeout: float) -> bool:
        """Observe retained work for at most timeout seconds, without cancelling it."""
        tasks = {task for tasks in self._pending.values() for task in tasks if not task.done()}
        if tasks:
            await asyncio.wait(tasks, timeout=max(0.0, timeout))
        return not self.has_pending

    async def run_if_acquired(
        self,
        role: OwnerRole,
        shard_id: int,
        process: ShardProcessor,
        stop: asyncio.Event,
        *,
        item_type: str | None = None,
    ) -> bool:
        scope = (role, item_type, shard_id)
        if scope in self._active:
            raise self._not_stopped(scope)
        if stop.is_set():
            return False
        self._active.add(scope)
        try:
            # Shield the request so caller cancellation cannot force an unbounded
            # join of a Redis implementation which suppresses cancellation.
            acquire = asyncio.create_task(
                self._leases.try_acquire(role, shard_id, item_type=item_type)
            )
            try:
                lease = await asyncio.shield(acquire)
            except BaseException:
                acquire.cancel()
                await self._settle(scope, {acquire})
                raise
            if lease is None:
                return False
            await self._run(scope, ShardExecution(lease), process, stop)
            return True
        finally:
            self._active.discard(scope)

    async def _run(
        self,
        scope: RunnerScope,
        execution: ShardExecution,
        process: ShardProcessor,
        stop: asyncio.Event,
    ) -> None:
        lease = execution._lease

        async def guarded_process() -> None:
            execution.ensure_active()
            await process(execution, stop)

        processor = asyncio.create_task(guarded_process())
        renewing = asyncio.Event()
        heartbeat = asyncio.create_task(self._heartbeat(lease, renewing))
        stopped = asyncio.create_task(stop.wait())
        lost = asyncio.create_task(lease.lost.wait())
        tasks = {processor, heartbeat, stopped, lost}
        forced = False
        error: BaseException | None = None
        try:
            grace_deadline: float | None = None
            while True:
                now = time.monotonic()
                if lease.lost.is_set() or now >= lease.valid_until_monotonic:
                    lease.lost.set()
                    forced = True
                    break
                if processor.done():
                    break
                if heartbeat.done():
                    # A heartbeat is only supposed to finish when ownership is lost.
                    lease.lost.set()
                    forced = True
                    break
                if stop.is_set() and grace_deadline is None:
                    grace_deadline = now + self._config.shutdown_timeout_seconds
                if grace_deadline is not None and now >= grace_deadline:
                    forced = True
                    break
                deadline = lease.valid_until_monotonic
                watched = {processor, heartbeat, lost}
                if grace_deadline is None:
                    watched.add(stopped)
                else:
                    deadline = min(deadline, grace_deadline)
                await asyncio.wait(
                    watched,
                    timeout=max(0.0, deadline - now),
                    return_when=asyncio.FIRST_COMPLETED,
                )
        except BaseException as exc:
            forced = True
            error = exc
        finally:
            execution._revoke()
            # A processor returning while it still owns detached operations is a
            # lifecycle violation, even if those operations cooperate with cancel.
            children = {task for task in execution._children if not task.done()}
            if children:
                if processor.done() and not forced:
                    error = error or StoreOperationError(
                        "shard_children_pending",
                        "Shard processor returned with unfinished children",
                    )
                forced = True
            if not processor.done() or processor.cancelled() or task_is_cancelling(processor):
                forced = True
            # Mark synchronously before cancel: a Redis implementation may catch
            # cancellation and return a success, which must not revive ownership.
            if renewing.is_set() and not heartbeat.done():
                lease.lost.set()
            local_tasks = tasks | children | execution._waiters
            for task in local_tasks:
                if not task.done():
                    task.cancel()
            await self._settle(scope, local_tasks, allow_orphans=True)

        # task.done() is terminal even if the task raised an ordinary exception.
        # Read the exception before deciding release, then propagate it afterwards.
        if processor.done() and not processor.cancelled():
            error = error or processor.exception()
        error = error or execution._error
        unknown = execution._outcome_unknown or (
            isinstance(error, StoreOperationError) and error.outcome_unknown
        )
        if time.monotonic() >= lease.valid_until_monotonic:
            lease.lost.set()
        if not forced and not unknown and not lease.lost.is_set():
            release = asyncio.create_task(self._leases.release(lease))
            try:
                done, _ = await asyncio.wait(
                    {release}, timeout=self._config.redis.request_budget_seconds
                )
            except BaseException:
                lease.lost.set()
                release.cancel()
                await self._settle(scope, {release})
                raise
            if release not in done:
                lease.lost.set()
                release.cancel()
                await self._settle(scope, {release})
            else:
                release.result()
        if error is not None:
            raise error

    async def _heartbeat(self, lease: ShardLease, renewing: asyncio.Event) -> None:
        while True:
            await asyncio.sleep(self._config.owner_renew_interval_seconds)
            renewing.set()
            try:
                if not await self._leases.renew(lease):
                    return
            finally:
                renewing.clear()

    async def _settle(
        self,
        scope: RunnerScope,
        tasks: set[asyncio.Task],
        *,
        allow_orphans: bool = False,
    ) -> None:
        """Never couple the deadline to completion of a cancelled task."""
        if not tasks:
            return
        # Retain before awaiting: a second caller cancellation must not orphan
        # work while cleanup itself is suspended. Each callback owns its exact
        # set, not a later run's same-role state slot.
        retained = self._pending.setdefault(scope, set())
        for task in tasks:
            if not task.done() and task not in retained:
                retained.add(task)

                def pending_done(
                    finished: asyncio.Task[object],
                    scope: RunnerScope = scope,
                    retained: set[asyncio.Task[object]] = retained,
                ) -> None:
                    self._pending_done(scope, retained, finished)

                task.add_done_callback(pending_done)
        try:
            done, _ = await asyncio.wait(tasks, timeout=self._config.redis.request_budget_seconds)
        except asyncio.CancelledError as exc:
            if any(not task.done() for task in tasks):
                raise self._not_stopped(scope) from exc
            raise
        for task in done:
            _consume_exception(task)
            retained.discard(task)
        if not retained and self._pending.get(scope) is retained:
            self._pending.pop(scope)
        orphaned = sum(not task.done() for task in tasks)
        if orphaned:
            role, item_type, shard_id = scope
            self._metrics.count(
                "timer_shard_orphaned",
                orphaned,
                item_type=item_type or "",
                outcome=role.value,
            )
            logger.warning(
                "expiry shard execution left orphaned tasks role=%s item_type=%s "
                "shard_id=%d count=%d",
                role.value,
                item_type or "",
                shard_id,
                orphaned,
            )
        if orphaned and not allow_orphans:
            raise self._not_stopped(scope)

    def _pending_done(
        self,
        scope: RunnerScope,
        retained: set[asyncio.Task[object]],
        task: asyncio.Task[object],
    ) -> None:
        _consume_exception(task)
        retained.discard(task)
        if not retained and self._pending.get(scope) is retained:
            self._pending.pop(scope)

    @staticmethod
    def _not_stopped(scope: RunnerScope) -> StoreOperationError:
        role, item_type, shard_id = scope
        label = role.value if item_type is None else f"{role.value}:{item_type}"
        return StoreOperationError(
            "shard_not_stopped",
            f"The {label} shard {shard_id} still has active local work",
        )
