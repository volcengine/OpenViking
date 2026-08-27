# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0
"""Coordinate task lifecycle, queue work, and cancellation.

Task state and queue work are read and mutated through ``TaskWorkStore``. The
tracker also owns process-local ``_active`` asyncio handles used to accelerate
cancellation and projects ``queue_status`` from persisted works.

Constraints:
  - The tracker does not know the storage medium or whether there is a cache;
    it only depends on the ``TaskWorkStore`` interface. Single-process ordering
    is provided by ``OwnerLoopDispatcher`` + per-task locks; a distributed store
    provides compare-and-swap for cross-process writes.
  - Task lifecycle terminalization is only final once all descendant work has
    settled (``TaskAggregate.has_open_work``).
  - Live asyncio handles (``_active``) are runtime state and never persisted.
  - Owner-scoped reads may reach persistence. Historical ownerless reads are
    cache-only; they never trigger an unscoped persistent lookup.
"""

import asyncio
import math
import re
import threading
import time
from copy import deepcopy
from typing import Any, Callable, Dict, List, Optional
from uuid import uuid4

from openviking.service.task_domain import (
    ACTIVE_TASK_STATUSES,
    TERMINAL_TASK_STATUSES,
    TaskAggregate,
    TaskRecord,
    TaskStatus,
    WorkRecord,
    WorkState,
)
from openviking.service.task_store import TaskWorkStore
from openviking.utils.async_utils import (
    AsyncConcurrencyLimiter,
    KeyedAsyncLockPool,
    OwnerLoopDispatcher,
    run_to_completion,
)
from openviking_cli.utils.logger import get_logger

logger = get_logger(__name__)

# Re-export for callers that import status from the tracker module historically.
__all__ = ["TaskStatus", "TaskRecord", "TaskTracker", "get_task_tracker", "set_task_tracker"]

_CANCELLABLE_TASK_TYPES = {
    "add_resource",
    "session_commit",
    "admin_reindex",
    "snapshot_restore_reindex",
}

_CANCEL_POLL_INTERVAL = 30
_TASK_WAIT_POLL_INTERVAL = 0.05
_RECOVERED_TASK_TYPE = "__recovered_queue_work__"
_MAX_CAS_RETRIES = 5


# ── Singleton ──

_instance: Optional["TaskTracker"] = None
_init_lock = threading.Lock()


def get_task_tracker() -> "TaskTracker":
    """Get the global TaskTracker singleton installed during storage init."""
    with _init_lock:
        if _instance is None:
            logger.error(
                "TaskTracker accessed before service storage initialization; refusing to create "
                "a separate AGFS client. Ensure OpenVikingService installs the shared tracker "
                "with set_task_tracker() before task APIs are used.",
                stack_info=True,
            )
            raise RuntimeError(
                "TaskTracker not initialized. OpenVikingService must install the shared "
                "tracker with set_task_tracker() during storage initialization."
            )
        return _instance


def set_task_tracker(tracker: "TaskTracker") -> None:
    """Replace the global TaskTracker singleton."""
    global _instance
    with _init_lock:
        _instance = tracker


# ── Sanitization ──

_SENSITIVE_PATTERNS = re.compile(
    r"(sk-|cr_|ghp_|ntn_|xox[baprs]-|Bearer\s+)[a-zA-Z0-9._-]+",
    re.IGNORECASE,
)
_MAX_ERROR_LEN = 500
_SENSITIVE_RESULT_KEYS = {"user_key"}


def _sanitize_error(error: str) -> str:
    sanitized = _SENSITIVE_PATTERNS.sub("[REDACTED]", error)
    if len(sanitized) > _MAX_ERROR_LEN:
        sanitized = sanitized[:_MAX_ERROR_LEN] + "...[truncated]"
    return sanitized


def _sanitize_task_result(result: Any) -> Any:
    if isinstance(result, dict):
        return {
            key: _sanitize_task_result(value)
            for key, value in result.items()
            if key not in _SENSITIVE_RESULT_KEYS
        }
    if isinstance(result, list):
        return [_sanitize_task_result(item) for item in result]
    return result


class _CommittedMutationCancelled(asyncio.CancelledError):
    """Caller cancellation observed after the store write committed."""


class TaskWriteConflict(RuntimeError):
    """The persisted task advanced after this mutation was computed."""


class TaskTracker:
    """Async task tracker backed by a TaskWorkStore.

    Mutations are serialized per task on one event loop. The store owns
    durability and cross-process concurrency. Caching, if needed, also belongs
    to the store implementation.
    """

    CLEANUP_INTERVAL = 300

    def __init__(self, store: TaskWorkStore, *, max_concurrent_store_io: int = 8) -> None:
        self._store = store
        self._dispatcher = OwnerLoopDispatcher()
        self._task_locks = KeyedAsyncLockPool[tuple[str, str, str]]()
        self._business_locks = KeyedAsyncLockPool[tuple[str, str, str, str]]()
        self._store_io = AsyncConcurrencyLimiter(max_concurrent_store_io)
        self._cleanup_task: Optional[asyncio.Task] = None
        self._cancel_poll_task: Optional[asyncio.Task] = None
        # Live handles accelerate cancellation on this process and are not persisted.
        self._active_lock = threading.Lock()
        # Per task-owner key: live handle -> whether a user-cancel signal was sent.
        self._active: Dict[tuple[str, str, str], Dict[asyncio.Task[Any], bool]] = {}
        logger.info(
            "[TaskTracker] Initialized (store=%s)",
            self._store.__class__.__name__,
        )

    # ── Owner validation ──

    @staticmethod
    def _validate_owner(account_id: str, user_id: str) -> None:
        if not account_id or not user_id:
            raise ValueError("Task ownership requires non-empty account_id and user_id")

    # ── Lifecycle loops ──

    def start_background_loops(self) -> None:
        self._dispatcher.bind_current_loop()
        if self._cleanup_task is None or self._cleanup_task.done():
            self._cleanup_task = asyncio.create_task(self._cleanup_loop())
        if self._cancel_poll_task is None or self._cancel_poll_task.done():
            self._cancel_poll_task = asyncio.create_task(self._cancel_poll_loop())
        logger.debug("[TaskTracker] Cleanup and cancel-poll loops started")

    def stop_background_loops(self) -> None:
        for task in (self._cleanup_task, self._cancel_poll_task):
            if task is not None and not task.done():
                task.cancel()

    async def _cleanup_loop(self) -> None:
        while True:
            try:
                await asyncio.sleep(self.CLEANUP_INTERVAL)
                await self._store.cleanup()
            except asyncio.CancelledError:
                break
            except Exception:
                logger.exception("[TaskTracker] Cleanup error")

    async def _cancel_poll_loop(self) -> None:
        """Cancel local handles whose persisted task is cancelling.

        The store supplies cancellation state; this process only acts on handles
        it owns. Distributed stores must return changes made by other nodes.
        """
        while True:
            try:
                await asyncio.sleep(_CANCEL_POLL_INTERVAL)
                cancelling = await self._store.list_cancelling_tasks()
                if not cancelling:
                    continue
                for handle in self._claim_user_cancel_handles(cancelling):
                    handle.get_loop().call_soon_threadsafe(handle.cancel)
            except asyncio.CancelledError:
                break
            except Exception:
                logger.exception("[TaskTracker] Cancel-poll error")

    # ── Task creation ──

    async def create(
        self,
        task_type: str,
        resource_id: Optional[str] = None,
        *,
        account_id: str,
        user_id: str,
        task_id: Optional[str] = None,
        meta: Optional[Dict[str, Any]] = None,
        auth: Optional[Dict[str, Any]] = None,
    ) -> TaskRecord:
        """Register a new pending task. Returns a snapshot copy."""
        self._validate_owner(account_id, user_id)
        task = TaskRecord(
            task_id=task_id or str(uuid4()),
            task_type=task_type,
            resource_id=resource_id,
            account_id=account_id,
            user_id=user_id,
            meta=dict(meta or {}),
            auth=dict(auth or {}),
        )
        return await self._dispatcher.run(lambda: self._create_on_owner(task, task_id is not None))

    async def _create_on_owner(self, task: TaskRecord, check_existing: bool) -> TaskRecord:
        async with self._task_locks.acquire(
            (task.account_id or "", task.user_id or "", task.task_id)
        ):
            if check_existing:
                existing = await self._load(task.task_id, task.account_id, task.user_id)
                if existing is not None:
                    if existing.task.task_type == _RECOVERED_TASK_TYPE:
                        recovered = existing.task
                        recovered.task_type = task.task_type
                        recovered.resource_id = task.resource_id
                        recovered.meta = dict(task.meta)
                        recovered.updated_at = _next_updated_at(recovered)
                        await self._store_write("update", recovered)
                        return _copy(recovered)
                    return _copy(existing.task)
            await self._store_write("create", task)
        logger.debug(
            "[TaskTracker] Created task %s type=%s resource=%s",
            task.task_id,
            task.task_type,
            task.resource_id,
        )
        return _copy(task)

    async def create_if_no_running(
        self,
        task_type: str,
        resource_id: str,
        *,
        account_id: str,
        user_id: str,
        meta: Optional[Dict[str, Any]] = None,
    ) -> Optional[TaskRecord]:
        """Atomically create a task only if no active one exists for type+resource."""
        self._validate_owner(account_id, user_id)
        business_key = (account_id, user_id, task_type, resource_id)
        return await self._dispatcher.run(
            lambda: self._create_if_no_running_on_owner(
                business_key, task_type, resource_id, account_id, user_id, meta
            )
        )

    async def _create_if_no_running_on_owner(
        self,
        business_key: tuple[str, str, str, str],
        task_type: str,
        resource_id: str,
        account_id: str,
        user_id: str,
        meta: Optional[Dict[str, Any]],
    ) -> Optional[TaskRecord]:
        async with self._business_locks.acquire(business_key):
            task = TaskRecord(
                task_id=str(uuid4()),
                task_type=task_type,
                resource_id=resource_id,
                account_id=account_id,
                user_id=user_id,
                meta=dict(meta or {}),
            )
            async with self._task_locks.acquire((account_id, user_id, task.task_id)):
                created = await self._store_io.run(
                    "create_if_no_active",
                    lambda: run_to_completion(lambda: self._store.create_if_no_active(task)),
                )
                if not created:
                    return None
        return _copy(task)

    # ── Task transitions ──

    async def start(
        self,
        task_id: str,
        account_id: Optional[str] = None,
        user_id: Optional[str] = None,
        stage: Optional[str] = None,
    ) -> None:
        await self._dispatcher.run(
            lambda: self._start_on_owner(task_id, account_id, user_id, stage)
        )

    async def _start_on_owner(
        self,
        task_id: str,
        account_id: Optional[str],
        user_id: Optional[str],
        stage: Optional[str],
    ) -> None:
        aggregate = await self._load(task_id, account_id, user_id)
        owner = _aggregate_owner(aggregate)
        if owner is None:
            return
        account_id, user_id = owner
        async with self._task_locks.acquire((account_id, user_id, task_id)):

            def mutate(agg: TaskAggregate) -> bool:
                if agg.task.status not in (TaskStatus.PENDING, TaskStatus.RUNNING):
                    return False
                agg.task.status = TaskStatus.RUNNING
                if stage is not None:
                    agg.task.stage = stage
                agg.task.updated_at = _next_updated_at(agg.task)
                return True

            await self._mutate_task_with_retry(task_id, account_id, user_id, mutate)

    async def update_stage(
        self,
        task_id: str,
        stage: str,
        account_id: Optional[str] = None,
        user_id: Optional[str] = None,
    ) -> None:
        await self._dispatcher.run(
            lambda: self._update_stage_on_owner(task_id, stage, account_id, user_id)
        )

    async def _update_stage_on_owner(
        self,
        task_id: str,
        stage: str,
        account_id: Optional[str],
        user_id: Optional[str],
    ) -> None:
        aggregate = await self._load(task_id, account_id, user_id)
        owner = _aggregate_owner(aggregate)
        if owner is None:
            return
        account_id, user_id = owner
        async with self._task_locks.acquire((account_id, user_id, task_id)):

            def mutate(agg: TaskAggregate) -> bool:
                if agg.task.status not in (TaskStatus.PENDING, TaskStatus.RUNNING):
                    return False
                agg.task.stage = stage
                agg.task.updated_at = _next_updated_at(agg.task)
                return True

            await self._mutate_task_with_retry(task_id, account_id, user_id, mutate)

    async def complete(
        self,
        task_id: str,
        result: Dict[str, Any],
        account_id: Optional[str] = None,
        user_id: Optional[str] = None,
        *,
        resource_id: Optional[str] = None,
    ) -> None:
        """Record success; terminalization happens once owned work settles."""
        await self._dispatcher.run(
            lambda: self._record_outcome_on_owner(
                task_id, account_id, user_id, result=result, error=None, resource_id=resource_id
            )
        )

    async def fail(
        self,
        task_id: str,
        error: str,
        account_id: Optional[str] = None,
        user_id: Optional[str] = None,
    ) -> None:
        """Record failure; terminalization happens once owned work settles."""
        await self._dispatcher.run(
            lambda: self._record_outcome_on_owner(
                task_id, account_id, user_id, result=None, error=error, resource_id=None
            )
        )

    async def _record_outcome_on_owner(
        self,
        task_id: str,
        account_id: Optional[str],
        user_id: Optional[str],
        *,
        result: Optional[Dict[str, Any]],
        error: Optional[str],
        resource_id: Optional[str],
    ) -> None:
        committed = False
        cancellation: asyncio.CancelledError | None = None
        aggregate = await self._load(task_id, account_id, user_id)
        owner = _aggregate_owner(aggregate)
        if owner is None:
            return
        account_id, user_id = owner
        async with self._task_locks.acquire((account_id, user_id, task_id)):

            def mutate(agg: TaskAggregate) -> bool:
                if agg.task.status not in (TaskStatus.PENDING, TaskStatus.RUNNING):
                    return False
                if result is not None:
                    agg.task.result = dict(result)
                    if resource_id is not None:
                        agg.task.resource_id = resource_id
                if error is not None and agg.task.error is None:
                    agg.task.error = _sanitize_error(error)
                agg.task.updated_at = _next_updated_at(agg.task)
                return True

            try:
                _aggregate, committed = await self._mutate_task_with_retry(
                    task_id, account_id, user_id, mutate
                )
            except _CommittedMutationCancelled as exc:
                committed = True
                cancellation = exc
        if committed:
            try:
                await run_to_completion(
                    lambda: self._finalize_on_owner(task_id, account_id, user_id)
                )
            except asyncio.CancelledError as exc:
                cancellation = cancellation or exc
        else:
            await self._finalize_on_owner(task_id, account_id, user_id)
        if cancellation is not None:
            raise cancellation

    async def cancel(
        self,
        task_id: str,
        account_id: Optional[str] = None,
        user_id: Optional[str] = None,
    ) -> Optional[TaskRecord]:
        """Request cooperative cancellation; return the current snapshot."""
        return await self._dispatcher.run(
            lambda: self._cancel_on_owner(task_id, account_id, user_id)
        )

    async def _cancel_on_owner(
        self, task_id: str, account_id: Optional[str], user_id: Optional[str]
    ) -> Optional[TaskRecord]:
        committed = False
        cancellation: asyncio.CancelledError | None = None
        aggregate = await self._load(task_id, account_id, user_id)
        owner = _aggregate_owner(aggregate)
        if owner is None:
            return None
        account_id, user_id = owner
        async with self._task_locks.acquire((account_id, user_id, task_id)):

            def mutate(agg: TaskAggregate) -> bool:
                task = agg.task
                if task.status in (TaskStatus.CANCELLED, TaskStatus.CANCELLING):
                    return False
                if task.task_type not in _CANCELLABLE_TASK_TYPES:
                    raise ValueError(f"Task type '{task.task_type}' does not support cancellation")
                if task.status in TERMINAL_TASK_STATUSES:
                    raise ValueError(f"Task is already {task.status.value}")
                task.status = TaskStatus.CANCELLING
                task.updated_at = _next_updated_at(task)
                return True

            try:
                aggregate, committed = await self._mutate_task_with_retry(
                    task_id, account_id, user_id, mutate
                )
            except _CommittedMutationCancelled as exc:
                committed = True
                cancellation = exc
                aggregate = None
            if aggregate is None and not committed:
                return None

        # Cancel local work immediately; polling observes requests from other nodes.
        async def finish_cancellation() -> None:
            self._cancel_active(task_id, account_id=account_id, user_id=user_id)
            await self._finalize_on_owner(task_id, account_id, user_id)

        if committed:
            try:
                await run_to_completion(finish_cancellation)
            except asyncio.CancelledError as exc:
                cancellation = cancellation or exc
        else:
            await finish_cancellation()
        agg = await self._load(task_id, account_id, user_id)
        if cancellation is not None:
            raise cancellation
        return _copy(agg.task) if agg is not None else None

    # ── Finalization ──

    async def _finalize(
        self,
        task_id: str,
        account_id: Optional[str] = None,
        user_id: Optional[str] = None,
    ) -> None:
        async def finalize() -> None:
            aggregate = await self._load(task_id, account_id, user_id)
            owner = _aggregate_owner(aggregate)
            if owner is not None:
                await self._finalize_on_owner(task_id, owner[0], owner[1])

        await self._dispatcher.run(finalize)

    async def _finalize_on_owner(self, task_id: str, account_id: str, user_id: str) -> None:
        async with self._task_locks.acquire((account_id, user_id, task_id)):

            def mutate(agg: TaskAggregate) -> bool:
                if agg.task.status not in ACTIVE_TASK_STATUSES:
                    return False
                if agg.has_open_work() or self._has_active_handles(
                    task_id, account_id=account_id, user_id=user_id
                ):
                    return False
                task = agg.task
                if task.status == TaskStatus.CANCELLING:
                    task.status = TaskStatus.CANCELLED
                else:
                    work_error = agg.first_work_error()
                    if work_error and task.error is None:
                        task.error = _sanitize_error(work_error)
                    if isinstance(task.result, dict) and "queue_status" in task.result:
                        queue_status = agg.queue_status()
                        task.result["queue_status"] = queue_status
                        for queue_name, status_field in (
                            ("Semantic", "semantic_status"),
                            ("Embedding", "vector_status"),
                        ):
                            if task.result.get(status_field) != "queued":
                                continue
                            queue_result = queue_status.get(queue_name, {})
                            task.result[status_field] = (
                                "failed"
                                if int(queue_result.get("error_count", 0) or 0) > 0
                                else "complete"
                            )
                    if task.error is not None:
                        task.status = TaskStatus.FAILED
                    elif task.result is not None:
                        task.status = TaskStatus.COMPLETED
                    else:
                        return False
                task.stage = task.status.value
                task.updated_at = _next_updated_at(task)
                # Terminal tasks no longer need their short-lived credentials.
                task.auth = {}
                return True

            aggregate, updated = await self._mutate_task_with_retry(
                task_id, account_id, user_id, mutate
            )
            if updated and aggregate is not None:
                logger.info("[TaskTracker] Task %s %s", task_id, aggregate.task.status.value)

    def _has_active_handles(
        self,
        task_id: str,
        *,
        account_id: Optional[str],
        user_id: Optional[str],
    ) -> bool:
        key = (account_id or "", user_id or "", task_id)
        with self._active_lock:
            return bool(self._active.get(key))

    # ── Work operations (called by the task-work queue hook) ──

    async def get_task_auth(
        self,
        task_id: str,
        *,
        account_id: str,
        user_id: str,
    ) -> Dict[str, Any]:
        """Load task-owned authentication excluded from public snapshots."""
        self._validate_owner(account_id, user_id)

        async def load() -> Dict[str, Any]:
            aggregate = await self._load(task_id, account_id, user_id)
            if aggregate is None:
                return {}
            return deepcopy(aggregate.task.auth)

        return await self._dispatcher.run(load)

    async def register_work(
        self, task_id: str, work_id: str, queue_name: str, *, account_id: str, user_id: str
    ) -> bool:
        """Register durable queue work for a task; reject if terminal/cancelling."""
        return await self._dispatcher.run(
            lambda: self._register_work_on_owner(task_id, work_id, queue_name, account_id, user_id)
        )

    async def _register_work_on_owner(
        self, task_id: str, work_id: str, queue_name: str, account_id: str, user_id: str
    ) -> bool:
        async with self._task_locks.acquire((account_id, user_id, task_id)):
            agg = await self._load(task_id, account_id, user_id)
            if agg is None:
                return False
            if agg.task.is_terminal() or agg.task.status == TaskStatus.CANCELLING:
                return False
            work = WorkRecord(work_id=work_id, task_id=task_id, queue_name=queue_name)
            added = await self._store_io.run(
                "add_work",
                lambda: run_to_completion(
                    lambda: self._store.add_work(work, account_id=account_id, user_id=user_id)
                ),
            )
        return added

    async def discard_work(
        self, task_id: str, work_id: str, *, account_id: str, user_id: str
    ) -> None:
        await self._dispatcher.run(
            lambda: self._discard_work_on_owner(task_id, work_id, account_id, user_id)
        )
        await self._finalize(task_id, account_id, user_id)

    async def _discard_work_on_owner(
        self, task_id: str, work_id: str, account_id: str, user_id: str
    ) -> None:
        async with self._task_locks.acquire((account_id, user_id, task_id)):
            await self._store_io.run(
                "discard_work",
                lambda: run_to_completion(
                    lambda: self._store.discard_work(
                        task_id, work_id, account_id=account_id, user_id=user_id
                    )
                ),
            )

    async def restore_work(
        self, task_id: str, work_id: str, queue_name: str, *, account_id: str, user_id: str
    ) -> None:
        aggregate = await self._load(task_id, account_id, user_id)
        if aggregate is None:
            await self.create(
                _RECOVERED_TASK_TYPE,
                account_id=account_id,
                user_id=user_id,
                task_id=task_id,
                meta={"recovered_queue": queue_name},
            )
        elif aggregate.task.is_terminal():
            return
        await self._dispatcher.run(
            lambda: self._restore_work_on_owner(task_id, work_id, queue_name, account_id, user_id)
        )

    async def _restore_work_on_owner(
        self,
        task_id: str,
        work_id: str,
        queue_name: str,
        account_id: str,
        user_id: str,
    ) -> None:
        work = WorkRecord(work_id=work_id, task_id=task_id, queue_name=queue_name)
        async with self._task_locks.acquire((account_id, user_id, task_id)):
            await self._store_io.run(
                "restore_work",
                lambda: run_to_completion(
                    lambda: self._store.restore_work(work, account_id=account_id, user_id=user_id)
                ),
            )

    async def get_work_state(
        self,
        task_id: str,
        work_id: str,
        *,
        account_id: str,
        user_id: str,
    ) -> Optional[WorkState]:
        """Return the persisted state of one queue delivery."""

        async def load_state() -> Optional[WorkState]:
            aggregate = await self._load(task_id, account_id, user_id)
            work = aggregate.works.get(work_id) if aggregate is not None else None
            return work.state if work is not None else None

        return await self._dispatcher.run(load_state)

    async def settle_work(
        self, task_id: str, work_id: str, *, account_id: str, user_id: str
    ) -> None:
        """Settle work (done/failed) and finalize if it was the last."""
        await self._dispatcher.run(
            lambda: self._settle_work_on_owner(
                task_id,
                work_id,
                account_id,
                user_id,
                done=None,
                error=None,
            )
        )
        await self._finalize(task_id, account_id, user_id)

    async def mark_work_done(
        self, task_id: str, work_id: str, *, account_id: str, user_id: str
    ) -> None:
        await self._dispatcher.run(
            lambda: self._settle_work_on_owner(
                task_id,
                work_id,
                account_id,
                user_id,
                done=True,
                error=None,
            )
        )
        await self._finalize(task_id, account_id, user_id)

    async def mark_work_failed(
        self, task_id: str, work_id: str, error: str, *, account_id: str, user_id: str
    ) -> None:
        await self._dispatcher.run(
            lambda: self._settle_work_on_owner(
                task_id, work_id, account_id, user_id, done=False, error=error
            )
        )
        await self._finalize(task_id, account_id, user_id)

    async def start_work(
        self, task_id: str, work_id: str, *, account_id: str, user_id: str
    ) -> None:
        await self._dispatcher.run(
            lambda: self._start_work_on_owner(task_id, work_id, account_id, user_id)
        )

    async def _start_work_on_owner(
        self, task_id: str, work_id: str, account_id: str, user_id: str
    ) -> None:
        async with self._task_locks.acquire((account_id, user_id, task_id)):
            await self._store_io.run(
                "start_work",
                lambda: run_to_completion(
                    lambda: self._store.start_work(
                        task_id, work_id, account_id=account_id, user_id=user_id
                    )
                ),
            )

    async def mark_work_requeued(
        self, task_id: str, work_id: str, *, delta: int = 1, account_id: str, user_id: str
    ) -> None:
        await self._dispatcher.run(
            lambda: self._mark_work_requeued_on_owner(task_id, work_id, delta, account_id, user_id)
        )
        await self._finalize(task_id, account_id, user_id)

    async def _mark_work_requeued_on_owner(
        self, task_id: str, work_id: str, delta: int, account_id: str, user_id: str
    ) -> None:
        async with self._task_locks.acquire((account_id, user_id, task_id)):
            await self._store_io.run(
                "mark_work_requeued",
                lambda: run_to_completion(
                    lambda: self._store.mark_work_requeued(
                        task_id, work_id, delta=delta, account_id=account_id, user_id=user_id
                    )
                ),
            )

    async def record_work_error(
        self, task_id: str, work_id: str, error: str, *, account_id: str, user_id: str
    ) -> None:
        await self._dispatcher.run(
            lambda: self._record_work_error_on_owner(task_id, work_id, error, account_id, user_id)
        )

    async def _record_work_error_on_owner(
        self, task_id: str, work_id: str, error: str, account_id: str, user_id: str
    ) -> None:
        async with self._task_locks.acquire((account_id, user_id, task_id)):
            await self._store_io.run(
                "record_work_error",
                lambda: run_to_completion(
                    lambda: self._store.record_work_error(
                        task_id,
                        work_id,
                        _sanitize_error(error),
                        account_id=account_id,
                        user_id=user_id,
                    )
                ),
            )

    async def _settle_work_on_owner(
        self,
        task_id: str,
        work_id: str,
        account_id: str,
        user_id: str,
        *,
        done: Optional[bool],
        error: Optional[str],
    ) -> None:
        async with self._task_locks.acquire((account_id, user_id, task_id)):
            if done is None:
                aggregate = await self._load(task_id, account_id, user_id)
                work = aggregate.works.get(work_id) if aggregate is not None else None
                if work is not None and work.state == WorkState.REQUEUED:
                    return
                done = not bool(work and work.error)
                error = work.error if work is not None else None
            if done:
                await self._store_io.run(
                    "mark_work_done",
                    lambda: run_to_completion(
                        lambda: self._store.mark_work_done(
                            task_id, work_id, account_id=account_id, user_id=user_id
                        )
                    ),
                )
            else:
                await self._store_io.run(
                    "mark_work_failed",
                    lambda: run_to_completion(
                        lambda: self._store.mark_work_failed(
                            task_id,
                            work_id,
                            _sanitize_error(error) if error else None,
                            account_id=account_id,
                            user_id=user_id,
                        )
                    ),
                )

    def is_cancelling(
        self,
        task_id: str,
        account_id: Optional[str] = None,
        user_id: Optional[str] = None,
    ) -> bool:
        """Synchronous best-effort cancel check for the queue worker hot path.

        Runs on worker threads; resolves against the store's fast path. For the
        single-process caching store this reads memory; a distributed store uses
        a local invalidation cache.
        """
        try:
            return self._store.is_cancelling(task_id, account_id=account_id, user_id=user_id)
        except Exception:  # noqa: BLE001
            return False

    # ── Active handle lifecycle (process-local registration + persisted finalization) ──

    def register_active(
        self,
        task_id: str,
        account_id: Optional[str] = None,
        user_id: Optional[str] = None,
    ) -> None:
        handle = asyncio.current_task()
        if handle is None:
            return
        key = (account_id or "", user_id or "", task_id)
        with self._active_lock:
            self._active.setdefault(key, {}).setdefault(handle, False)
        # Close the race where cancellation commits after process middleware
        # checks the store but before this coroutine is visible in _active. This also
        # keeps cooperative cancellation correct in embedded runtimes that do
        # not start the background cancel-poll loop.
        if self.is_cancelling(task_id, account_id=account_id, user_id=user_id):
            for target in self._claim_user_cancel_handles({key}):
                target.get_loop().call_soon_threadsafe(target.cancel)

    async def unregister_active(
        self,
        task_id: str,
        account_id: Optional[str] = None,
        user_id: Optional[str] = None,
    ) -> None:
        """Release the current handle and finalize when no work remains."""
        handle = asyncio.current_task()
        if handle is not None:
            key = (account_id or "", user_id or "", task_id)
            with self._active_lock:
                handles = self._active.get(key)
                if handles is not None:
                    handles.pop(handle, None)
                    if not handles:
                        self._active.pop(key, None)
        await self._finalize(task_id, account_id, user_id)

    def _cancel_active(
        self,
        task_id: str,
        account_id: Optional[str] = None,
        user_id: Optional[str] = None,
    ) -> None:
        key = (account_id or "", user_id or "", task_id)
        for handle in self._claim_user_cancel_handles({key}):
            handle.get_loop().call_soon_threadsafe(handle.cancel)

    def _claim_user_cancel_handles(
        self, keys: set[tuple[str, str, str]]
    ) -> List[asyncio.Task[Any]]:
        """Atomically pick handles that still need a user-cancel signal.

        Marks each returned handle as signalled under the lock so concurrent
        cancel paths (API cancel, poll loop, late registration) never deliver
        more than one user-triggered ``cancel()`` to the same coroutine.
        """
        claimed: List[asyncio.Task[Any]] = []
        with self._active_lock:
            for key in keys:
                handles = self._active.get(key)
                if not handles:
                    continue
                for handle, signalled in handles.items():
                    if not signalled:
                        handles[handle] = True
                        claimed.append(handle)
        return claimed

    # ── Reads ──

    async def get(
        self,
        task_id: str,
        account_id: Optional[str] = None,
        user_id: Optional[str] = None,
    ) -> Optional[TaskRecord]:
        agg = await self._dispatcher.run(lambda: self._load(task_id, account_id, user_id))
        return _copy(agg.task) if agg is not None else None

    async def wait(
        self,
        task_id: str,
        account_id: Optional[str] = None,
        user_id: Optional[str] = None,
        timeout: Optional[float] = None,
        poll_interval: float = _TASK_WAIT_POLL_INTERVAL,
    ) -> TaskRecord:
        async def _poll() -> TaskRecord:
            while True:
                task = await self.get(task_id, account_id=account_id, user_id=user_id)
                if task is None:
                    raise KeyError(f"Task not found: {task_id}")
                if task.status in TERMINAL_TASK_STATUSES:
                    return task
                await asyncio.sleep(poll_interval)

        if timeout is None:
            return await _poll()
        return await asyncio.wait_for(_poll(), timeout)

    async def list_tasks(
        self,
        task_type: Optional[str] = None,
        status: Optional[str] = None,
        resource_id: Optional[str] = None,
        limit: int = 50,
        account_id: Optional[str] = None,
        user_id: Optional[str] = None,
    ) -> List[TaskRecord]:
        aggregates = await self._store_io.run(
            "list",
            lambda: run_to_completion(
                lambda: self._store.list(
                    account_id,
                    user_id=user_id,
                    task_type=task_type,
                    status=status,
                    resource_id=resource_id,
                    limit=limit,
                )
            ),
        )
        return [_copy(aggregate.task) for aggregate in aggregates]

    async def has_running(
        self,
        task_type: str,
        resource_id: str,
        account_id: Optional[str] = None,
        user_id: Optional[str] = None,
    ) -> bool:
        aggregates = await self._store_io.run(
            "list",
            lambda: run_to_completion(
                lambda: self._store.list(
                    account_id,
                    user_id=user_id,
                    task_type=task_type,
                    resource_id=resource_id,
                )
            ),
        )
        return any(
            aggregate.task.task_type == task_type
            and aggregate.task.resource_id == resource_id
            and aggregate.task.status in ACTIVE_TASK_STATUSES
            for aggregate in aggregates
        )

    async def has_work(
        self,
        task_id: str,
        account_id: Optional[str] = None,
        user_id: Optional[str] = None,
    ) -> bool:
        agg = await self._load(task_id, account_id, user_id)
        return bool(
            agg
            and (
                agg.has_open_work()
                or self._has_active_handles(task_id, account_id=account_id, user_id=user_id)
            )
        )

    async def wait_for_descendants(
        self,
        task_id: str,
        current_work_id: str,
        account_id: Optional[str] = None,
        user_id: Optional[str] = None,
    ) -> None:
        while True:
            agg = await self._load(task_id, account_id, user_id)
            if agg is None or not agg.has_open_work(exclude_work_id=current_work_id):
                return
            await asyncio.sleep(0.05)

    async def queue_status(
        self,
        task_id: str,
        account_id: Optional[str] = None,
        user_id: Optional[str] = None,
    ) -> Dict[str, Any]:
        agg = await self._load(task_id, account_id, user_id)
        return agg.queue_status() if agg is not None else {}

    async def wait_for_work(
        self,
        task_id: str,
        account_id: Optional[str] = None,
        user_id: Optional[str] = None,
        timeout: Optional[float] = None,
        poll_interval: float = 0.05,
    ) -> Dict[str, Any]:
        """Wait until all work owned by the task is terminal, then project stats."""

        async def _poll() -> Dict[str, Any]:
            while True:
                agg = await self._load(task_id, account_id, user_id)
                if agg is None:
                    raise KeyError(f"Task not found: {task_id}")
                if not agg.has_open_work():
                    return agg.queue_status()
                await asyncio.sleep(poll_interval)

        if timeout is None:
            return await _poll()
        return await asyncio.wait_for(_poll(), timeout)

    # ── Deletion ──

    async def delete_user_tasks(self, account_id: str, user_id: str) -> int:
        self._validate_owner(account_id, user_id)
        return await self._dispatcher.run(
            lambda: self._delete_user_tasks_on_owner(account_id, user_id)
        )

    async def _delete_user_tasks_on_owner(self, account_id: str, user_id: str) -> int:
        aggregates = await self._store_io.run(
            "list",
            lambda: run_to_completion(lambda: self._store.list(account_id, user_id=user_id)),
        )
        active = [a for a in aggregates if a.task.status in ACTIVE_TASK_STATUSES]
        if active:
            raise RuntimeError(
                "Cannot delete active task records: "
                + ", ".join(f"{a.task.task_id}({a.task.task_type})" for a in active)
            )
        deleted = 0
        for agg in aggregates:
            task_id = agg.task.task_id
            async with self._task_locks.acquire((account_id, user_id, task_id)):
                if agg.has_open_work() or self._has_active_handles(
                    task_id, account_id=account_id, user_id=user_id
                ):
                    raise RuntimeError(
                        f"Cannot delete active task record: {task_id}({agg.task.task_type})"
                    )
                await self._store_io.run(
                    "delete",
                    lambda tid=task_id: run_to_completion(
                        lambda: self._store.delete(tid, account_id=account_id, user_id=user_id)
                    ),
                )
                deleted += 1
        return deleted

    # ── Store helpers ──

    async def _load(
        self,
        task_id: str,
        account_id: Optional[str],
        user_id: Optional[str],
    ) -> Optional[TaskAggregate]:
        # Let the store serve cache hits before applying its own I/O limits.
        return await self._store.get(task_id, account_id=account_id, user_id=user_id)

    async def _mutate_task_with_retry(
        self,
        task_id: str,
        account_id: str,
        user_id: str,
        mutate: Callable[[TaskAggregate], bool],
    ) -> tuple[Optional[TaskAggregate], bool]:
        """Reload and replay one pure task transition on CAS conflict.

        Work mutations advance the aggregate revision, so a conflict may mean a
        descendant was added or settled after the task snapshot was read. Every
        attempt reloads the authoritative aggregate and reruns the transition;
        notably, finalization will stop if the reloaded aggregate has open work.
        """
        for attempt in range(_MAX_CAS_RETRIES):
            aggregate = await self._load(task_id, account_id, user_id)
            if aggregate is None or not mutate(aggregate):
                return aggregate, False
            try:
                await self._store_write("update", aggregate.task)
            except TaskWriteConflict:
                if attempt + 1 >= _MAX_CAS_RETRIES:
                    raise
                continue
            return aggregate, True
        raise AssertionError("unreachable CAS retry loop")

    async def _store_write(self, operation: str, task: TaskRecord) -> None:
        committed = False

        async def write() -> None:
            nonlocal committed
            if operation == "create":
                await self._store.create(task)
            else:
                if not await self._store.update(task):
                    raise TaskWriteConflict(
                        f"Task {task.task_id} changed concurrently; reload and retry"
                    )
            committed = True

        try:
            await self._store_io.run(operation, lambda: run_to_completion(write))
        except asyncio.CancelledError as exc:
            if committed:
                raise _CommittedMutationCancelled() from exc
            raise

    def count(self) -> int:
        """Return the number of tasks represented by the Store stats view."""
        return sum(sum(statuses.values()) for statuses in self.snapshot_counts_by_type().values())

    def snapshot_counts_by_type(self) -> Dict[str, Dict[str, int]]:
        """Task counts grouped by type/status for the metrics layer."""
        return self._store.snapshot_task_stats()


def _next_updated_at(task: TaskRecord) -> float:
    return max(time.time(), math.nextafter(task.updated_at, math.inf))


def _aggregate_owner(aggregate: Optional[TaskAggregate]) -> Optional[tuple[str, str]]:
    if aggregate is None or not aggregate.task.account_id or not aggregate.task.user_id:
        return None
    return aggregate.task.account_id, aggregate.task.user_id


def _copy(task: TaskRecord) -> TaskRecord:
    copied = deepcopy(task)
    copied.meta = _sanitize_task_result(copied.meta)
    copied.result = _sanitize_task_result(copied.result)
    # Task-owned credentials are private; only get_task_auth returns them.
    copied.auth = {}
    return copied
