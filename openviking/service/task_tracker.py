# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0
"""
Async Task Tracker for OpenViking.

Provides a lightweight registry for tracking background operations
(e.g. session commit with wait=false). Callers receive a task_id that can be
polled via the /tasks API to check completion status, results, or errors.

Design decisions:
  - Thread-safe (QueueManager workers run in separate threads).
  - TTL-based cleanup applies to the process-local cache.
  - Error messages are sanitized to avoid leaking sensitive data.
"""

import asyncio
import math
import re
import threading
import time
from copy import deepcopy
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from enum import Enum
from typing import Any, Dict, List, Optional
from uuid import uuid4

from openviking.service.task_store import TaskStore
from openviking.service.task_tracker_concurrency import (
    KeyedAsyncLockPool,
    OwnerLoopDispatcher,
    StoreIOLimiter,
    run_to_completion,
)
from openviking.service.task_work_index import QueueTaskMetadata, TaskWorkIndex
from openviking_cli.utils.logger import get_logger

logger = get_logger(__name__)


class _CommittedMutationCancelled(asyncio.CancelledError):
    """Caller cancellation observed after store and cache commit completed."""


class TaskStatus(str, Enum):
    """Lifecycle states of an async task."""

    PENDING = "pending"
    RUNNING = "running"
    CANCELLING = "cancelling"
    COMPLETED = "completed"
    FAILED = "failed"
    CANCELLED = "cancelled"


_TERMINAL_STATUSES = (TaskStatus.COMPLETED, TaskStatus.FAILED, TaskStatus.CANCELLED)
_ACTIVE_STATUSES = (TaskStatus.PENDING, TaskStatus.RUNNING, TaskStatus.CANCELLING)

_CANCELLABLE_TASK_TYPES = {
    "add_resource",
    "session_commit",
    "admin_reindex",
    "snapshot_restore_reindex",
}


@dataclass
class TaskRecord:
    """Immutable snapshot of an async task."""

    task_id: str
    task_type: str  # e.g. "session_commit"
    status: TaskStatus = TaskStatus.PENDING
    created_at: float = field(default_factory=time.time)
    updated_at: float = field(default_factory=time.time)
    resource_id: Optional[str] = None  # e.g. session_id
    account_id: Optional[str] = None
    user_id: Optional[str] = None
    meta: Dict[str, Any] = field(default_factory=dict)
    stage: Optional[str] = None
    result: Optional[Dict[str, Any]] = None
    error: Optional[str] = None

    def to_dict(self) -> Dict[str, Any]:
        """Serialize for JSON response."""
        d = asdict(self)
        d["status"] = self.status.value
        d["created_at_iso"] = datetime.fromtimestamp(self.created_at, tz=timezone.utc).isoformat()
        d["updated_at_iso"] = datetime.fromtimestamp(self.updated_at, tz=timezone.utc).isoformat()
        d["result"] = _sanitize_task_result(d.get("result"))
        d["meta"] = _sanitize_task_result(d.get("meta"))
        d.pop("account_id", None)
        d.pop("user_id", None)
        return d


# ── Singleton ──

_instance: Optional["TaskTracker"] = None
_init_lock = threading.Lock()


def get_task_tracker() -> "TaskTracker":
    """Get the global TaskTracker singleton installed by service storage initialization."""
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
    """Remove potential secrets from error messages."""
    sanitized = _SENSITIVE_PATTERNS.sub("[REDACTED]", error)
    if len(sanitized) > _MAX_ERROR_LEN:
        sanitized = sanitized[:_MAX_ERROR_LEN] + "...[truncated]"
    return sanitized


def _sanitize_task_result(result: Any) -> Any:
    """Remove sensitive fields from task results before exposing snapshots."""
    if isinstance(result, dict):
        return {
            key: _sanitize_task_result(value)
            for key, value in result.items()
            if key not in _SENSITIVE_RESULT_KEYS
        }
    if isinstance(result, list):
        return [_sanitize_task_result(item) for item in result]
    return result


# ── TaskTracker ──


class TaskTracker:
    """Async task tracker with persistent storage and a process-local cache.

    Mutations are serialized per task on one owner event loop. The thread lock
    only protects short, synchronous accesses to immutable cache snapshots.
    """

    MAX_TASKS = 10_000
    TTL_COMPLETED = 86_400  # 24 hours
    TTL_FAILED = 604_800  # 7 days
    CLEANUP_INTERVAL = 300  # 5 minutes

    def __init__(self, store: TaskStore, *, max_concurrent_store_io: int = 8) -> None:
        self._store = store
        self._tasks: Dict[str, TaskRecord] = {}
        self._lock = threading.Lock()
        # The keyed registries provide process-local ordering. A deployment with
        # multiple TaskTracker writers must add store-level revision/CAS first.
        self._dispatcher = OwnerLoopDispatcher()
        self._task_locks = KeyedAsyncLockPool[str]()
        self._business_locks = KeyedAsyncLockPool[tuple[str, str, str, str]]()
        self._store_io = StoreIOLimiter(max_concurrent_store_io)
        self._cleanup_task: Optional[asyncio.Task] = None
        self._work_index = TaskWorkIndex()
        self._install_work_index_callbacks()
        logger.info(
            "[TaskTracker] Initialized (store=%s, max_tasks=%d)",
            self._store.__class__.__name__,
            self.MAX_TASKS,
        )

    # ── Lifecycle ──

    def _install_work_index_callbacks(self) -> None:
        self._work_index.set_callbacks(
            finalize_before_ack=self._finalize_before_ack,
            is_cancellation_requested=self.is_cancellation_requested,
        )

    def attach_work_index(self, work_index: TaskWorkIndex) -> None:
        """Use the QueueManager-owned index as the task lifecycle authority."""
        self._work_index = work_index
        self._dispatcher.bind_current_loop()
        self._install_work_index_callbacks()

    async def restore_work_tasks(self, owners: Dict[str, tuple[str, str]]) -> None:
        """Restore task records referenced by rebuilt QueueFS work."""
        for task_id, (account_id, user_id) in owners.items():
            await self.get(task_id, account_id=account_id, user_id=user_id)

    async def _finalize_before_ack(self, metadata: QueueTaskMetadata) -> None:
        """Persist the terminal state before QueueFS removes the last recovery message."""
        await self._dispatcher.run(
            lambda: self._finalize_task_on_owner(
                metadata.task_id,
                account_id=metadata.account_id or None,
                user_id=metadata.user_id or None,
            )
        )

    def is_cancellation_requested(self, task_id: str) -> bool:
        """Fast thread-safe status check used by queue workers."""
        with self._lock:
            task = self._tasks.get(task_id)
            return task is not None and task.status in (
                TaskStatus.CANCELLING,
                TaskStatus.CANCELLED,
            )

    def start_cleanup_loop(self) -> None:
        """Start the background TTL cleanup coroutine.

        Safe to call multiple times; subsequent calls are no-ops.
        Must be called from within a running event loop.
        """
        if self._cleanup_task is not None and not self._cleanup_task.done():
            return
        self._dispatcher.bind_current_loop()
        self._cleanup_task = asyncio.create_task(self._cleanup_loop())
        logger.debug("[TaskTracker] Cleanup loop started")

    def stop_cleanup_loop(self) -> None:
        """Cancel the background cleanup task. Safe to call if not started."""
        if self._cleanup_task is not None and not self._cleanup_task.done():
            self._cleanup_task.cancel()
            logger.debug("[TaskTracker] Cleanup loop stopped")

    async def _cleanup_loop(self) -> None:
        while True:
            try:
                await asyncio.sleep(self.CLEANUP_INTERVAL)
                await self._evict_expired()
            except asyncio.CancelledError:
                break
            except Exception:
                logger.exception("[TaskTracker] Cleanup error")

    async def _evict_expired(self) -> None:
        """Remove expired tasks and enforce MAX_TASKS."""
        await self._dispatcher.run(self._evict_expired_on_owner)

    async def _evict_expired_on_owner(self) -> None:
        now = time.time()
        with self._lock:
            expired_ids = [
                task_id for task_id, task in self._tasks.items() if self._is_expired(task, now)
            ]

        evicted_count = 0
        for task_id in expired_ids:
            evicted_count += await self._delete_expired_task_on_owner(task_id, now)

        with self._lock:
            if len(self._tasks) > self.MAX_TASKS:
                sorted_tasks = sorted(self._tasks.items(), key=lambda x: x[1].created_at)
                excess = len(self._tasks) - self.MAX_TASKS
                for tid, _ in sorted_tasks[:excess]:
                    self._tasks.pop(tid, None)

        if evicted_count:
            logger.debug("[TaskTracker] Evicted %d expired tasks", evicted_count)

    def _is_expired(self, task: TaskRecord, now: float) -> bool:
        age = now - task.updated_at
        if task.status in (TaskStatus.COMPLETED, TaskStatus.CANCELLED):
            return age > self.TTL_COMPLETED
        return task.status == TaskStatus.FAILED and age > self.TTL_FAILED

    async def _delete_expired_task_on_owner(self, task_id: str, now: float) -> bool:
        async with self._task_locks.acquire(task_id):
            task = self._cached_task(task_id)
            if task is None or not self._is_expired(task, now):
                return False
            account_id = task.account_id
            user_id = task.user_id
            if not account_id or not user_id:
                logger.warning(
                    "[TaskTracker] Cannot delete expired ownerless task %s",
                    task_id,
                )
                return False
            try:
                await self._store_io.run(
                    "delete",
                    lambda: run_to_completion(
                        lambda: self._store.delete(
                            task_id,
                            account_id=account_id,
                            user_id=user_id,
                        )
                    ),
                )
            except Exception:
                logger.warning(
                    "[TaskTracker] Failed to delete expired task %s",
                    task_id,
                    exc_info=True,
                )
                return False

            with self._lock:
                self._tasks.pop(task_id, None)
            return True

    @staticmethod
    def _matches_owner(
        task: TaskRecord,
        account_id: Optional[str] = None,
        user_id: Optional[str] = None,
    ) -> bool:
        """Return True when a task belongs to the requested owner filter."""
        if account_id is not None and task.account_id != account_id:
            return False
        if user_id is not None and task.user_id != user_id:
            return False
        return True

    @staticmethod
    def _validate_owner(account_id: str, user_id: str) -> None:
        """Reject ownerless task creation for user-originated background work."""
        if not account_id or not user_id:
            raise ValueError("Task ownership requires non-empty account_id and user_id")

    # ── CRUD ──

    async def create(
        self,
        task_type: str,
        resource_id: Optional[str] = None,
        *,
        account_id: str,
        user_id: str,
        task_id: Optional[str] = None,
        meta: Optional[Dict[str, Any]] = None,
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
        )
        return await self._dispatcher.run(lambda: self._create_on_owner(task, task_id is not None))

    async def _create_on_owner(self, task: TaskRecord, check_existing: bool) -> TaskRecord:
        async with self._task_locks.acquire(task.task_id):
            if check_existing:
                existing = self._cached_task(task.task_id)
                if existing is not None:
                    if not self._matches_owner(existing, task.account_id, task.user_id):
                        raise ValueError(
                            f"Task ID already belongs to another owner: {task.task_id}"
                        )
                    return self._copy(existing)
                existing = await self._load_from_store(
                    task.task_id,
                    task.account_id or "",
                    task.user_id,
                )
                if existing is not None:
                    self._publish_task(existing)
                    return self._copy(existing)
            await self._persist_and_publish("create", task)
        logger.debug(
            "[TaskTracker] Created task %s type=%s resource=%s",
            task.task_id,
            task.task_type,
            task.resource_id,
        )
        return self._copy(task)

    async def create_if_no_running(
        self,
        task_type: str,
        resource_id: str,
        *,
        account_id: str,
        user_id: str,
    ) -> Optional[TaskRecord]:
        """Atomically check for running tasks and create a new one if none exist.

        Returns TaskRecord on success, None if a running task already exists.
        This eliminates the race condition between has_running() and create().
        """
        self._validate_owner(account_id, user_id)
        business_key = (account_id, user_id, task_type, resource_id)
        return await self._dispatcher.run(
            lambda: self._create_if_no_running_on_owner(
                business_key,
                task_type,
                resource_id,
                account_id,
                user_id,
            )
        )

    async def _create_if_no_running_on_owner(
        self,
        business_key: tuple[str, str, str, str],
        task_type: str,
        resource_id: str,
        account_id: str,
        user_id: str,
    ) -> Optional[TaskRecord]:
        async with self._business_locks.acquire(business_key):
            self._merge_loaded_tasks(await self._load_all_from_store(account_id, user_id))

            tasks = self._cache_snapshot()
            has_active = any(
                t.task_type == task_type
                and t.resource_id == resource_id
                and self._matches_owner(t, account_id, user_id)
                and t.status in _ACTIVE_STATUSES
                for t in tasks
            )
            if has_active:
                return None
            task = TaskRecord(
                task_id=str(uuid4()),
                task_type=task_type,
                resource_id=resource_id,
                account_id=account_id,
                user_id=user_id,
            )
            async with self._task_locks.acquire(task.task_id):
                await self._persist_and_publish("create", task)
        logger.debug(
            "[TaskTracker] Created task %s type=%s resource=%s",
            task.task_id,
            task_type,
            resource_id,
        )
        return self._copy(task)

    async def start(
        self,
        task_id: str,
        account_id: Optional[str] = None,
        user_id: Optional[str] = None,
        stage: Optional[str] = None,
    ) -> None:
        """Transition task to RUNNING."""
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
        async with self._task_locks.acquire(task_id):
            task = await self._load_for_update(task_id, account_id, user_id)
            if task and task.status in (TaskStatus.PENDING, TaskStatus.RUNNING):
                updated = deepcopy(task)
                updated.status = TaskStatus.RUNNING
                if stage is not None:
                    updated.stage = stage
                updated.updated_at = self._next_updated_at(task)
                await self._persist_and_publish("update", updated)

    async def update_stage(
        self,
        task_id: str,
        stage: str,
        account_id: Optional[str] = None,
        user_id: Optional[str] = None,
    ) -> None:
        """Update task stage without changing its lifecycle status."""
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
        async with self._task_locks.acquire(task_id):
            task = await self._load_for_update(task_id, account_id, user_id)
            if task and task.status in (TaskStatus.PENDING, TaskStatus.RUNNING):
                updated = deepcopy(task)
                updated.stage = stage
                updated.updated_at = self._next_updated_at(task)
                await self._persist_and_publish("update", updated)

    async def complete(
        self,
        task_id: str,
        result: Dict[str, Any],
        account_id: Optional[str] = None,
        user_id: Optional[str] = None,
        *,
        resource_id: Optional[str] = None,
    ) -> None:
        """Record successful completion and finalize after owned work settles."""
        await self._record_outcome(
            task_id,
            account_id,
            user_id,
            result=result,
            resource_id=resource_id,
        )

    async def fail(
        self,
        task_id: str,
        error: str,
        account_id: Optional[str] = None,
        user_id: Optional[str] = None,
    ) -> None:
        """Record failure and finalize after owned work settles."""
        await self._record_outcome(task_id, account_id, user_id, error=error)

    async def _record_outcome(
        self,
        task_id: str,
        account_id: Optional[str],
        user_id: Optional[str],
        *,
        result: Optional[Dict[str, Any]] = None,
        error: Optional[str] = None,
        resource_id: Optional[str] = None,
    ) -> None:
        await self._dispatcher.run(
            lambda: self._record_outcome_on_owner(
                task_id,
                account_id,
                user_id,
                result=result,
                error=error,
                resource_id=resource_id,
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
        cancellation: asyncio.CancelledError | None = None
        outcome_persisted = False
        async with self._task_locks.acquire(task_id):
            task = await self._load_for_update(task_id, account_id, user_id)
            if task and task.status in (TaskStatus.PENDING, TaskStatus.RUNNING):
                updated = deepcopy(task)
                if result is not None:
                    updated.result = deepcopy(result)
                    if resource_id is not None:
                        updated.resource_id = resource_id
                if error is not None and updated.error is None:
                    updated.error = _sanitize_error(error)
                work_error = self._work_index.failure(task_id)
                if work_error and updated.error is None:
                    updated.error = _sanitize_error(work_error)
                updated.updated_at = self._next_updated_at(task)
                try:
                    await self._persist_and_publish("update", updated)
                except _CommittedMutationCancelled as exc:
                    cancellation = exc
                    outcome_persisted = True
                else:
                    outcome_persisted = True
        if outcome_persisted:
            try:
                await run_to_completion(
                    lambda: self._finalize_task_on_owner(
                        task_id,
                        account_id=account_id,
                        user_id=user_id,
                    )
                )
            except asyncio.CancelledError as exc:
                cancellation = cancellation or exc
        else:
            await self._finalize_task_on_owner(task_id, account_id=account_id, user_id=user_id)
        if cancellation is not None:
            raise cancellation

    async def cancel(
        self,
        task_id: str,
        account_id: Optional[str] = None,
        user_id: Optional[str] = None,
    ) -> Optional[TaskRecord]:
        """Request cooperative cancellation and return the current task snapshot."""
        return await self._dispatcher.run(
            lambda: self._cancel_on_owner(task_id, account_id, user_id)
        )

    async def _cancel_on_owner(
        self,
        task_id: str,
        account_id: Optional[str],
        user_id: Optional[str],
    ) -> Optional[TaskRecord]:
        cancellation: asyncio.CancelledError | None = None
        cancellation_persisted = False
        async with self._task_locks.acquire(task_id):
            task = await self._load_for_update(task_id, account_id, user_id)
            if task is None:
                return None
            if task.status == TaskStatus.CANCELLED:
                return self._copy(task)
            if task.status != TaskStatus.CANCELLING:
                if task.task_type not in _CANCELLABLE_TASK_TYPES:
                    raise ValueError(f"Task type '{task.task_type}' does not support cancellation")
                if task.status in _TERMINAL_STATUSES:
                    raise ValueError(f"Task is already {task.status.value}")

                updated = deepcopy(task)
                updated.status = TaskStatus.CANCELLING
                updated.updated_at = self._next_updated_at(task)
                try:
                    await self._persist_and_publish("update", updated)
                except _CommittedMutationCancelled as exc:
                    cancellation = exc
                    cancellation_persisted = True
                else:
                    cancellation_persisted = True

        async def finish_cancellation() -> None:
            self._work_index.cancel_active(task_id)
            await self._finalize_task_on_owner(
                task_id,
                account_id=account_id,
                user_id=user_id,
            )

        if cancellation_persisted:
            try:
                await run_to_completion(finish_cancellation)
            except asyncio.CancelledError as exc:
                cancellation = cancellation or exc
        else:
            await finish_cancellation()
        task = self._cached_task(task_id)
        if task is None or not self._matches_owner(task, account_id, user_id):
            return None
        if cancellation is not None:
            raise cancellation
        return self._copy(task)

    async def _finalize_task(
        self,
        task_id: str,
        account_id: Optional[str] = None,
        user_id: Optional[str] = None,
    ) -> None:
        """Apply the task's terminal outcome after all owned work is settled."""
        await self._dispatcher.run(
            lambda: self._finalize_task_on_owner(task_id, account_id, user_id)
        )

    async def _finalize_task_on_owner(
        self,
        task_id: str,
        account_id: Optional[str] = None,
        user_id: Optional[str] = None,
    ) -> None:
        if self._work_index.has_work(task_id):
            return
        async with self._task_locks.acquire(task_id):
            task = await self._load_for_update(task_id, account_id, user_id)
            if task and task.status in _ACTIVE_STATUSES:
                if self._work_index.has_work(task_id):
                    return
                updated = deepcopy(task)
                if updated.status != TaskStatus.CANCELLING:
                    work_error = self._work_index.failure(task_id)
                    if work_error and updated.error is None:
                        updated.error = _sanitize_error(work_error)
                if updated.status == TaskStatus.CANCELLING:
                    updated.status = TaskStatus.CANCELLED
                elif updated.error is not None:
                    updated.status = TaskStatus.FAILED
                elif updated.result is not None:
                    updated.status = TaskStatus.COMPLETED
                else:
                    return
                updated.stage = updated.status.value
                updated.updated_at = self._next_updated_at(task)
                await self._persist_and_publish("update", updated)
                self._work_index.clear_failure(task_id)
                logger.info("[TaskTracker] Task %s %s", task_id, updated.status.value)

    async def wait(
        self,
        task_id: str,
        account_id: Optional[str] = None,
        user_id: Optional[str] = None,
        timeout: Optional[float] = None,
        poll_interval: float = 0.05,
    ) -> TaskRecord:
        """Wait for one task's terminal state without changing its lifecycle."""

        async def _poll() -> TaskRecord:
            while True:
                task = await self.get(task_id, account_id=account_id, user_id=user_id)
                if task is None:
                    raise KeyError(f"Task not found: {task_id}")
                if task.status in _TERMINAL_STATUSES:
                    return task
                await asyncio.sleep(poll_interval)

        if timeout is None:
            return await _poll()
        return await asyncio.wait_for(_poll(), timeout)

    async def wait_for_descendants(self, task_id: str, current_work_id: str) -> None:
        """Wait on the same durable work index used by completion and cancellation."""
        while self._work_index.has_work(task_id, exclude_work_id=current_work_id):
            await asyncio.sleep(0.05)

    def register_running_task(self, task_id: str) -> None:
        """Register the current asyncio task so cancellation can interrupt it."""
        active_task = asyncio.current_task()
        if active_task is not None:
            self._work_index.register_active(task_id, active_task)

    async def unregister_running_task(self, task_id: str) -> None:
        """Release direct background work and persist its terminal outcome."""
        active_task = asyncio.current_task()
        if active_task is not None:
            self._work_index.unregister_active(task_id, active_task)
        await self._finalize_task(task_id)

    async def get(
        self,
        task_id: str,
        account_id: Optional[str] = None,
        user_id: Optional[str] = None,
    ) -> Optional[TaskRecord]:
        """Look up a single task. Returns a snapshot copy (None if not found)."""
        task = self._cached_task(task_id)
        if task is not None:
            if not self._matches_owner(task, account_id, user_id):
                return None
            return self._copy(task)
        if account_id is None:
            return None
        return await self._dispatcher.run(lambda: self._get_on_owner(task_id, account_id, user_id))

    async def _get_on_owner(
        self,
        task_id: str,
        account_id: str,
        user_id: Optional[str],
    ) -> Optional[TaskRecord]:
        task = self._cached_task(task_id)
        if task is None:
            task = await self._load_from_store(task_id, account_id, user_id)
            if task is not None:
                self._merge_loaded_tasks([task])
                task = self._cached_task(task_id)
        if task is None or not self._matches_owner(task, account_id, user_id):
            return None
        return self._copy(task)

    async def list_tasks(
        self,
        task_type: Optional[str] = None,
        status: Optional[str] = None,
        resource_id: Optional[str] = None,
        limit: int = 50,
        account_id: Optional[str] = None,
        user_id: Optional[str] = None,
    ) -> List[TaskRecord]:
        """List tasks with optional filters. Most-recent first. Returns snapshot copies."""
        return await self._dispatcher.run(
            lambda: self._list_tasks_on_owner(
                task_type,
                status,
                resource_id,
                limit,
                account_id,
                user_id,
            )
        )

    async def _list_tasks_on_owner(
        self,
        task_type: Optional[str],
        status: Optional[str],
        resource_id: Optional[str],
        limit: int,
        account_id: Optional[str],
        user_id: Optional[str],
    ) -> List[TaskRecord]:
        if account_id is not None:
            self._merge_loaded_tasks(await self._load_all_from_store(account_id, user_id))
        source = self._cache_snapshot()
        tasks = [self._copy(t) for t in source if self._matches_owner(t, account_id, user_id)]
        if task_type:
            tasks = [t for t in tasks if t.task_type == task_type]
        if status:
            tasks = [t for t in tasks if t.status.value == status]
        if resource_id:
            tasks = [t for t in tasks if t.resource_id == resource_id]
        tasks.sort(key=lambda t: t.created_at, reverse=True)
        return tasks[:limit]

    async def has_running(
        self,
        task_type: str,
        resource_id: str,
        account_id: Optional[str] = None,
        user_id: Optional[str] = None,
    ) -> bool:
        """Check if there is already a running task for the given type+resource."""
        return await self._dispatcher.run(
            lambda: self._has_running_on_owner(
                task_type,
                resource_id,
                account_id,
                user_id,
            )
        )

    async def _has_running_on_owner(
        self,
        task_type: str,
        resource_id: str,
        account_id: Optional[str],
        user_id: Optional[str],
    ) -> bool:
        if account_id is not None:
            self._merge_loaded_tasks(await self._load_all_from_store(account_id, user_id))
        tasks = self._cache_snapshot()
        return any(
            t.task_type == task_type
            and t.resource_id == resource_id
            and self._matches_owner(t, account_id, user_id)
            and t.status in _ACTIVE_STATUSES
            for t in tasks
        )

    async def _load_for_update(
        self,
        task_id: str,
        account_id: Optional[str],
        user_id: Optional[str],
    ) -> Optional[TaskRecord]:
        task = self._cached_task(task_id)
        if task is not None:
            return task if self._matches_owner(task, account_id, user_id) else None
        if account_id is None or user_id is None:
            return None
        return await self._load_from_store(task_id, account_id, user_id)

    @staticmethod
    def _record_from_payload(payload: Dict[str, Any]) -> TaskRecord:
        data = dict(payload)
        data["status"] = TaskStatus(data["status"])
        return TaskRecord(**data)

    async def _load_from_store(
        self,
        task_id: str,
        account_id: str,
        user_id: Optional[str],
    ) -> Optional[TaskRecord]:
        payload = await self._store_io.run(
            "get",
            lambda: self._store.get(task_id, account_id=account_id, user_id=user_id),
        )
        if payload is None:
            return None
        return self._record_from_payload(payload)

    async def _load_all_from_store(
        self, account_id: str, user_id: Optional[str]
    ) -> List[TaskRecord]:
        return [
            self._record_from_payload(payload)
            for payload in await self._store_io.run(
                "list",
                lambda: self._store.list(account_id, user_id=user_id),
            )
        ]

    async def _persist_and_publish(self, operation: str, task: TaskRecord) -> None:
        committed = False

        async def write_and_publish() -> None:
            nonlocal committed
            if operation == "create":
                await self._store.create(task)
            else:
                await self._store.update(task)
            self._publish_task(task)
            committed = True

        # Semaphore/lock waits remain cancellable. Once the store operation is
        # admitted, settle the physical write and publish its cache snapshot
        # before propagating caller cancellation.
        try:
            await self._store_io.run(
                operation,
                lambda: run_to_completion(write_and_publish),
            )
        except asyncio.CancelledError as exc:
            if committed:
                raise _CommittedMutationCancelled() from exc
            raise

    def _cached_task(self, task_id: str) -> Optional[TaskRecord]:
        with self._lock:
            task = self._tasks.get(task_id)
        return deepcopy(task) if task is not None else None

    def _cache_snapshot(self) -> List[TaskRecord]:
        with self._lock:
            tasks = list(self._tasks.values())
        return [deepcopy(task) for task in tasks]

    def _publish_task(self, task: TaskRecord) -> None:
        published = deepcopy(task)
        with self._lock:
            self._tasks[task.task_id] = published

    def _merge_loaded_tasks(self, loaded_tasks: List[TaskRecord]) -> None:
        candidates = [deepcopy(task) for task in loaded_tasks]
        with self._lock:
            for loaded in candidates:
                cached = self._tasks.get(loaded.task_id)
                if cached is None or loaded.updated_at > cached.updated_at:
                    self._tasks[loaded.task_id] = loaded

    @staticmethod
    def _next_updated_at(task: TaskRecord) -> float:
        return max(time.time(), math.nextafter(task.updated_at, math.inf))

    @staticmethod
    def _copy(task: TaskRecord) -> TaskRecord:
        """Return a defensive copy of a TaskRecord."""
        copied = deepcopy(task)
        copied.meta = _sanitize_task_result(copied.meta)
        copied.result = _sanitize_task_result(copied.result)
        return copied

    def count(self) -> int:
        """Return total task count."""
        with self._lock:
            return len(self._tasks)

    def snapshot_counts_by_type(self) -> Dict[str, Dict[str, int]]:
        """Return a snapshot of task counts grouped by task_type and status."""
        from collections import defaultdict

        grouped: Dict[str, Dict[str, int]] = defaultdict(lambda: defaultdict(int))
        with self._lock:
            tasks = list(self._tasks.values())
        for t in tasks:
            grouped[t.task_type][t.status.value] += 1
        return {k: dict(v) for k, v in grouped.items()}
