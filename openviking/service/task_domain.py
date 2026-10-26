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

from openviking.service.task_events import append_task_event


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


_TASK_PATCHABLE_FIELDS = frozenset({"result", "stage", "meta", "auth"})


@dataclass(frozen=True)
class TaskPatch:
    """One atomic, field-scoped Task mutation.

    Non-``None`` values update their matching fields. ``clear_fields`` is the
    explicit counterpart for nullable fields, keeping ``None`` available to
    mean "leave unchanged". Status, errors, and Work state remain owned by
    their dedicated state-transition methods.
    """

    result: Optional[Dict[str, Any]] = None
    stage: Optional[str] = None
    meta: Optional[Dict[str, Any]] = None
    auth: Optional[Dict[str, Any]] = None
    clear_fields: frozenset[str] = field(default_factory=frozenset)
    add_enqueue_complete_work_ids: frozenset[str] = field(default_factory=frozenset)

    def __post_init__(self) -> None:
        clear_fields = frozenset(str(name) for name in self.clear_fields)
        unknown = clear_fields - _TASK_PATCHABLE_FIELDS
        if unknown:
            raise ValueError(f"unsupported TaskPatch clear fields: {sorted(unknown)}")
        for field_name in clear_fields:
            if getattr(self, field_name) is not None:
                raise ValueError(f"TaskPatch cannot update and clear {field_name}")
        object.__setattr__(self, "clear_fields", clear_fields)
        object.__setattr__(
            self,
            "add_enqueue_complete_work_ids",
            frozenset(str(work_id) for work_id in self.add_enqueue_complete_work_ids if work_id),
        )

    def is_empty(self) -> bool:
        return (
            self.result is None
            and self.stage is None
            and self.meta is None
            and self.auth is None
            and not self.clear_fields
            and not self.add_enqueue_complete_work_ids
        )


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
    account_id: Optional[str] = None
    user_id: Optional[str] = None

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


def append_task_events(task: TaskRecord, previous: Optional[TaskRecord]) -> None:
    """Append lifecycle events shared by all TaskStore implementations."""
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


def queue_result(summary: Optional[QueueWorkSummary]) -> Dict[str, Any]:
    """Project queue aggregates into the public Task result shape."""
    summary = summary or QueueWorkSummary()
    return {
        "processed": summary.processed,
        "requeue_count": summary.requeue_count,
        "error_count": summary.error_count,
        "errors": [{"message": error} for error in summary.errors[:MAX_QUEUE_ERROR_DETAILS]],
    }


def finalize_task(task: TaskRecord, works: Iterable[WorkRecord]) -> bool:
    """Apply the shared Task terminal-state and result projection rules."""
    works = list(works)
    if task.status not in ACTIVE_TASK_STATUSES or any(work.is_open() for work in works):
        return False
    if task.status == TaskStatus.CANCELLING:
        task.status = TaskStatus.CANCELLED
    else:
        work_error = first_work_error(works)
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
        summary = summarize_works(works)
        projected = {
            queue_name: queue_result(summary.queues.get(queue_name))
            for queue_name in ("Semantic", "Embedding")
        }
        if any(work.queue_name in projected for work in works):
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
    task.processing_seconds = processing_seconds(works)
    return True
