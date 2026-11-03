# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0
"""Process-local execution context and cancellable handles for Task Work."""

from __future__ import annotations

import asyncio
import json
import threading
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass
from typing import Any, Dict, Generator, Mapping, Optional
from uuid import uuid4

from openviking.service.task_domain import TaskWorkRejected as TaskWorkRejected

TASK_WORK_ID_FIELD = "_task_work_id"
TASK_ACCOUNT_ID_FIELD = "_task_account_id"
TASK_USER_ID_FIELD = "_task_user_id"


@dataclass(frozen=True)
class TaskExecutionContext:
    task_id: str
    account_id: str
    user_id: str
    enqueued_work: Optional[list[tuple[str, str]]] = None
    work_id: Optional[str] = None


@dataclass(frozen=True)
class QueueTaskMetadata:
    task_id: str
    work_id: str
    account_id: str = ""
    user_id: str = ""


_current_task_context: ContextVar[Optional[TaskExecutionContext]] = ContextVar(
    "openviking_task_execution_context",
    default=None,
)


@contextmanager
def bind_task_context(
    task_id: str,
    account_id: str,
    user_id: str,
    *,
    enqueued_work: Optional[list[tuple[str, str]]] = None,
    work_id: Optional[str] = None,
) -> Generator[None, None, None]:
    """Propagate Task ownership to queue messages produced by this coroutine."""
    current = _current_task_context.get()
    if enqueued_work is None and current is not None and current.task_id == str(task_id):
        enqueued_work = current.enqueued_work
    if work_id is None and current is not None and current.task_id == str(task_id):
        work_id = current.work_id
    token = _current_task_context.set(
        TaskExecutionContext(
            task_id=str(task_id),
            account_id=str(account_id),
            user_id=str(user_id),
            enqueued_work=enqueued_work,
            work_id=work_id,
        )
    )
    try:
        yield
    finally:
        _current_task_context.reset(token)


@contextmanager
def detach_task_context() -> Generator[None, None, None]:
    """Keep independently scheduled work outside the current Task lifecycle."""
    token = _current_task_context.set(None)
    try:
        yield
    finally:
        _current_task_context.reset(token)


def get_task_context() -> Optional[TaskExecutionContext]:
    return _current_task_context.get()


def record_enqueued_work(queue_name: str, work_id: str) -> None:
    """Record a committed child enqueue for the currently executing handler."""
    current = _current_task_context.get()
    if current is not None and current.enqueued_work is not None:
        current.enqueued_work.append((queue_name, work_id))


def get_enqueued_work_ids(queue_name: Optional[str] = None) -> list[str]:
    """Return committed child Work IDs produced by the current handler."""
    current = _current_task_context.get()
    if current is None or current.enqueued_work is None:
        return []
    return [
        work_id
        for work_queue, work_id in current.enqueued_work
        if queue_name is None or work_queue == queue_name
    ]


def _payload_dict(message: Any) -> Optional[Dict[str, Any]]:
    if not isinstance(message, dict):
        return None
    payload: Any = message.get("data", message)
    if isinstance(payload, str):
        try:
            payload = json.loads(payload)
        except (TypeError, ValueError):
            return None
    return payload if isinstance(payload, dict) else None


def _owner_from_payload(payload: Mapping[str, Any]) -> tuple[str, str]:
    task_account_id = payload.get(TASK_ACCOUNT_ID_FIELD)
    task_user_id = payload.get(TASK_USER_ID_FIELD)
    if task_account_id and task_user_id:
        return str(task_account_id), str(task_user_id)

    account_id = payload.get("account_id")
    user_id = payload.get("user_id")

    user = payload.get("user")
    if isinstance(user, dict):
        account_id = account_id or user.get("account_id")
        user_id = user_id or user.get("user_id")

    context_data = payload.get("context_data")
    if isinstance(context_data, dict):
        account_id = account_id or context_data.get("account_id")
        user_id = user_id or context_data.get("owner_user_id")
    payload_data = payload.get("payload")
    if isinstance(payload_data, dict):
        context_data = payload_data.get("context_data")
        if isinstance(context_data, dict):
            account_id = account_id or context_data.get("account_id")
            user_id = user_id or context_data.get("owner_user_id")
    return str(account_id or ""), str(user_id or "")


def prepare_task_payload(
    data: Dict[str, Any],
) -> tuple[Dict[str, Any], Optional[QueueTaskMetadata]]:
    """Copy a payload and attach durable Task metadata when it belongs to a Task."""
    payload = dict(data)
    current = _current_task_context.get()
    task_id = payload.get("task_id") or (current.task_id if current is not None else "")
    if not task_id:
        return payload, None

    task_id = str(task_id)
    has_authoritative_context = (
        current is not None
        and current.task_id == task_id
        and current.account_id
        and current.user_id
    )
    if has_authoritative_context:
        account_id, user_id = current.account_id, current.user_id
    else:
        account_id, user_id = "", ""

    work_id = str(payload.get(TASK_WORK_ID_FIELD) or uuid4())
    payload["task_id"] = task_id
    payload[TASK_WORK_ID_FIELD] = work_id
    if has_authoritative_context:
        payload[TASK_ACCOUNT_ID_FIELD] = account_id
        payload[TASK_USER_ID_FIELD] = user_id
    if account_id and user_id:
        payload.setdefault("account_id", account_id)
        payload.setdefault("user_id", user_id)
    return payload, QueueTaskMetadata(task_id, work_id, account_id, user_id)


def extract_task_metadata(message: Any) -> Optional[QueueTaskMetadata]:
    """Read Task metadata from either a QueueFS envelope or its inner payload."""
    payload = _payload_dict(message)
    if payload is None:
        return None
    task_id = payload.get("task_id")
    work_id = payload.get(TASK_WORK_ID_FIELD)
    if not work_id and isinstance(message, dict) and "data" in message and message.get("id"):
        work_id = f"queuefs:{message['id']}"
    if not task_id or not work_id:
        return None
    account_id, user_id = _owner_from_payload(payload)
    return QueueTaskMetadata(str(task_id), str(work_id), account_id, user_id)


@contextmanager
def bind_task_context_from_message(message: Any) -> Generator[None, None, None]:
    """Bind TaskContext for direct queue handler calls that bypass middleware."""
    if _current_task_context.get() is not None:
        yield
        return

    metadata = extract_task_metadata(message)
    if metadata is None:
        payload = _payload_dict(message)
        if payload is None or not payload.get("task_id"):
            yield
            return
        account_id, user_id = _owner_from_payload(payload)
        metadata = QueueTaskMetadata(
            str(payload["task_id"]),
            str(payload.get(TASK_WORK_ID_FIELD) or ""),
            account_id,
            user_id,
        )
    if not metadata.account_id or not metadata.user_id:
        yield
        return

    with bind_task_context(
        metadata.task_id,
        metadata.account_id,
        metadata.user_id,
        work_id=metadata.work_id or None,
    ):
        yield


class TaskWorkIndex:
    """Process-local asyncio handles used to accelerate cancellation."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._active: Dict[tuple[str, str, str], set[asyncio.Task[Any]]] = {}

    def has_other_active(
        self,
        task_id: str,
        current_task: Optional[asyncio.Task[Any]],
        *,
        account_id: Optional[str] = None,
        user_id: Optional[str] = None,
    ) -> bool:
        """Return whether another in-process handler can finish producing Work."""
        with self._lock:
            return any(
                task is not current_task
                for (account, user, active_task_id), tasks in self._active.items()
                if active_task_id == task_id
                and (account_id is None or account == account_id)
                and (user_id is None or user == user_id)
                for task in tasks
            )

    def forget_task(
        self,
        task_id: str,
        *,
        account_id: Optional[str] = None,
        user_id: Optional[str] = None,
    ) -> None:
        with self._lock:
            for key in [
                key
                for key in self._active
                if key[2] == task_id
                and (account_id is None or key[0] == account_id)
                and (user_id is None or key[1] == user_id)
            ]:
                self._active.pop(key, None)

    def register_active(
        self,
        task_id: str,
        active_task: asyncio.Task[Any],
        *,
        account_id: str,
        user_id: str,
    ) -> None:
        with self._lock:
            self._active.setdefault((account_id, user_id, task_id), set()).add(active_task)

    def unregister_active(
        self,
        task_id: str,
        active_task: asyncio.Task[Any],
        *,
        account_id: str,
        user_id: str,
    ) -> None:
        with self._lock:
            key = (account_id, user_id, task_id)
            entries = self._active.get(key)
            if entries is not None:
                entries.discard(active_task)
                if not entries:
                    self._active.pop(key, None)

    def active_tasks_by_owner(self) -> Dict[tuple[str, str], set[str]]:
        with self._lock:
            grouped: Dict[tuple[str, str], set[str]] = {}
            for account_id, user_id, task_id in self._active:
                grouped.setdefault((account_id, user_id), set()).add(task_id)
            return grouped

    def cancel_active(
        self,
        task_id: str,
        *,
        account_id: Optional[str] = None,
        user_id: Optional[str] = None,
    ) -> None:
        with self._lock:
            active = [
                task
                for (account, user, active_task_id), tasks in self._active.items()
                if active_task_id == task_id
                and (account_id is None or account == account_id)
                and (user_id is None or user == user_id)
                for task in tasks
            ]
        for task in active:
            task.get_loop().call_soon_threadsafe(task.cancel)
