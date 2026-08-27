# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0
"""Propagate the task owner through asynchronous execution.

Business code binds a task while it runs so nested operations can associate new
queue work with the same task. This module contains no queue or persistence
logic.
"""

from __future__ import annotations

from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass
from typing import Iterator, Optional


@dataclass(frozen=True)
class TaskExecutionContext:
    task_id: str
    account_id: str
    user_id: str


_current_task_context: ContextVar[Optional[TaskExecutionContext]] = ContextVar(
    "openviking_task_execution_context",
    default=None,
)


@contextmanager
def bind_task_context(task_id: str, account_id: str, user_id: str) -> Iterator[None]:
    """Expose task ownership to nested operations in the current context."""
    token = _current_task_context.set(
        TaskExecutionContext(
            task_id=str(task_id),
            account_id=str(account_id),
            user_id=str(user_id),
        )
    )
    try:
        yield
    finally:
        _current_task_context.reset(token)


def get_task_context() -> Optional[TaskExecutionContext]:
    """Return the task bound to the current context, if any."""
    return _current_task_context.get()


@contextmanager
def detach_task_context() -> Iterator[None]:
    """Keep independently scheduled work outside the current task lifecycle."""
    token = _current_task_context.set(None)
    try:
        yield
    finally:
        _current_task_context.reset(token)


__all__ = [
    "TaskExecutionContext",
    "bind_task_context",
    "detach_task_context",
    "get_task_context",
]
