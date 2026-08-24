# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0
"""Persistence-neutral task and queue-work state.

``TaskRecord`` describes a user-visible operation, ``WorkRecord`` describes one
queue execution owned by that task, and ``TaskAggregate`` applies their state
transitions. This module performs no storage or queue I/O.

Stores manage ``TaskRecord.version`` for optimistic concurrency. Terminal work
is retained until task cleanup so task completion and queue statistics can be
derived from the same state. Runtime objects such as ``asyncio.Task`` handles
never belong in these records.
"""

from __future__ import annotations

import math
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
from typing import Any, Dict, List, Optional


class TaskStatus(str, Enum):
    """Lifecycle states of a task."""

    PENDING = "pending"
    RUNNING = "running"
    CANCELLING = "cancelling"
    COMPLETED = "completed"
    FAILED = "failed"
    CANCELLED = "cancelled"


class WorkState(str, Enum):
    """Lifecycle states of a single queue-backed work unit."""

    PENDING = "pending"
    IN_PROGRESS = "in_progress"
    REQUEUED = "requeued"
    DONE = "done"
    FAILED = "failed"


class TaskWorkRejected(Exception):
    """Task lifecycle state prevented descendant work from being created."""


TERMINAL_TASK_STATUSES = (
    TaskStatus.COMPLETED,
    TaskStatus.FAILED,
    TaskStatus.CANCELLED,
)
ACTIVE_TASK_STATUSES = (
    TaskStatus.PENDING,
    TaskStatus.RUNNING,
    TaskStatus.CANCELLING,
)
_TERMINAL_WORK_STATES = (WorkState.REQUEUED, WorkState.DONE, WorkState.FAILED)


@dataclass
class TaskRecord:
    """Persistent task state.

    ``version`` starts at 0 and is managed by the store. The file store
    records a monotonically increasing revision; a distributed store uses it as
    the compare-and-swap token.
    """

    task_id: str
    task_type: str
    status: TaskStatus = TaskStatus.PENDING
    version: int = 0
    created_at: float = field(default_factory=time.time)
    updated_at: float = field(default_factory=time.time)
    resource_id: Optional[str] = None
    account_id: Optional[str] = None
    user_id: Optional[str] = None
    meta: Dict[str, Any] = field(default_factory=dict)
    stage: Optional[str] = None
    result: Optional[Dict[str, Any]] = None
    error: Optional[str] = None
    # Task-owned, short-lived credentials. Never exposed by ``to_dict`` and
    # cleared once the task reaches a terminal state.
    auth: Dict[str, Any] = field(default_factory=dict, repr=False)

    def to_dict(self) -> Dict[str, Any]:
        """Public API representation (explicit whitelist).

        ``version`` and task-owned works are internal concurrency/runtime
        details and must never leak into the historical /tasks response.
        Sanitization of meta/result remains in the tracker API adapter.
        """
        return {
            "task_id": self.task_id,
            "task_type": self.task_type,
            "status": self.status.value,
            "created_at": self.created_at,
            "updated_at": self.updated_at,
            "created_at_iso": datetime.fromtimestamp(self.created_at, tz=timezone.utc).isoformat(),
            "updated_at_iso": datetime.fromtimestamp(self.updated_at, tz=timezone.utc).isoformat(),
            "resource_id": self.resource_id,
            "meta": _sanitize_public_value(dict(self.meta)),
            "stage": self.stage,
            "result": _sanitize_public_value(self.result),
            "error": self.error,
        }

    def is_terminal(self) -> bool:
        return self.status in TERMINAL_TASK_STATUSES

    def is_active(self) -> bool:
        return self.status in ACTIVE_TASK_STATUSES

    @staticmethod
    def next_updated_at(previous: float) -> float:
        """Monotonically advance updated_at even within the same clock tick."""
        return max(time.time(), math.nextafter(previous, math.inf))


@dataclass
class WorkRecord:
    """Persistent snapshot of one queue-backed work unit owned by a task."""

    work_id: str
    task_id: str
    queue_name: str
    state: WorkState = WorkState.PENDING
    requeue_count: int = 0
    error: Optional[str] = None
    updated_at: float = field(default_factory=time.time)

    def is_open(self) -> bool:
        """A work item still gates its task until it reaches a terminal state."""
        return self.state not in _TERMINAL_WORK_STATES


@dataclass
class TaskAggregate:
    """A task and its work records, with storage-independent transitions.

    Stores may persist an aggregate as one blob or as separate task/work rows.
    """

    task: TaskRecord
    works: Dict[str, WorkRecord] = field(default_factory=dict)

    # ── Queries ──

    def has_open_work(self, exclude_work_id: Optional[str] = None) -> bool:
        """Whether the task still owns a non-terminal work unit."""
        for work_id, work in self.works.items():
            if exclude_work_id is not None and work_id == exclude_work_id:
                continue
            if work.is_open():
                return True
        return False

    def open_work_count(self) -> int:
        return sum(1 for work in self.works.values() if work.is_open())

    def queue_status(
        self, queue_names: tuple[str, ...] = ("Semantic", "Embedding")
    ) -> Dict[str, Dict[str, Any]]:
        """Project per-queue counters from the owned works.

        Preserves the existing task API shape:
        ``{queue_name: {processed, requeue_count, error_count, errors}}``.
        This is a read-only projection, not a second source of state.
        """
        status: Dict[str, Dict[str, Any]] = {
            queue_name: {
                "processed": 0,
                "requeue_count": 0,
                "error_count": 0,
                "errors": [],
            }
            for queue_name in queue_names
        }

        def bucket(queue_name: str) -> Dict[str, Any]:
            return status.setdefault(
                queue_name,
                {"processed": 0, "requeue_count": 0, "error_count": 0, "errors": []},
            )

        for work in self.works.values():
            if queue_names and work.queue_name not in queue_names:
                continue
            b = bucket(work.queue_name)
            b["requeue_count"] += work.requeue_count
            if work.state == WorkState.DONE:
                b["processed"] += 1
            elif work.state == WorkState.FAILED:
                b["error_count"] += 1
                if work.error:
                    b["errors"].append({"message": work.error})
        return status

    def first_work_error(self) -> Optional[str]:
        """The first recorded failure among owned works, if any."""
        for work in self.works.values():
            if work.state == WorkState.FAILED and work.error:
                return work.error
        return None

    # ── Pure transitions on works ──

    def register_work(self, work_id: str, queue_name: str) -> WorkRecord:
        """Add a work unit as pending. Idempotent per work_id."""
        existing = self.works.get(work_id)
        if existing is not None:
            return existing
        work = WorkRecord(work_id=work_id, task_id=self.task.task_id, queue_name=queue_name)
        self.works[work_id] = work
        return work

    def mark_work_done(self, work_id: str) -> None:
        work = self.works.get(work_id)
        if work is not None and work.is_open():
            work.state = WorkState.DONE
            work.updated_at = time.time()

    def record_work_error(self, work_id: str, error: Optional[str]) -> None:
        """Record an error while keeping unacknowledged work open."""
        work = self.works.get(work_id)
        if work is not None and error and not work.error:
            work.error = error
            work.updated_at = time.time()

    def start_work(self, work_id: str) -> None:
        """Start/retry one work attempt and clear a prior attempt error."""
        work = self.works.get(work_id)
        if work is not None and work.is_open():
            work.state = WorkState.IN_PROGRESS
            work.error = None
            work.updated_at = time.time()

    def mark_work_requeued(self, work_id: str, delta: int = 1) -> None:
        """Terminalize a work as requeued and count the retry in one step."""
        work = self.works.get(work_id)
        if work is not None:
            work.state = WorkState.REQUEUED
            if delta > 0:
                work.requeue_count += delta
            work.updated_at = time.time()

    def restore_work(self, work_id: str, queue_name: str) -> WorkRecord:
        """Restore missing/open transport work while preserving terminal work."""
        work = self.works.get(work_id)
        if work is None:
            work = WorkRecord(work_id=work_id, task_id=self.task.task_id, queue_name=queue_name)
            self.works[work_id] = work
        elif work.is_open():
            work.queue_name = queue_name
            work.state = WorkState.PENDING
            work.updated_at = time.time()
        return work

    def reopen_after_work_restore(self) -> None:
        """Undo pre-ACK terminalization when physical ACK fails."""
        if self.task.status == TaskStatus.CANCELLED:
            self.task.status = TaskStatus.CANCELLING
        elif self.task.status in (TaskStatus.COMPLETED, TaskStatus.FAILED):
            self.task.status = TaskStatus.RUNNING
        if self.task.status in ACTIVE_TASK_STATUSES:
            self.task.stage = self.task.status.value
            self.task.updated_at = TaskRecord.next_updated_at(self.task.updated_at)

    def mark_work_failed(self, work_id: str, error: Optional[str]) -> None:
        work = self.works.get(work_id)
        if work is None or work.state in (WorkState.DONE, WorkState.REQUEUED):
            return
        work.state = WorkState.FAILED
        if error:
            work.error = error
        work.updated_at = time.time()

    def open_work_ids(self) -> List[str]:
        return [work_id for work_id, work in self.works.items() if work.is_open()]


def _sanitize_public_value(value: Any) -> Any:
    """Preserve the historical API filtering of the internal user_key."""
    if isinstance(value, dict):
        return {
            key: _sanitize_public_value(item) for key, item in value.items() if key != "user_key"
        }
    if isinstance(value, list):
        return [_sanitize_public_value(item) for item in value]
    return value
