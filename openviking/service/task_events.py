# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0
"""Bounded, task-owned execution history stored with each task snapshot."""

import json
import re
from copy import deepcopy
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Optional, TypedDict
from uuid import uuid4

from typing_extensions import NotRequired

MAX_TASK_EVENTS = 64
MAX_TASK_EVENT_BYTES = 32 * 1024
OPERATION_EVENT_KINDS = frozenset(
    {
        "operation_started",
        "operation_completed",
        "operation_failed",
        "operation_cancelled",
        "operation_skipped",
    }
)
PROCESS_EVENT_KINDS = OPERATION_EVENT_KINDS | {"waiting_for_descendants"}
SKIP_REASONS = frozenset(
    {
        "working_memory_disabled",
        "extractor_unavailable",
        "extraction_disabled",
        "no_eligible_scope",
        "no_memory_types",
        "no_pending_messages",
    }
)


class TaskEventData(TypedDict):
    recorded_at: str
    kind: str
    status: str
    stage: Optional[str]
    operation: Optional[str]
    error: Optional[str]
    event_id: NotRequired[str]
    reason: NotRequired[str]


class TaskEvent(TaskEventData):
    seq: int


class TaskEventHistory(TypedDict):
    items: list[TaskEvent]
    dropped_count: int
    started_mid_task: bool
    discarded_count: NotRequired[int]


class PendingTaskEvents(TypedDict):
    items: list[TaskEventData]
    dropped_count: int


def make_process_event(
    kind: str,
    *,
    status: str,
    stage: Optional[str],
    operation: Optional[str],
    error: Optional[str] = None,
    reason: Optional[str] = None,
) -> TaskEventData:
    """Validate the small, public event payload before admitting it to memory."""
    if kind not in PROCESS_EVENT_KINDS:
        raise ValueError(f"Unknown task process event: {kind}")
    if operation is not None and not re.fullmatch(r"[\w.:-]{1,128}", operation):
        raise ValueError("operation must be a bounded operation identifier")
    if kind in OPERATION_EVENT_KINDS and not operation:
        raise ValueError("operation is required")
    if (error is not None) != (kind == "operation_failed"):
        raise ValueError("only operation_failed requires error")
    if kind == "operation_skipped":
        if reason not in SKIP_REASONS:
            raise ValueError("a registered skip reason is required")
    elif reason is not None:
        raise ValueError("only operation_skipped accepts reason")
    event: TaskEventData = {
        "event_id": str(uuid4()),
        "recorded_at": datetime.now(timezone.utc).isoformat(),
        "kind": kind,
        "status": status,
        "stage": stage,
        "operation": operation,
        "error": error,
    }
    if reason is not None:
        event["reason"] = reason
    return event


@dataclass
class _PendingBatch:
    entries: list[tuple[TaskEventData, int]] = field(default_factory=list)
    size: int = 0
    dropped_count: int = 0


class TaskEventBuffer:
    """Bounded observations, guarded by TaskTracker's short in-memory lock.

    Snapshotting does not dequeue: a failed business write retains its batch,
    while acknowledgement removes only the events included in a successful write.
    """

    MAX_TASKS = 1024
    MAX_BYTES = 8 * 1024 * 1024

    def __init__(self) -> None:
        self._tasks: dict[str, _PendingBatch] = {}
        self.bytes = 0

    def add(self, task_id: str, event: TaskEventData) -> bool:
        size = len(json.dumps(event).encode("utf-8"))
        batch = self._tasks.get(task_id)
        if batch is None:
            if len(self._tasks) >= self.MAX_TASKS:
                return False
            batch = self._tasks[task_id] = _PendingBatch()
        if (
            len(batch.entries) >= MAX_TASK_EVENTS
            or batch.size + size > MAX_TASK_EVENT_BYTES
            or self.bytes + size > self.MAX_BYTES
        ):
            batch.dropped_count += 1
            return False
        batch.entries.append((event, size))
        batch.size += size
        self.bytes += size
        return True

    def snapshot(self, task_id: str) -> PendingTaskEvents:
        batch = self._tasks.get(task_id)
        return {
            "items": deepcopy([event for event, _ in batch.entries]) if batch else [],
            "dropped_count": batch.dropped_count if batch else 0,
        }

    def note_drop(self, task_id: str) -> None:
        batch = self._tasks.get(task_id)
        if batch is None and len(self._tasks) < self.MAX_TASKS:
            batch = self._tasks[task_id] = _PendingBatch()
        if batch is not None:
            batch.dropped_count += 1

    def acknowledge(self, task_id: str, saved: PendingTaskEvents) -> None:
        batch = self._tasks.get(task_id)
        if batch is None:
            return
        ids = {event["event_id"] for event in saved["items"]}
        retained = [(event, size) for event, size in batch.entries if event["event_id"] not in ids]
        size = sum(size for _, size in retained)
        self.bytes -= batch.size - size
        batch.entries, batch.size = retained, size
        batch.dropped_count = max(0, batch.dropped_count - saved["dropped_count"])
        if not retained and not batch.dropped_count:
            self._tasks.pop(task_id)

    def discard(self, task_id: str) -> None:
        batch = self._tasks.pop(task_id, None)
        if batch:
            self.bytes -= batch.size


def append_task_event(
    history: Optional[TaskEventHistory],
    *,
    kind: str,
    status: str,
    stage: Optional[str],
    error: Optional[str] = None,
    operation: Optional[str] = None,
    recorded_at: Optional[str] = None,
    event_id: Optional[str] = None,
    reason: Optional[str] = None,
) -> TaskEventHistory:
    """Append to an unpublished snapshot; callers supply only public, sanitized fields."""
    if history is None:
        history = {"items": [], "dropped_count": 0, "started_mid_task": kind != "created"}
    items = history["items"]
    event: TaskEvent = {
        "seq": items[-1]["seq"] + 1 if items else 1,
        "recorded_at": recorded_at or datetime.now(timezone.utc).isoformat(),
        "kind": kind,
        "status": status,
        "stage": stage[:128] if stage is not None else None,
        "operation": operation,
        "error": error,
    }
    if event_id:
        event["event_id"] = event_id
    if reason:
        event["reason"] = reason
    items.append(event)
    trim_task_events(history)
    return history


def trim_task_events(history: TaskEventHistory) -> None:
    items = history["items"]
    while items and (
        len(items) > MAX_TASK_EVENTS
        or len(json.dumps(history).encode("utf-8")) > MAX_TASK_EVENT_BYTES
    ):
        items.pop(0)
        history["dropped_count"] += 1
