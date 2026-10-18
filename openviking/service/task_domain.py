# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0
"""Persistence-neutral Task and Work records."""

from __future__ import annotations

import math
import re
import time
from copy import deepcopy
from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
from typing import Any, Dict, Iterable, Optional


class TaskStatus(str, Enum):
    PENDING = "pending"
    RUNNING = "running"
    CANCELLING = "cancelling"
    COMPLETED = "completed"
    FAILED = "failed"
    CANCELLED = "cancelled"


class WorkState(str, Enum):
    PENDING = "pending"
    RUNNING = "running"
    DONE = "done"
    FAILED = "failed"


class WorkRecoveryMode(str, Enum):
    """How a Store rebuilds Work state after process startup."""

    QUEUE_SNAPSHOT = "queue_snapshot"
    DURABLE_STORE = "durable_store"


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
TERMINAL_WORK_STATES = (WorkState.DONE, WorkState.FAILED)
MAX_QUEUE_ERROR_DETAILS = 100


class TaskWorkRejected(Exception):
    """Task lifecycle state prevented Work registration."""


@dataclass(frozen=True)
class TaskRetentionPolicy:
    completed_seconds: float
    failed_seconds: float

    def is_expired(self, status: str, updated_at: float, now: float) -> bool:
        if status in ("completed", "cancelled"):
            return now - updated_at > self.completed_seconds
        return status == "failed" and now - updated_at > self.failed_seconds


@dataclass(frozen=True)
class DeletedTask:
    task_id: str
    account_id: str
    user_id: str


@dataclass(frozen=True)
class DeleteExpiredResult:
    deleted: tuple[DeletedTask, ...] = ()
    scanned: int = 0
    scan_complete: bool = False

    @property
    def deleted_count(self) -> int:
        return len(self.deleted)


@dataclass
class QueueWorkSummary:
    processed: int = 0
    requeue_count: int = 0
    error_count: int = 0
    errors: list[str] = field(default_factory=list)
    queue_wait_ms: float = 0.0
    execute_ms: float = 0.0


@dataclass
class WorkSummary:
    queues: Dict[str, QueueWorkSummary] = field(default_factory=dict)
    context_count: int = 0


SENSITIVE_TASK_KEYS = frozenset({"api_key", "user_key"})
_SENSITIVE_PATTERNS = re.compile(
    r"(sk-|cr_|ghp_|ntn_|xox[baprs]-|Bearer\s+)[a-zA-Z0-9._-]+",
    re.IGNORECASE,
)
_MAX_ERROR_LEN = 500


def sanitize_error(error: str) -> str:
    sanitized = _SENSITIVE_PATTERNS.sub("[REDACTED]", error)
    if len(sanitized) > _MAX_ERROR_LEN:
        sanitized = sanitized[:_MAX_ERROR_LEN] + "...[truncated]"
    return sanitized


def sanitize_task_result(result: Any) -> Any:
    if isinstance(result, dict):
        return {
            key: sanitize_task_result(value)
            for key, value in result.items()
            if key not in SENSITIVE_TASK_KEYS
        }
    if isinstance(result, list):
        return [sanitize_task_result(item) for item in result]
    return result


@dataclass
class TaskRecord:
    task_id: str
    task_type: str
    status: TaskStatus = TaskStatus.PENDING
    created_at: float = field(default_factory=time.time)
    updated_at: float = field(default_factory=time.time)
    resource_id: Optional[str] = None
    account_id: Optional[str] = None
    user_id: Optional[str] = None
    meta: Dict[str, Any] = field(default_factory=dict)
    stage: Optional[str] = None
    result: Optional[Dict[str, Any]] = None
    error: Optional[str] = None
    auth: Dict[str, Any] = field(default_factory=dict, repr=False)
    execution_events: Optional[Any] = None
    processing_seconds: Optional[float] = None
    _extra_fields: Dict[str, Any] = field(
        default_factory=dict, init=False, repr=False, compare=False
    )

    def to_dict(self, *, include_events: bool = False) -> Dict[str, Any]:
        public_meta = deepcopy(self.meta)
        public_meta.pop("submission_hash", None)
        public_meta.pop("submission_token", None)
        return {
            **({"execution_events": deepcopy(self.execution_events)} if include_events else {}),
            "task_id": self.task_id,
            "task_type": self.task_type,
            "status": self.status.value,
            "created_at": self.created_at,
            "updated_at": self.updated_at,
            "processing_seconds": self.processing_seconds,
            "resource_id": self.resource_id,
            "meta": sanitize_task_result(public_meta),
            "stage": self.stage,
            "result": sanitize_task_result(deepcopy(self.result)),
            "error": self.error,
            "created_at_iso": datetime.fromtimestamp(self.created_at, tz=timezone.utc).isoformat(),
            "updated_at_iso": datetime.fromtimestamp(self.updated_at, tz=timezone.utc).isoformat(),
        }

    def is_terminal(self) -> bool:
        return self.status in TERMINAL_TASK_STATUSES

    def is_active(self) -> bool:
        return self.status in ACTIVE_TASK_STATUSES

    @staticmethod
    def next_updated_at(previous: float) -> float:
        return max(time.time(), math.nextafter(previous, math.inf))


@dataclass
class WorkRecord:
    work_id: str
    task_id: str
    queue_name: str
    state: WorkState = WorkState.PENDING
    requeue_count: int = 0
    error: Optional[str] = None
    errors: list[str] = field(default_factory=list)
    context_count: int = 0
    enqueued_at: float = field(default_factory=time.time)
    started_at: Optional[float] = None
    finished_at: Optional[float] = None
    updated_at: float = field(default_factory=time.time)

    def is_open(self) -> bool:
        return self.state not in TERMINAL_WORK_STATES


def processing_seconds(
    works: Iterable[WorkRecord], *, now: Optional[float] = None
) -> Optional[float]:
    """Return the wall-clock union of claimed Work intervals."""
    current = time.time() if now is None else now
    intervals = sorted(
        (
            work.started_at,
            max(
                work.started_at,
                work.finished_at if work.finished_at is not None else current,
            ),
        )
        for work in works
        if work.started_at is not None
    )
    if not intervals:
        return None
    elapsed = 0.0
    left, right = intervals[0]
    for start, end in intervals[1:]:
        if start <= right:
            right = max(right, end)
        else:
            elapsed += right - left
            left, right = start, end
    return elapsed + right - left


def summarize_works(works: Iterable[WorkRecord]) -> WorkSummary:
    """Aggregate persisted Work facts without imposing an API response shape."""
    summary = WorkSummary()
    for work in works:
        bucket = summary.queues.setdefault(work.queue_name, QueueWorkSummary())
        bucket.requeue_count += work.requeue_count
        summary.context_count += work.context_count
        if work.state == WorkState.DONE:
            bucket.processed += 1
        elif work.state == WorkState.FAILED:
            errors = work.errors or ([work.error] if work.error else [])
            bucket.error_count += len(errors) or 1
            bucket.errors.extend(errors)
        if work.started_at is None:
            continue
        bucket.queue_wait_ms += max(0.0, (work.started_at - work.enqueued_at) * 1000.0)
        if work.finished_at is not None:
            bucket.execute_ms += max(0.0, (work.finished_at - work.started_at) * 1000.0)
    return summary


def first_work_error(works: Iterable[WorkRecord]) -> Optional[str]:
    return next(
        (work.error for work in works if work.state == WorkState.FAILED and work.error),
        None,
    )
