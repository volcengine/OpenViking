"""Content-free stage timings through the official observability event bus."""

from __future__ import annotations

import time
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass
from functools import wraps
from uuid import uuid4

_enabled = False
_current: ContextVar[Stage | None] = ContextVar("observability_stage", default=None)


@dataclass(frozen=True)
class Stage:
    stage_id: str
    parent_stage_id: str
    account_id: str | None
    user_id: str | None
    task_id: str | None
    name: str


def configure_performance(enabled: bool):
    global _enabled
    _enabled = bool(enabled)


def is_performance_enabled():
    return _enabled


def stage_fields(payload):
    current = _current.get()
    if current is None:
        return {}
    # An inherited stage is not evidence for a mixed-principal/background batch.
    if len(payload.get("source_task_ids") or []) > 1:
        return {}
    for key in ("account_id", "user_id", "task_id"):
        if payload.get(key) != getattr(current, key):
            return {}
    return {
        "stage_id": current.stage_id,
        "parent_stage_id": current.parent_stage_id,
        "stage": current.name,
    }


@contextmanager
def performance_stage(name):
    if not _enabled:
        yield
        return
    from openviking.observability.context import get_observability_context
    from openviking.observability.events import try_publish_event
    from openviking.service.task_work_index import get_task_context

    task = get_task_context()
    root = get_observability_context().root
    account = getattr(task, "account_id", None) or getattr(root, "account_id", None)
    user = getattr(task, "user_id", None) or getattr(root, "user_id", None)
    task_id = getattr(task, "task_id", None)
    parent = _current.get()
    related = parent and (parent.account_id, parent.user_id, parent.task_id) == (
        account,
        user,
        task_id,
    )
    current = Stage(
        uuid4().hex,
        parent.stage_id if parent is not None and related else "",
        account,
        user,
        task_id,
        name,
    )
    token = _current.set(current)
    started, outcome = time.time(), "success"
    monotonic = time.monotonic()
    try:
        yield
    except BaseException as exc:
        import asyncio

        outcome = "cancelled" if isinstance(exc, asyncio.CancelledError) else "failed"
        raise
    finally:
        elapsed = time.monotonic() - monotonic
        try_publish_event(
            "workflow.stage",
            {
                "stage": name,
                "stage_id": current.stage_id,
                "parent_stage_id": current.parent_stage_id,
                "started_at": started,
                "duration_seconds": elapsed,
                "status": outcome,
                "task_id": task_id,
                "account_id": account,
                "user_id": user,
            },
        )
        _current.reset(token)


def timed_stage(name):
    def decorate(function):
        @wraps(function)
        async def invoke(*args, **kwargs):
            with performance_stage(name):
                return await function(*args, **kwargs)

        return invoke

    return decorate
