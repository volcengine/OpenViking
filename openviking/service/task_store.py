# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0
"""Task/Work storage contract and the local file-backed implementation."""

from __future__ import annotations

import asyncio
import heapq
import inspect
import json
import threading
import time
from collections import defaultdict
from collections.abc import AsyncIterator, Callable, Collection
from copy import deepcopy
from dataclasses import dataclass, field, fields
from enum import Enum
from typing import Any, Dict, List, Optional, Protocol

from openviking.pyagfs import AsyncAGFSClient
from openviking.pyagfs.exceptions import AGFSAlreadyExistsError, AGFSNotFoundError
from openviking.service.task_domain import (
    ACTIVE_TASK_STATUSES,
    MAX_QUEUE_ERROR_DETAILS,
    TERMINAL_TASK_STATUSES,
    DeletedTask,
    DeleteExpiredResult,
    QueueWorkSummary,
    TaskRecord,
    TaskRetentionPolicy,
    TaskStatus,
    WorkRecord,
    WorkRecoveryMode,
    WorkState,
    WorkSummary,
    first_work_error,
    processing_seconds,
    sanitize_error,
    summarize_works,
)
from openviking.service.task_events import append_task_event
from openviking.service.task_tracker_concurrency import (
    KeyedAsyncLockPool,
    run_to_completion,
)

SYSTEM_TASK_ACCOUNT_ID = "_system"
SYSTEM_TASK_USER_ID = "root"
TaskStatsSnapshot = Dict[str, Dict[str, int]]


class WorkRegistration(str, Enum):
    CREATED = "created"
    EXISTING = "existing"
    REJECTED = "rejected"

    def __bool__(self) -> bool:
        return self is not WorkRegistration.REJECTED


class StoreOperationOutcome(str, Enum):
    APPLIED = "applied"
    NOOP = "noop"
    REJECTED = "rejected"


class StoreOperationCode(str, Enum):
    OK = "ok"
    NOT_FOUND = "not_found"
    ACTIVE_TASK = "active_task"
    ALREADY_TERMINAL = "already_terminal"
    ALREADY_CANCELLED = "already_cancelled"
    INVALID_STATE = "invalid_state"
    NOT_CANCELLABLE = "not_cancellable"
    OPEN_WORK = "open_work"
    WORK_NOT_OPEN = "work_not_open"
    DUPLICATE = "duplicate"


@dataclass(frozen=True)
class StoreOperationResult:
    outcome: StoreOperationOutcome
    code: StoreOperationCode = StoreOperationCode.OK
    message: Optional[str] = None

    @property
    def accepted(self) -> bool:
        return self.outcome is not StoreOperationOutcome.REJECTED

    @property
    def is_applied(self) -> bool:
        return self.outcome is StoreOperationOutcome.APPLIED

    @classmethod
    def applied(cls, code: StoreOperationCode = StoreOperationCode.OK) -> "StoreOperationResult":
        return cls(StoreOperationOutcome.APPLIED, code)

    @classmethod
    def noop(
        cls,
        code: StoreOperationCode = StoreOperationCode.OK,
        message: Optional[str] = None,
    ) -> "StoreOperationResult":
        return cls(StoreOperationOutcome.NOOP, code, message)

    @classmethod
    def rejected(
        cls,
        code: StoreOperationCode,
        message: Optional[str] = None,
    ) -> "StoreOperationResult":
        return cls(StoreOperationOutcome.REJECTED, code, message)


@dataclass(frozen=True)
class TaskDeletionBlocker:
    task_id: str
    account_id: str
    user_id: str
    task_type: str
    code: StoreOperationCode
    message: str


@dataclass(frozen=True)
class DeleteTasksResult:
    deleted: tuple[DeletedTask, ...] = ()
    result: StoreOperationResult = field(default_factory=StoreOperationResult.applied)
    blockers: tuple[TaskDeletionBlocker, ...] = ()

    @property
    def accepted(self) -> bool:
        return self.result.accepted


class TaskAlreadyActive(RuntimeError):
    def __init__(self, task_type: str, resource_id: Optional[str]) -> None:
        self.task_type = task_type
        self.resource_id = resource_id
        super().__init__(f"An active {task_type} task already exists for {resource_id}")


class TaskWorkStore(Protocol):
    """Atomic semantic operations required by TaskTracker.

    Implementations own transaction and revision details. Callers never replace
    a complete Task/Work aggregate or perform compare-and-set retries.
    """

    @property
    def work_recovery_mode(self) -> WorkRecoveryMode: ...

    async def create(self, task: TaskRecord, *, reject_if_active: bool = False) -> TaskRecord: ...

    async def get_task(
        self,
        task_id: str,
        *,
        account_id: Optional[str] = None,
        user_id: Optional[str] = None,
    ) -> Optional[TaskRecord]: ...

    async def list(
        self,
        account_id: Optional[str] = None,
        *,
        user_id: Optional[str] = None,
        task_type: Optional[str] = None,
        status: Optional[str] = None,
        resource_id: Optional[str] = None,
        limit: Optional[int] = None,
        before: tuple[float, str] | None = None,
        include_internal: bool = True,
        q: Optional[str] = None,
    ) -> List[TaskRecord]: ...

    async def delete_tasks(
        self,
        task_id: Optional[str] = None,
        *,
        account_id: str,
        user_id: Optional[str] = None,
        allow_active_types: Collection[str] = (),
    ) -> DeleteTasksResult: ...

    async def delete_expired(
        self, policy: TaskRetentionPolicy, *, now: float, limit: int
    ) -> DeleteExpiredResult: ...

    def snapshot_task_stats(self) -> TaskStatsSnapshot: ...

    async def start_task(
        self,
        task_id: str,
        *,
        stage: Optional[str],
        account_id: Optional[str],
        user_id: Optional[str],
    ) -> StoreOperationResult: ...

    async def update_stage(
        self,
        task_id: str,
        stage: str,
        *,
        meta: Optional[Dict[str, Any]],
        account_id: Optional[str],
        user_id: Optional[str],
    ) -> StoreOperationResult: ...

    async def update_auth(
        self,
        task_id: str,
        values: Dict[str, Any],
        *,
        account_id: str,
        user_id: str,
    ) -> StoreOperationResult: ...

    async def merge_meta(
        self,
        task_id: str,
        values: Dict[str, Any],
        *,
        account_id: str,
        user_id: str,
    ) -> StoreOperationResult: ...

    async def record_event(
        self,
        task_id: str,
        kind: str,
        *,
        operation: Optional[str],
        account_id: str,
        user_id: str,
    ) -> StoreOperationResult: ...

    async def record_outcome(
        self,
        task_id: str,
        *,
        result: Optional[Dict[str, Any]],
        error: Optional[str],
        resource_id: Optional[str],
        cancelled: bool,
        account_id: Optional[str],
        user_id: Optional[str],
    ) -> StoreOperationResult: ...

    async def request_cancellation(
        self,
        task_id: str,
        *,
        cancellable_types: Collection[str],
        allow_terminal: bool,
        account_id: str,
        user_id: str,
    ) -> StoreOperationResult: ...

    async def mark_enqueue_complete(
        self,
        task_id: str,
        work_id: str,
        *,
        account_id: str,
        user_id: str,
    ) -> StoreOperationResult: ...

    async def is_enqueue_complete(
        self,
        task_id: str,
        work_id: str,
        *,
        account_id: str,
        user_id: str,
    ) -> bool: ...

    async def add_work(
        self, work: WorkRecord, *, account_id: str, user_id: str
    ) -> StoreOperationResult: ...

    async def reconcile_delivery(
        self, work: WorkRecord, *, account_id: str, user_id: str
    ) -> StoreOperationResult: ...

    async def discard_work(
        self, task_id: str, work_id: str, *, account_id: str, user_id: str
    ) -> StoreOperationResult: ...

    async def get_work(
        self, task_id: str, work_id: str, *, account_id: str, user_id: str
    ) -> Optional[WorkRecord]: ...

    async def get_works(
        self,
        task_id: str,
        work_ids: Collection[str],
        *,
        account_id: Optional[str],
        user_id: Optional[str],
    ) -> Dict[str, WorkRecord]: ...

    async def start_work(
        self, task_id: str, work_id: str, *, account_id: str, user_id: str
    ) -> StoreOperationResult: ...

    async def finish_work(
        self,
        task_id: str,
        work_id: str,
        state: WorkState,
        *,
        error: Optional[str],
        errors: Optional[Collection[str]],
        context_count_delta: int,
        account_id: str,
        user_id: str,
    ) -> StoreOperationResult: ...

    async def requeue_work(
        self,
        task_id: str,
        work_id: str,
        *,
        delta: int,
        account_id: str,
        user_id: str,
    ) -> StoreOperationResult: ...

    async def record_work_error(
        self,
        task_id: str,
        work_id: str,
        error: str,
        *,
        account_id: str,
        user_id: str,
    ) -> StoreOperationResult: ...

    async def settle_work(
        self, task_id: str, work_id: str, *, account_id: str, user_id: str
    ) -> StoreOperationResult: ...

    async def has_open_work(
        self,
        task_id: str,
        *,
        exclude_work_id: Optional[str],
        account_id: Optional[str],
        user_id: Optional[str],
    ) -> bool: ...

    async def summarize_works(
        self,
        task_id: str,
        *,
        work_ids: Optional[Collection[str]],
        account_id: Optional[str],
        user_id: Optional[str],
    ) -> WorkSummary: ...

    async def filter_cancellation_requested(
        self,
        task_ids: Collection[str],
        *,
        account_id: str,
        user_id: str,
    ) -> set[str]: ...

    async def get_task_status(
        self,
        task_id: str,
        *,
        account_id: str,
        user_id: str,
    ) -> Optional[TaskStatus]: ...


# Compatibility name for integrations that only annotate the constructor.
TaskStore = TaskWorkStore


@dataclass
class _WorkSet:
    works: Dict[str, WorkRecord]
    open_count: int = 0


@dataclass(frozen=True)
class _WorkMutationResult:
    changed: bool
    work: Optional[WorkRecord]
    result: StoreOperationResult = StoreOperationResult.applied()


class PersistentTaskStore(TaskWorkStore):
    """File Task store with process-local Work state reconstructed from QueueFS.

    Only Task records are durable. QueueFS startup recovery can reconstruct
    unacknowledged Work, but terminal Work history, timings, and errors may be
    incomplete after restart.
    """

    ROOT_PREFIX = "/local"
    SYSTEM_DIRNAME = "_system"
    TASKS_DIRNAME = "tasks"

    def __init__(self, agfs: Any) -> None:
        self._agfs = agfs if isinstance(agfs, AsyncAGFSClient) else AsyncAGFSClient(agfs)
        self._ensured_task_dirs: set[tuple[str, str]] = set()
        self._lock = threading.Lock()
        self._task_cache: Dict[tuple[str, str, str], TaskRecord] = {}
        self._work_sets: Dict[tuple[str, str, str], _WorkSet] = {}
        self._business_locks = KeyedAsyncLockPool()
        self._record_locks = KeyedAsyncLockPool()
        self._cleanup_lock = KeyedAsyncLockPool()
        self._cleanup_scan: Optional[AsyncIterator[Optional[Dict[str, Any]]]] = None

    @property
    def work_recovery_mode(self) -> WorkRecoveryMode:
        return WorkRecoveryMode.QUEUE_SNAPSHOT

    async def create(self, task: TaskRecord, *, reject_if_active: bool = False) -> TaskRecord:
        if not task.account_id or not task.user_id:
            raise ValueError("PersistentTaskStore requires account_id and user_id")
        business_key = (task.account_id, task.user_id, task.task_type, task.resource_id)
        async with self._business_locks.acquire(business_key):
            if reject_if_active:
                candidates = await self.list(task.account_id, user_id=task.user_id)
                if any(
                    item.task_type == task.task_type
                    and item.resource_id == task.resource_id
                    and item.status in ACTIVE_TASK_STATUSES
                    for item in candidates
                ):
                    raise TaskAlreadyActive(task.task_type, task.resource_id)
            owner = (task.account_id, task.user_id, task.task_id)
            async with self._record_locks.acquire(owner):
                existing = await self._load_task(task.task_id, task.account_id, task.user_id)
                if existing is not None:
                    return self._copy_task(existing, self._work_sets.get(owner))
                stored = deepcopy(task)
                self._append_events(stored, None)
                await self._write_task(stored)
                with self._lock:
                    self._task_cache[owner] = stored
                    self._work_sets[owner] = _WorkSet({})
                return deepcopy(stored)

    async def get_task(
        self,
        task_id: str,
        *,
        account_id: Optional[str] = None,
        user_id: Optional[str] = None,
    ) -> Optional[TaskRecord]:
        task = await self._load_task(task_id, account_id, user_id)
        if task is None:
            return None
        with self._lock:
            owner = self._find_cached_owner(task_id, account_id, user_id)
            if owner is None:
                return None
            return self._copy_task(self._task_cache[owner], self._work_sets.get(owner))

    async def start_task(
        self,
        task_id: str,
        *,
        stage: Optional[str],
        account_id: Optional[str],
        user_id: Optional[str],
    ) -> StoreOperationResult:
        def mutate(task: TaskRecord, _work_set: _WorkSet) -> StoreOperationResult:
            if task.status not in (TaskStatus.PENDING, TaskStatus.RUNNING):
                return _inactive_task_result(task, "Task cannot be started")
            task.status = TaskStatus.RUNNING
            if stage is not None:
                task.stage = stage
            return StoreOperationResult.applied()

        return await self._mutate_task(task_id, account_id, user_id, mutate)

    async def update_stage(
        self,
        task_id: str,
        stage: str,
        *,
        meta: Optional[Dict[str, Any]],
        account_id: Optional[str],
        user_id: Optional[str],
    ) -> StoreOperationResult:
        def mutate(task: TaskRecord, _work_set: _WorkSet) -> StoreOperationResult:
            if task.status not in ACTIVE_TASK_STATUSES:
                return _inactive_task_result(task, "Task stage cannot be updated")
            task.stage = stage
            if meta:
                _deep_merge(task.meta, deepcopy(meta))
            return StoreOperationResult.applied()

        return await self._mutate_task(task_id, account_id, user_id, mutate)

    async def update_auth(
        self,
        task_id: str,
        values: Dict[str, Any],
        *,
        account_id: str,
        user_id: str,
    ) -> StoreOperationResult:
        def mutate(task: TaskRecord, _work_set: _WorkSet) -> StoreOperationResult:
            if task.status not in ACTIVE_TASK_STATUSES:
                return _inactive_task_result(task, "Task auth cannot be updated")
            _deep_merge(task.auth, deepcopy(values))
            return StoreOperationResult.applied()

        return await self._mutate_task(task_id, account_id, user_id, mutate)

    async def merge_meta(
        self,
        task_id: str,
        values: Dict[str, Any],
        *,
        account_id: str,
        user_id: str,
    ) -> StoreOperationResult:
        def mutate(task: TaskRecord, _work_set: _WorkSet) -> StoreOperationResult:
            if task.status not in (TaskStatus.PENDING, TaskStatus.RUNNING):
                return _inactive_task_result(task, "Task meta cannot be updated")
            _deep_merge(task.meta, deepcopy(values))
            return StoreOperationResult.applied()

        return await self._mutate_task(task_id, account_id, user_id, mutate)

    async def record_event(
        self,
        task_id: str,
        kind: str,
        *,
        operation: Optional[str],
        account_id: str,
        user_id: str,
    ) -> StoreOperationResult:
        def mutate(task: TaskRecord, _work_set: _WorkSet) -> StoreOperationResult:
            if task.status not in ACTIVE_TASK_STATUSES:
                return StoreOperationResult.noop(
                    StoreOperationCode.ALREADY_TERMINAL,
                    f"Task is already {task.status.value}",
                )
            task.execution_events = append_task_event(
                task.execution_events,
                kind=kind,
                status=task.status.value,
                stage=sanitize_error(task.stage) if task.stage else None,
                operation=operation,
            )
            return StoreOperationResult.applied()

        return await self._mutate_task(task_id, account_id, user_id, mutate)

    async def record_outcome(
        self,
        task_id: str,
        *,
        result: Optional[Dict[str, Any]],
        error: Optional[str],
        resource_id: Optional[str],
        cancelled: bool,
        account_id: Optional[str],
        user_id: Optional[str],
    ) -> StoreOperationResult:
        def mutate(task: TaskRecord, work_set: _WorkSet) -> StoreOperationResult:
            if task.status not in ACTIVE_TASK_STATUSES:
                return StoreOperationResult.noop(
                    StoreOperationCode.ALREADY_TERMINAL,
                    f"Task is already {task.status.value}",
                )
            if result is not None:
                task.result = deepcopy(result)
            if resource_id is not None:
                task.resource_id = resource_id
            if error and task.error is None:
                task.error = sanitize_error(error)
            if cancelled:
                task.status = TaskStatus.CANCELLING
            enqueue_markers = task.auth.get("enqueue_complete_work_ids")
            task.auth = (
                {"enqueue_complete_work_ids": deepcopy(enqueue_markers)} if enqueue_markers else {}
            )
            self._try_finalize(task, work_set)
            return StoreOperationResult.applied()

        return await self._mutate_task(task_id, account_id, user_id, mutate)

    async def request_cancellation(
        self,
        task_id: str,
        *,
        cancellable_types: Collection[str],
        allow_terminal: bool,
        account_id: str,
        user_id: str,
    ) -> StoreOperationResult:
        def mutate(task: TaskRecord, work_set: _WorkSet) -> StoreOperationResult:
            if task.status in (TaskStatus.CANCELLING, TaskStatus.CANCELLED):
                return StoreOperationResult.noop(
                    StoreOperationCode.ALREADY_CANCELLED,
                    f"Task is already {task.status.value}",
                )
            if task.task_type not in cancellable_types:
                return StoreOperationResult.rejected(
                    StoreOperationCode.NOT_CANCELLABLE,
                    f"Task type '{task.task_type}' does not support cancellation",
                )
            if task.status in TERMINAL_TASK_STATUSES and not allow_terminal:
                return StoreOperationResult.rejected(
                    StoreOperationCode.ALREADY_TERMINAL,
                    f"Task is already {task.status.value}",
                )
            task.status = TaskStatus.CANCELLING
            self._try_finalize(task, work_set)
            return StoreOperationResult.applied()

        return await self._mutate_task(task_id, account_id, user_id, mutate)

    async def mark_enqueue_complete(
        self,
        task_id: str,
        work_id: str,
        *,
        account_id: str,
        user_id: str,
    ) -> StoreOperationResult:
        def mutate(task: TaskRecord, _work_set: _WorkSet) -> StoreOperationResult:
            if task.status not in ACTIVE_TASK_STATUSES:
                return StoreOperationResult.noop(
                    StoreOperationCode.ALREADY_TERMINAL,
                    f"Task is already {task.status.value}",
                )
            completed = set(task.auth.get("enqueue_complete_work_ids") or ())
            if work_id in completed:
                return StoreOperationResult.noop(StoreOperationCode.DUPLICATE)
            completed.add(work_id)
            task.auth["enqueue_complete_work_ids"] = sorted(completed)
            return StoreOperationResult.applied()

        return await self._mutate_task(task_id, account_id, user_id, mutate)

    async def is_enqueue_complete(
        self,
        task_id: str,
        work_id: str,
        *,
        account_id: str,
        user_id: str,
    ) -> bool:
        task = await self.get_task(task_id, account_id=account_id, user_id=user_id)
        return bool(task and work_id in set(task.auth.get("enqueue_complete_work_ids") or ()))

    async def add_work(
        self, work: WorkRecord, *, account_id: str, user_id: str
    ) -> StoreOperationResult:
        def mutate(task: TaskRecord, existing: Optional[WorkRecord]) -> _WorkMutationResult:
            if task.is_terminal():
                return _WorkMutationResult(
                    False,
                    existing,
                    StoreOperationResult.rejected(
                        StoreOperationCode.ALREADY_TERMINAL,
                        f"Task {work.task_id} is {task.status.value}; rejected work",
                    ),
                )
            if task.status == TaskStatus.CANCELLING:
                return _WorkMutationResult(
                    False,
                    existing,
                    StoreOperationResult.rejected(
                        StoreOperationCode.INVALID_STATE,
                        f"Task {work.task_id} is cancelling; rejected work",
                    ),
                )
            if existing is not None:
                return _WorkMutationResult(
                    False,
                    existing,
                    StoreOperationResult.noop(StoreOperationCode.DUPLICATE),
                )
            return _WorkMutationResult(True, deepcopy(work))

        return await self._mutate_work(
            work.task_id,
            work.work_id,
            account_id,
            user_id,
            mutate,
        )

    async def reconcile_delivery(
        self, work: WorkRecord, *, account_id: str, user_id: str
    ) -> StoreOperationResult:
        """Ensure a delivered QueueFS message has local Work state."""

        def mutate(task: TaskRecord, existing: Optional[WorkRecord]) -> _WorkMutationResult:
            if task.is_terminal():
                return _WorkMutationResult(
                    False,
                    existing,
                    StoreOperationResult.noop(
                        StoreOperationCode.ALREADY_TERMINAL,
                        f"Task is already {task.status.value}",
                    ),
                )
            if existing is None:
                return _WorkMutationResult(True, deepcopy(work))
            if existing.is_open() and existing.queue_name != work.queue_name:
                existing.queue_name = work.queue_name
                return _WorkMutationResult(True, existing)
            return _WorkMutationResult(False, existing, StoreOperationResult.noop())

        return await self._mutate_work(
            work.task_id,
            work.work_id,
            account_id,
            user_id,
            mutate,
        )

    async def discard_work(
        self, task_id: str, work_id: str, *, account_id: str, user_id: str
    ) -> StoreOperationResult:
        def mutate(_task: TaskRecord, existing: Optional[WorkRecord]) -> _WorkMutationResult:
            if existing is None:
                return _WorkMutationResult(
                    False,
                    None,
                    StoreOperationResult.noop(StoreOperationCode.NOT_FOUND),
                )
            return _WorkMutationResult(True, None)

        return await self._mutate_work(
            task_id,
            work_id,
            account_id,
            user_id,
            mutate,
            try_finalize=True,
        )

    async def get_work(
        self, task_id: str, work_id: str, *, account_id: str, user_id: str
    ) -> Optional[WorkRecord]:
        task = await self._load_task(task_id, account_id, user_id)
        if task is None:
            return None
        with self._lock:
            work_set = self._work_sets.get((account_id, user_id, task_id))
            work = work_set.works.get(work_id) if work_set else None
            return deepcopy(work) if work is not None else None

    async def get_works(
        self,
        task_id: str,
        work_ids: Collection[str],
        *,
        account_id: Optional[str],
        user_id: Optional[str],
    ) -> Dict[str, WorkRecord]:
        task = await self._load_task(task_id, account_id, user_id)
        if task is None or not task.account_id or not task.user_id:
            return {}
        wanted = set(work_ids)
        with self._lock:
            work_set = self._work_sets.get((task.account_id, task.user_id, task_id))
            return {
                work_id: deepcopy(work)
                for work_id, work in (work_set.works.items() if work_set else ())
                if work_id in wanted
            }

    async def start_work(
        self, task_id: str, work_id: str, *, account_id: str, user_id: str
    ) -> StoreOperationResult:
        def mutate(_task: TaskRecord, work: Optional[WorkRecord]) -> _WorkMutationResult:
            if work is None or not work.is_open():
                return _WorkMutationResult(
                    False,
                    work,
                    StoreOperationResult.rejected(
                        StoreOperationCode.WORK_NOT_OPEN,
                        f"Work {work_id} is unavailable",
                    ),
                )
            now = time.time()
            work.state = WorkState.RUNNING
            work.started_at = work.started_at or now
            work.finished_at = None
            work.updated_at = now
            return _WorkMutationResult(True, work)

        return await self._mutate_work(task_id, work_id, account_id, user_id, mutate)

    async def finish_work(
        self,
        task_id: str,
        work_id: str,
        state: WorkState,
        *,
        error: Optional[str],
        errors: Optional[Collection[str]],
        context_count_delta: int,
        account_id: str,
        user_id: str,
    ) -> StoreOperationResult:
        if state not in (WorkState.DONE, WorkState.FAILED):
            raise ValueError(f"Non-terminal Work state: {state}")

        def mutate(_task: TaskRecord, work: Optional[WorkRecord]) -> _WorkMutationResult:
            if work is None or not work.is_open():
                return _WorkMutationResult(
                    False,
                    work,
                    StoreOperationResult.noop(
                        StoreOperationCode.WORK_NOT_OPEN,
                        f"Work {work_id} is unavailable",
                    ),
                )
            now = time.time()
            work.state = state
            work.finished_at = now
            work.updated_at = now
            if error:
                work.error = sanitize_error(error)
            if state == WorkState.FAILED:
                details = [sanitize_error(str(value)) for value in errors or () if str(value)]
                work.errors = details or ([work.error] if work.error else [])
            if context_count_delta > 0:
                work.context_count += context_count_delta
            return _WorkMutationResult(True, work)

        return await self._mutate_work(
            task_id,
            work_id,
            account_id,
            user_id,
            mutate,
            try_finalize=True,
        )

    async def requeue_work(
        self,
        task_id: str,
        work_id: str,
        *,
        delta: int,
        account_id: str,
        user_id: str,
    ) -> StoreOperationResult:
        def mutate(_task: TaskRecord, work: Optional[WorkRecord]) -> _WorkMutationResult:
            if work is None or not work.is_open():
                return _WorkMutationResult(
                    False,
                    work,
                    StoreOperationResult.noop(
                        StoreOperationCode.WORK_NOT_OPEN,
                        f"Work {work_id} is unavailable",
                    ),
                )
            work.state = WorkState.PENDING
            work.requeue_count += max(delta, 0)
            work.started_at = None
            work.finished_at = None
            work.updated_at = time.time()
            return _WorkMutationResult(True, work)

        return await self._mutate_work(task_id, work_id, account_id, user_id, mutate)

    async def record_work_error(
        self,
        task_id: str,
        work_id: str,
        error: str,
        *,
        account_id: str,
        user_id: str,
    ) -> StoreOperationResult:
        def mutate(_task: TaskRecord, work: Optional[WorkRecord]) -> _WorkMutationResult:
            if work is None or work.error:
                return _WorkMutationResult(
                    False,
                    work,
                    StoreOperationResult.noop(
                        StoreOperationCode.WORK_NOT_OPEN if work is None else StoreOperationCode.OK
                    ),
                )
            work.error = sanitize_error(error)
            work.updated_at = time.time()
            return _WorkMutationResult(True, work)

        return await self._mutate_work(task_id, work_id, account_id, user_id, mutate)

    async def settle_work(
        self, task_id: str, work_id: str, *, account_id: str, user_id: str
    ) -> StoreOperationResult:
        def mutate(_task: TaskRecord, work: Optional[WorkRecord]) -> _WorkMutationResult:
            if work is None:
                return _WorkMutationResult(
                    False,
                    None,
                    StoreOperationResult.noop(StoreOperationCode.NOT_FOUND),
                )
            changed = False
            if work.is_open():
                work.state = WorkState.FAILED if work.error else WorkState.DONE
                work.finished_at = time.time()
                work.updated_at = work.finished_at
                changed = True
            return _WorkMutationResult(
                changed,
                work,
                StoreOperationResult.applied() if changed else StoreOperationResult.noop(),
            )

        return await self._mutate_work(
            task_id,
            work_id,
            account_id,
            user_id,
            mutate,
            try_finalize=True,
            finalize_when_unchanged=True,
        )

    async def has_open_work(
        self,
        task_id: str,
        *,
        exclude_work_id: Optional[str] = None,
        account_id: Optional[str] = None,
        user_id: Optional[str] = None,
    ) -> bool:
        task = await self._load_task(task_id, account_id, user_id)
        if task is None or not task.account_id or not task.user_id:
            return False
        with self._lock:
            work_set = self._work_sets.get((task.account_id, task.user_id, task_id))
            if work_set is None or work_set.open_count == 0:
                return False
            if exclude_work_id is None:
                return True
            excluded = work_set.works.get(exclude_work_id)
            return work_set.open_count > int(excluded is not None and excluded.is_open())

    async def summarize_works(
        self,
        task_id: str,
        *,
        work_ids: Optional[Collection[str]] = None,
        account_id: Optional[str] = None,
        user_id: Optional[str] = None,
    ) -> WorkSummary:
        works = await self._selected_works(task_id, work_ids, account_id, user_id)
        return summarize_works(works)

    async def filter_cancellation_requested(
        self,
        task_ids: Collection[str],
        *,
        account_id: str,
        user_id: str,
    ) -> set[str]:
        requested = set()
        for task_id in set(task_ids):
            status = await self.get_task_status(task_id, account_id=account_id, user_id=user_id)
            if status in (
                TaskStatus.CANCELLING,
                TaskStatus.CANCELLED,
            ):
                requested.add(task_id)
        return requested

    async def get_task_status(
        self,
        task_id: str,
        *,
        account_id: str,
        user_id: str,
    ) -> Optional[TaskStatus]:
        task = await self._load_task(task_id, account_id, user_id)
        if task is None:
            return None
        with self._lock:
            live = self._task_cache.get((account_id, user_id, task_id))
            return live.status if live is not None else None

    async def list(
        self,
        account_id: Optional[str] = None,
        *,
        user_id: Optional[str] = None,
        task_type: Optional[str] = None,
        status: Optional[str] = None,
        resource_id: Optional[str] = None,
        limit: Optional[int] = None,
        before: tuple[float, str] | None = None,
        include_internal: bool = True,
        q: Optional[str] = None,
    ) -> List[TaskRecord]:
        """List matching Tasks.

        Owner-scoped reads include durable records. Without an account, the
        file backend can only return records already loaded into its cache.
        """
        from openviking.service.task_pagination import matches

        if limit is not None and limit <= 0:
            return []
        if account_id and user_id and limit is not None:
            return await self._list_owner_page(
                account_id,
                user_id=user_id,
                limit=limit,
                before=before,
                task_type=task_type,
                status=status,
                resource_id=resource_id,
                include_internal=include_internal,
                q=q,
            )
        if account_id:
            users = [user_id] if user_id else await self._owner_users(account_id)
            for owner_user in users:
                for payload in await self._list_owner_payloads(account_id, owner_user):
                    try:
                        self._put_if_absent(
                            _task_from_payload(payload, account_id=account_id, user_id=owner_user)
                        )
                    except (ValueError, TypeError, KeyError):
                        continue
        with self._lock:
            tasks = [
                self._copy_task(task, self._work_sets.get(owner))
                for owner, task in self._task_cache.items()
                for account, user, _ in (owner,)
                if (account_id is None or account == account_id)
                and (user_id is None or user == user_id)
            ]
        tasks = [
            task
            for task in tasks
            if (before is None or (task.created_at, task.task_id) < before)
            and matches(
                task.to_dict(),
                task_type=task_type,
                status=status,
                resource_id=resource_id,
                include_internal=include_internal,
                q=q,
            )
        ]
        tasks.sort(key=lambda task: (task.created_at, task.task_id), reverse=True)
        return tasks if limit is None else tasks[:limit]

    async def _list_owner_page(
        self,
        account_id: str,
        *,
        user_id: str,
        limit: int,
        before: tuple[float, str] | None = None,
        task_type: Optional[str],
        status: Optional[str],
        resource_id: Optional[str],
        include_internal: bool,
        q: Optional[str],
    ) -> List[TaskRecord]:
        from openviking.service.task_pagination import matches

        try:
            entries = await self._ls(self._task_dir(account_id, user_id))
        except (AGFSNotFoundError, FileNotFoundError):
            return []
        heap: list[tuple[float, str, TaskRecord]] = []
        for item in entries:
            path = (
                item.get("path") or f"{self._task_dir(account_id, user_id)}/{item.get('name', '')}"
            )
            if not path.endswith(".json"):
                continue
            try:
                payload = json.loads(_decode_bytes(await self._read(path)))
                record = _task_from_payload(payload, account_id=account_id, user_id=user_id)
            except (AGFSNotFoundError, FileNotFoundError, ValueError, TypeError, KeyError):
                continue
            with self._lock:
                owner = (account_id, user_id, record.task_id)
                cached = self._task_cache.get(owner)
                if cached is not None:
                    record = self._copy_task(cached, self._work_sets.get(owner))
            key = (record.created_at, record.task_id)
            if before is not None and key >= before:
                continue
            if not matches(
                record.to_dict(),
                task_type=task_type,
                status=status,
                resource_id=resource_id,
                include_internal=include_internal,
                q=q,
            ):
                continue
            candidate = (*key, record)
            if len(heap) < limit:
                heapq.heappush(heap, candidate)
            elif key > heap[0][:2]:
                heapq.heapreplace(heap, candidate)
        return [item[2] for item in sorted(heap, reverse=True)]

    async def delete_tasks(
        self,
        task_id: Optional[str] = None,
        *,
        account_id: str,
        user_id: Optional[str] = None,
        allow_active_types: Collection[str] = (),
    ) -> DeleteTasksResult:
        """Delete one Task or all Tasks in an owner scope."""
        if task_id is not None:
            if not user_id:
                raise ValueError("Deleting one Task requires user_id")
            owner = (account_id, user_id, task_id)
            async with self._record_locks.acquire(owner):
                task = await self._load_task(task_id, account_id, user_id)
                if task is not None:
                    blocker = self._deletion_blocker(
                        task, self._work_sets.get(owner), allow_active_types
                    )
                    if blocker is not None:
                        return self._delete_tasks_rejected((blocker,))
                deleted = await self._delete_task_unlocked(owner, task is not None)
                return DeleteTasksResult(
                    (DeletedTask(task_id, account_id, user_id),) if deleted else (),
                    StoreOperationResult.applied()
                    if deleted
                    else StoreOperationResult.noop(StoreOperationCode.NOT_FOUND),
                )

        return await self._delete_owner_tasks(
            account_id,
            user_id=user_id,
            allow_active_types=allow_active_types,
        )

    async def _delete_owner_tasks(
        self,
        account_id: str,
        *,
        user_id: Optional[str],
        allow_active_types: Collection[str],
    ) -> DeleteTasksResult:
        tasks = await self.list(account_id, user_id=user_id, limit=None)
        blockers = tuple(
            blocker
            for task in tasks
            if task.user_id
            for blocker in (
                self._deletion_blocker(
                    task,
                    self._work_sets.get((task.account_id, task.user_id, task.task_id)),
                    allow_active_types,
                ),
            )
            if blocker is not None
        )
        if blockers:
            return self._delete_tasks_rejected(blockers)
        deleted: list[DeletedTask] = []
        for task in tasks:
            if task.user_id:
                result = await self.delete_tasks(
                    task.task_id,
                    account_id=account_id,
                    user_id=task.user_id,
                    allow_active_types=allow_active_types,
                )
                if not result.accepted:
                    return result
                deleted.extend(result.deleted)
        self._ensured_task_dirs = {
            owner
            for owner in self._ensured_task_dirs
            if owner[0] != account_id or (user_id is not None and owner[1] != user_id)
        }
        return DeleteTasksResult(
            tuple(sorted(deleted, key=lambda item: (item.account_id, item.user_id, item.task_id)))
        )

    @staticmethod
    def _deletion_blocker(
        task: TaskRecord,
        work_set: Optional[_WorkSet],
        allow_active_types: Collection[str],
    ) -> Optional[TaskDeletionBlocker]:
        allowed = set(allow_active_types)
        if task.status in ACTIVE_TASK_STATUSES and task.task_type not in allowed:
            return TaskDeletionBlocker(
                task.task_id,
                task.account_id or "",
                task.user_id or "",
                task.task_type,
                StoreOperationCode.ACTIVE_TASK,
                f"Cannot delete active task record: {task.task_id}({task.task_type})",
            )
        if task.task_type in allowed:
            return None
        if work_set is not None and work_set.open_count:
            return TaskDeletionBlocker(
                task.task_id,
                task.account_id or "",
                task.user_id or "",
                task.task_type,
                StoreOperationCode.OPEN_WORK,
                f"Cannot delete active task record: {task.task_id}",
            )
        return None

    @staticmethod
    def _delete_tasks_rejected(blockers: Collection[TaskDeletionBlocker]) -> DeleteTasksResult:
        ordered = tuple(blockers)
        active = [item for item in ordered if item.code is StoreOperationCode.ACTIVE_TASK]
        if active:
            message = "Cannot delete active task records: " + ", ".join(
                f"{item.task_id}({item.task_type})" for item in active
            )
            code = StoreOperationCode.ACTIVE_TASK
        elif len(ordered) == 1:
            message = ordered[0].message
            code = ordered[0].code
        else:
            message = "Cannot delete active task records: " + ", ".join(
                item.task_id for item in ordered
            )
            code = StoreOperationCode.OPEN_WORK
        return DeleteTasksResult(
            (),
            StoreOperationResult.rejected(code, message),
            ordered,
        )

    async def _delete_task_unlocked(
        self,
        owner: tuple[str, str, str],
        task_exists: bool,
    ) -> bool:
        account_id, user_id, task_id = owner
        try:
            await self._rm(self._task_path(account_id, user_id, task_id))
        except (AGFSNotFoundError, FileNotFoundError):
            if not task_exists:
                return False
        with self._lock:
            self._task_cache.pop(owner, None)
            self._work_sets.pop(owner, None)
        return task_exists

    async def delete_expired(
        self, policy: TaskRetentionPolicy, *, now: float, limit: int
    ) -> DeleteExpiredResult:
        if limit <= 0:
            return DeleteExpiredResult()
        deleted: list[DeletedTask] = []
        scanned = 0
        scan_complete = False
        async with self._cleanup_lock.acquire("scan"):
            if self._cleanup_scan is None:
                self._cleanup_scan = self._iter_task_payloads()
            for _ in range(limit):
                try:
                    payload = await anext(self._cleanup_scan)
                except StopAsyncIteration:
                    self._cleanup_scan = None
                    scan_complete = True
                    break
                scanned += 1
                if payload is None:
                    continue
                try:
                    expired = policy.is_expired(
                        str(payload.get("status", "")),
                        float(payload.get("updated_at", 0.0)),
                        now,
                    )
                except (TypeError, ValueError):
                    expired = False
                if not expired:
                    continue
                owner = (
                    str(payload.get("account_id") or ""),
                    str(payload.get("user_id") or ""),
                    str(payload.get("task_id") or ""),
                )
                if not all(owner):
                    continue
                account_id, user_id, task_id = owner
                async with self._record_locks.acquire(owner):
                    task = await self._load_task(task_id, account_id, user_id)
                    work_set = self._work_sets.get(owner)
                    if task is None or (work_set is not None and work_set.open_count):
                        continue
                    if not policy.is_expired(task.status.value, task.updated_at, now):
                        continue
                    try:
                        await self._delete_task_unlocked(owner, task_exists=True)
                    except BaseException:
                        self._cleanup_scan = None
                        raise
                    deleted.append(DeletedTask(task_id, account_id, user_id))
        return DeleteExpiredResult(tuple(deleted), scanned, scan_complete)

    def snapshot_task_stats(self) -> TaskStatsSnapshot:
        grouped: Dict[str, Dict[str, int]] = defaultdict(lambda: defaultdict(int))
        with self._lock:
            for task in self._task_cache.values():
                grouped[task.task_type][task.status.value] += 1
        return {task_type: dict(statuses) for task_type, statuses in grouped.items()}

    async def _selected_works(
        self,
        task_id: str,
        work_ids: Optional[Collection[str]],
        account_id: Optional[str],
        user_id: Optional[str],
    ) -> List[WorkRecord]:
        task = await self._load_task(task_id, account_id, user_id)
        if task is None or not task.account_id or not task.user_id:
            return []
        selected = set(work_ids) if work_ids is not None else None
        with self._lock:
            work_set = self._work_sets.get((task.account_id, task.user_id, task_id))
            return [
                deepcopy(work)
                for work in (work_set.works.values() if work_set else ())
                if selected is None or work.work_id in selected
            ]

    async def _mutate_task(
        self,
        task_id: str,
        account_id: Optional[str],
        user_id: Optional[str],
        mutate: Callable[[TaskRecord, _WorkSet], StoreOperationResult],
    ) -> StoreOperationResult:
        task = await self._load_task(task_id, account_id, user_id)
        if task is None or not task.account_id or not task.user_id:
            return StoreOperationResult.rejected(StoreOperationCode.NOT_FOUND)
        owner = (task.account_id, task.user_id, task_id)
        async with self._record_locks.acquire(owner):
            await self._load_task(task_id, owner[0], owner[1])
            with self._lock:
                live_task = self._task_cache.get(owner)
                if live_task is None:
                    return StoreOperationResult.rejected(StoreOperationCode.NOT_FOUND)
                work_set = self._work_sets.setdefault(owner, _WorkSet({}))
                candidate = deepcopy(live_task)
                previous = deepcopy(live_task)
                mutation = mutate(candidate, work_set)
                if inspect.isawaitable(mutation):
                    raise TypeError("File store mutations must be synchronous")
                if not isinstance(mutation, StoreOperationResult):
                    raise TypeError("Task mutations must return StoreOperationResult")
                if not mutation.is_applied:
                    return mutation
                candidate.updated_at = TaskRecord.next_updated_at(candidate.updated_at)
                self._append_events(candidate, previous)
                snapshot = deepcopy(candidate)
            await self._write_task_and_publish(snapshot, owner, candidate)
            return mutation

    async def _mutate_work(
        self,
        task_id: str,
        work_id: str,
        account_id: Optional[str],
        user_id: Optional[str],
        mutate: Callable[[TaskRecord, Optional[WorkRecord]], _WorkMutationResult],
        *,
        try_finalize: bool = False,
        finalize_when_unchanged: bool = False,
    ) -> StoreOperationResult:
        task = await self._load_task(task_id, account_id, user_id)
        if task is None or not task.account_id or not task.user_id:
            return StoreOperationResult.rejected(StoreOperationCode.NOT_FOUND)
        owner = (task.account_id, task.user_id, task_id)
        async with self._record_locks.acquire(owner):
            await self._load_task(task_id, owner[0], owner[1])
            with self._lock:
                live_task = self._task_cache.get(owner)
                if live_task is None:
                    return StoreOperationResult.rejected(StoreOperationCode.NOT_FOUND)
                work_set = self._work_sets.setdefault(owner, _WorkSet({}))
                existing = work_set.works.get(work_id)
                mutation = mutate(live_task, deepcopy(existing) if existing is not None else None)
                if inspect.isawaitable(mutation):
                    raise TypeError("File store mutations must be synchronous")
                if not isinstance(mutation, _WorkMutationResult):
                    raise TypeError("Work mutations must return _WorkMutationResult")
                old_open = existing is not None and existing.is_open()
                new_open = mutation.work is not None and mutation.work.is_open()
                open_count = work_set.open_count + int(new_open) - int(old_open)

                candidate_task: Optional[TaskRecord] = None
                candidate_work_set: Optional[_WorkSet] = None
                finalized = False
                if (
                    try_finalize
                    and open_count == 0
                    and (mutation.changed or finalize_when_unchanged)
                ):
                    candidate_task = deepcopy(live_task)
                    previous = deepcopy(live_task)
                    works = dict(work_set.works)
                    if mutation.changed:
                        if mutation.work is None:
                            works.pop(work_id, None)
                        else:
                            works[work_id] = mutation.work
                    candidate_work_set = _WorkSet(works, open_count)
                    finalized = self._try_finalize(candidate_task, candidate_work_set)

                if finalized:
                    assert candidate_task is not None
                    assert candidate_work_set is not None
                    candidate_task.updated_at = TaskRecord.next_updated_at(
                        candidate_task.updated_at
                    )
                    self._append_events(candidate_task, previous)
                    snapshot = deepcopy(candidate_task)
                else:
                    if mutation.changed:
                        if mutation.work is None:
                            work_set.works.pop(work_id, None)
                        else:
                            work_set.works[work_id] = mutation.work
                        work_set.open_count = open_count
                    return mutation.result
            assert candidate_task is not None
            assert candidate_work_set is not None
            await self._write_task_and_publish(snapshot, owner, candidate_task, candidate_work_set)
            return StoreOperationResult.applied()

    async def _write_task_and_publish(
        self,
        snapshot: TaskRecord,
        owner: tuple[str, str, str],
        candidate_task: TaskRecord,
        candidate_work_set: Optional[_WorkSet] = None,
    ) -> None:
        write_task = asyncio.create_task(self._write_task(snapshot))
        cancellation: asyncio.CancelledError | None = None
        try:
            while not write_task.done():
                try:
                    await asyncio.shield(write_task)
                except asyncio.CancelledError as exc:
                    if write_task.cancelled():
                        raise
                    cancellation = exc
            write_task.result()
            with self._lock:
                self._task_cache[owner] = candidate_task
                if candidate_work_set is not None:
                    self._work_sets[owner] = candidate_work_set
            if cancellation is not None:
                raise cancellation
        finally:
            del write_task, cancellation

    def _try_finalize(self, task: TaskRecord, work_set: _WorkSet) -> bool:
        if task.status not in ACTIVE_TASK_STATUSES or work_set.open_count:
            return False
        if task.status == TaskStatus.CANCELLING:
            task.status = TaskStatus.CANCELLED
        else:
            work_error = first_work_error(work_set.works.values())
            if work_error and task.error is None:
                task.error = sanitize_error(work_error)
            if task.error is not None:
                task.status = TaskStatus.FAILED
            elif task.result is not None:
                task.status = TaskStatus.COMPLETED
            else:
                task.error = "Task operation interrupted before its result was persisted"
                task.status = TaskStatus.FAILED
        if isinstance(task.result, dict):
            # QueueFS reconstructs only unacknowledged Work after restart. For this
            # file backend, terminal Work history and its errors may therefore be
            # absent from the final projection until Work itself becomes durable.
            summary = summarize_works(work_set.works.values())
            projected = {
                queue_name: _queue_result(summary.queues.get(queue_name))
                for queue_name in ("Semantic", "Embedding")
            }
            has_projected_work = any(
                work.queue_name in projected for work in work_set.works.values()
            )
            if has_projected_work:
                task.result["queue_status"] = projected
            for queue_name, field_name in (
                ("Semantic", "semantic_status"),
                ("Embedding", "vector_status"),
            ):
                if task.result.get(field_name) == "queued":
                    task.result[field_name] = (
                        "failed"
                        if projected.get(queue_name, {}).get("error_count", 0)
                        else "complete"
                    )
        task.stage = task.status.value
        task.auth = {}
        task.processing_seconds = processing_seconds(work_set.works.values())
        return True

    def _copy_task(self, task: TaskRecord, work_set: Optional[_WorkSet]) -> TaskRecord:
        copied = deepcopy(task)
        if copied.status in ACTIVE_TASK_STATUSES and work_set is not None:
            copied.processing_seconds = processing_seconds(work_set.works.values())
        return copied

    async def _load_task(
        self, task_id: str, account_id: Optional[str], user_id: Optional[str]
    ) -> Optional[TaskRecord]:
        with self._lock:
            owner = self._find_cached_owner(task_id, account_id, user_id)
            if owner is not None:
                return self._task_cache[owner]
        if not account_id or not user_id:
            return None
        try:
            payload = json.loads(
                _decode_bytes(await self._read(self._task_path(account_id, user_id, task_id)))
            )
        except (AGFSNotFoundError, FileNotFoundError):
            return None
        task = _task_from_payload(payload, account_id=account_id, user_id=user_id)
        self._put_if_absent(task)
        with self._lock:
            return self._task_cache.get((account_id, user_id, task_id))

    def _find_cached_owner(
        self, task_id: str, account_id: Optional[str], user_id: Optional[str]
    ) -> Optional[tuple[str, str, str]]:
        if account_id and user_id:
            owner = (account_id, user_id, task_id)
            return owner if owner in self._task_cache else None
        return max(
            (
                owner
                for owner in self._task_cache
                for account, user, cached_id in (owner,)
                if cached_id == task_id
                and (account_id is None or account == account_id)
                and (user_id is None or user == user_id)
            ),
            key=lambda owner: self._task_cache[owner].updated_at,
            default=None,
        )

    def _put_if_absent(self, task: TaskRecord) -> None:
        if not task.account_id or not task.user_id:
            return
        owner = (task.account_id, task.user_id, task.task_id)
        with self._lock:
            self._task_cache.setdefault(owner, task)
            self._work_sets.setdefault(owner, _WorkSet({}))

    @staticmethod
    def _append_events(task: TaskRecord, previous: Optional[TaskRecord]) -> None:
        from datetime import datetime, timezone

        recorded_at = datetime.now(timezone.utc).isoformat()
        if previous is None:
            kinds = ["created"]
        else:
            kinds = []
            if task.error is not None and previous.error is None:
                kinds.append("error_recorded")
            if task.status == TaskStatus.CANCELLED and previous.status != TaskStatus.CANCELLING:
                task.execution_events = append_task_event(
                    task.execution_events,
                    kind="status_changed",
                    status=TaskStatus.CANCELLING.value,
                    stage=sanitize_error(previous.stage) if previous.stage else None,
                    recorded_at=recorded_at,
                )
            if task.status != previous.status:
                kinds.append("status_changed")
            if task.stage != previous.stage and task.status not in TERMINAL_TASK_STATUSES:
                kinds.append("stage_changed")

        stage = previous.stage if previous and task.status in TERMINAL_TASK_STATUSES else task.stage
        for kind in kinds:
            task.execution_events = append_task_event(
                task.execution_events,
                kind=kind,
                status=task.status.value,
                stage=sanitize_error(stage) if stage else None,
                error=sanitize_error(task.error)
                if kind == "error_recorded" and task.error
                else None,
                recorded_at=recorded_at,
            )

    async def _read(self, path: str) -> Any:
        return await run_to_completion(lambda: self._agfs.read(path))

    async def _ls(self, path: str) -> Any:
        return await run_to_completion(lambda: self._agfs.ls(path))

    async def _rm(self, path: str) -> None:
        await run_to_completion(lambda: self._agfs.rm(path, force=True, auto_pathlock=False))

    async def _write_task(self, task: TaskRecord) -> None:
        if not task.account_id or not task.user_id:
            raise ValueError("PersistentTaskStore requires account_id and user_id")
        await self._ensure_task_dir(task.account_id, task.user_id)
        payload = json.dumps(_task_to_payload(task), ensure_ascii=False).encode()
        path = self._task_path(task.account_id, task.user_id, task.task_id)
        try:
            await run_to_completion(lambda: self._agfs.write(path, payload, auto_pathlock=False))
        except (AGFSNotFoundError, FileNotFoundError):
            self._ensured_task_dirs.discard((task.account_id, task.user_id))
            await self._ensure_task_dir(task.account_id, task.user_id)
            await run_to_completion(lambda: self._agfs.write(path, payload, auto_pathlock=False))

    async def _ensure_task_dir(self, account_id: str, user_id: str) -> None:
        key = (account_id, user_id)
        if key in self._ensured_task_dirs:
            return
        for path in self._task_dir_chain(account_id, user_id):
            try:
                await run_to_completion(lambda path=path: self._agfs.mkdir(path))
            except AGFSAlreadyExistsError:
                pass
            except Exception as exc:
                if "already exists" not in str(exc).lower():
                    raise
        self._ensured_task_dirs.add(key)

    async def _owner_users(self, account_id: str) -> List[str]:
        try:
            return [
                str(item["name"])
                for item in await self._ls(self._task_root_dir(account_id))
                if item.get("name")
            ]
        except (AGFSNotFoundError, FileNotFoundError):
            return []

    async def _list_owner_payloads(self, account_id: str, user_id: str) -> List[Dict[str, Any]]:
        try:
            entries = await self._ls(self._task_dir(account_id, user_id))
        except (AGFSNotFoundError, FileNotFoundError):
            return []
        payloads = []
        for item in entries:
            path = (
                item.get("path") or f"{self._task_dir(account_id, user_id)}/{item.get('name', '')}"
            )
            if not path.endswith(".json"):
                continue
            try:
                value = json.loads(_decode_bytes(await self._read(path)))
                if isinstance(value, dict):
                    payloads.append(value)
            except (AGFSNotFoundError, FileNotFoundError, ValueError, TypeError):
                continue
        return payloads

    async def _iter_task_payloads(self) -> AsyncIterator[Optional[Dict[str, Any]]]:
        seen = set()
        with self._lock:
            cached = list(self._task_cache.items())
        for key, task in cached:
            seen.add(key)
            yield _task_to_payload(task)
        try:
            accounts = await self._ls(self.ROOT_PREFIX)
        except (AGFSNotFoundError, FileNotFoundError):
            return
        for account in accounts:
            raw_account_id = account.get("name")
            if not raw_account_id:
                continue
            account_id = str(raw_account_id)
            for user_id in await self._owner_users(account_id):
                try:
                    entries = await self._ls(self._task_dir(account_id, user_id))
                except (AGFSNotFoundError, FileNotFoundError):
                    continue
                for item in entries:
                    path = item.get("path") or (
                        f"{self._task_dir(account_id, user_id)}/{item.get('name', '')}"
                    )
                    if not path.endswith(".json"):
                        continue
                    task_id = path.rsplit("/", 1)[-1][:-5]
                    if (account_id, user_id, task_id) in seen:
                        continue
                    try:
                        payload = json.loads(_decode_bytes(await self._read(path)))
                    except (
                        AGFSNotFoundError,
                        FileNotFoundError,
                        ValueError,
                        TypeError,
                    ):
                        yield None
                        continue
                    yield payload if isinstance(payload, dict) else None

    def _task_dir_chain(self, account_id: str, user_id: str) -> tuple[str, ...]:
        return (
            f"{self.ROOT_PREFIX}/{account_id}",
            self._system_dir(account_id),
            self._task_root_dir(account_id),
            self._task_dir(account_id, user_id),
        )

    def _system_dir(self, account_id: str) -> str:
        if account_id == SYSTEM_TASK_ACCOUNT_ID:
            return f"{self.ROOT_PREFIX}/{account_id}"
        return f"{self.ROOT_PREFIX}/{account_id}/{self.SYSTEM_DIRNAME}"

    def _task_root_dir(self, account_id: str) -> str:
        return f"{self._system_dir(account_id)}/{self.TASKS_DIRNAME}"

    def _task_dir(self, account_id: str, user_id: str) -> str:
        return f"{self._task_root_dir(account_id)}/{user_id}"

    def _task_path(self, account_id: str, user_id: str, task_id: str) -> str:
        return f"{self._task_dir(account_id, user_id)}/{task_id}.json"


def _deep_merge(target: Dict[str, Any], values: Dict[str, Any]) -> None:
    for key, value in values.items():
        if isinstance(value, dict) and isinstance(target.get(key), dict):
            _deep_merge(target[key], value)
        else:
            target[key] = value


def _inactive_task_result(task: TaskRecord, message: str) -> StoreOperationResult:
    if task.status in TERMINAL_TASK_STATUSES:
        return StoreOperationResult.rejected(
            StoreOperationCode.ALREADY_TERMINAL,
            f"{message}: task is already {task.status.value}",
        )
    return StoreOperationResult.rejected(
        StoreOperationCode.INVALID_STATE,
        f"{message}: task is {task.status.value}",
    )


def _queue_result(summary: Optional[QueueWorkSummary]) -> Dict[str, Any]:
    summary = summary or QueueWorkSummary()
    return {
        "processed": summary.processed,
        "requeue_count": summary.requeue_count,
        "error_count": summary.error_count,
        "errors": [{"message": error} for error in summary.errors[:MAX_QUEUE_ERROR_DETAILS]],
    }


def _task_to_payload(task: TaskRecord) -> Dict[str, Any]:
    return {
        **deepcopy(task._extra_fields),
        "task_id": task.task_id,
        "task_type": task.task_type,
        "status": task.status.value,
        "created_at": task.created_at,
        "updated_at": task.updated_at,
        "processing_seconds": task.processing_seconds,
        "resource_id": task.resource_id,
        "account_id": task.account_id,
        "user_id": task.user_id,
        "meta": deepcopy(task.meta),
        "stage": task.stage,
        "result": deepcopy(task.result),
        "error": task.error,
        "execution_events": deepcopy(task.execution_events),
        "auth": deepcopy(task.auth),
    }


def _task_from_payload(
    payload: Dict[str, Any],
    *,
    account_id: Optional[str] = None,
    user_id: Optional[str] = None,
) -> TaskRecord:
    known = {
        item.name for item in fields(TaskRecord) if item.init and not item.name.startswith("_")
    }
    data = {key: deepcopy(value) for key, value in payload.items() if key in known}
    data["status"] = TaskStatus(data["status"])
    if account_id is not None:
        data["account_id"] = account_id
    if user_id is not None:
        data["user_id"] = user_id
    task = TaskRecord(**data)
    task._extra_fields = {
        key: deepcopy(value) for key, value in payload.items() if key not in known
    }
    return task


def _decode_bytes(raw: Any) -> str:
    return raw.decode() if isinstance(raw, bytes) else str(raw)
