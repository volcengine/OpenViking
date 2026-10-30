# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0
"""Task execution history and committed lifecycle notifications."""

import json
from datetime import datetime, timezone
from typing import TYPE_CHECKING, Optional, TypedDict

if TYPE_CHECKING:
    from openviking.service.task_domain import TaskRecord

MAX_TASK_EVENTS = 64
MAX_TASK_EVENT_BYTES = 32 * 1024
PROCESS_EVENT_KINDS = frozenset({"waiting_for_descendants"})


class TaskEvent(TypedDict):
    seq: int
    recorded_at: str
    kind: str
    status: str
    stage: Optional[str]
    operation: Optional[str]
    error: Optional[str]


class TaskEventHistory(TypedDict):
    items: list[TaskEvent]
    dropped_count: int
    started_mid_task: bool


def append_task_event(
    history: Optional[TaskEventHistory],
    *,
    kind: str,
    status: str,
    stage: Optional[str],
    error: Optional[str] = None,
    operation: Optional[str] = None,
    recorded_at: Optional[str] = None,
) -> TaskEventHistory:
    """Append to an unpublished snapshot; callers supply only public, sanitized fields."""
    if history is None:
        history = {"items": [], "dropped_count": 0, "started_mid_task": kind != "created"}
    items = history["items"]
    items.append(
        {
            "seq": items[-1]["seq"] + 1 if items else 1,
            "recorded_at": recorded_at or datetime.now(timezone.utc).isoformat(),
            "kind": kind,
            "status": status,
            "stage": stage[:128] if stage is not None else None,
            "operation": operation,
            "error": error,
        }
    )
    while (
        len(items) > MAX_TASK_EVENTS
        or len(json.dumps(history).encode("utf-8")) > MAX_TASK_EVENT_BYTES
    ):
        items.pop(0)
        history["dropped_count"] += 1
    return history


def publish_task_finished(task: "TaskRecord") -> None:
    """Publish a committed terminal transition, never an attempted completion."""
    from openviking.observability.events import try_publish_event

    try_publish_event(
        "task.finished",
        {
            "task_type": task.task_type,
            "status": task.status.value,
            "error_code": task.error_code,
            "end_to_end_duration_seconds": max(0.0, task.updated_at - task.created_at),
        },
    )
