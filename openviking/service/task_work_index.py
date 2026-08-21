# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0
"""Runtime index of durable queue work owned by a tracked task.

QueueFS remains the durable source of truth.  This index is rebuilt from all
unacknowledged queue messages during startup and then maintained by enqueue/ACK
hooks.  It deliberately does not persist a second counter beside QueueFS.
"""

from __future__ import annotations

import asyncio
import json
import threading
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass
from typing import Any, Awaitable, Callable, Dict, Iterable, Iterator, Mapping, Optional
from uuid import uuid4

TASK_WORK_ID_FIELD = "_task_work_id"


class TaskWorkRejected(Exception):
    """Task cancellation prevented descendant queue work from being created."""


@dataclass(frozen=True)
class TaskExecutionContext:
    task_id: str
    account_id: str
    user_id: str


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
) -> Iterator[None]:
    """Propagate task ownership to queue messages produced by this coroutine."""
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


@contextmanager
def detach_task_context() -> Iterator[None]:
    """Keep independently scheduled work outside the current task lifecycle."""
    token = _current_task_context.set(None)
    try:
        yield
    finally:
        _current_task_context.reset(token)


def get_task_context() -> Optional[TaskExecutionContext]:
    return _current_task_context.get()


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
    return str(account_id or ""), str(user_id or "")


def prepare_task_payload(
    data: Dict[str, Any],
) -> tuple[Dict[str, Any], Optional[QueueTaskMetadata]]:
    """Copy a payload and attach durable task metadata when it belongs to a task."""
    payload = dict(data)
    current = _current_task_context.get()
    task_id = payload.get("task_id") or (current.task_id if current is not None else "")
    if not task_id:
        return payload, None

    task_id = str(task_id)
    account_id, user_id = _owner_from_payload(payload)
    if current is not None and current.task_id == task_id:
        account_id = account_id or current.account_id
        user_id = user_id or current.user_id

    work_id = str(payload.get(TASK_WORK_ID_FIELD) or uuid4())
    payload["task_id"] = task_id
    payload[TASK_WORK_ID_FIELD] = work_id
    if account_id and user_id:
        payload.setdefault("account_id", account_id)
        payload.setdefault("user_id", user_id)
    return payload, QueueTaskMetadata(task_id, work_id, account_id, user_id)


def extract_task_metadata(message: Any) -> Optional[QueueTaskMetadata]:
    """Read task metadata from either a QueueFS envelope or its inner payload."""
    payload = _payload_dict(message)
    if payload is None:
        return None
    task_id = payload.get("task_id")
    work_id = payload.get(TASK_WORK_ID_FIELD)
    if not work_id and isinstance(message, dict) and "data" in message and message.get("id"):
        # Messages produced before task work IDs were introduced can still be
        # rebuilt from their stable QueueFS envelope ID.
        work_id = f"queuefs:{message['id']}"
    if not task_id or not work_id:
        return None
    account_id, user_id = _owner_from_payload(payload)
    return QueueTaskMetadata(str(task_id), str(work_id), account_id, user_id)


class TaskWorkIndex:
    """Thread-safe, rebuildable index of persistent and currently active task work."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._work: Dict[str, set[tuple[str, str]]] = {}
        self._active: Dict[str, set[asyncio.Task[Any]]] = {}
        self._failures: Dict[str, str] = {}
        self._finalize_before_ack: Optional[Callable[[QueueTaskMetadata], Awaitable[None]]] = None
        self._is_cancellation_requested: Optional[Callable[[str], bool]] = None

    def set_callbacks(
        self,
        *,
        finalize_before_ack: Callable[[QueueTaskMetadata], Awaitable[None]],
        is_cancellation_requested: Callable[[str], bool],
    ) -> None:
        self._finalize_before_ack = finalize_before_ack
        self._is_cancellation_requested = is_cancellation_requested

    def rebuild(
        self,
        snapshots: Mapping[str, Iterable[Any]],
    ) -> Dict[str, tuple[str, str]]:
        """Rebuild work from QueueFS and return owners needed to restore task records."""
        work: Dict[str, set[tuple[str, str]]] = {}
        owners: Dict[str, tuple[str, str]] = {}
        for queue_name, messages in snapshots.items():
            for message in messages:
                metadata = extract_task_metadata(message)
                if metadata is None:
                    continue
                work.setdefault(metadata.task_id, set()).add((queue_name, metadata.work_id))
                if metadata.account_id and metadata.user_id:
                    owners[metadata.task_id] = (metadata.account_id, metadata.user_id)
        with self._lock:
            self._work = work
            self._failures = {}
        return owners

    def register(self, queue_name: str, metadata: Optional[QueueTaskMetadata]) -> bool:
        """Atomically reject cancelled work or add it to the runtime index."""
        if metadata is None:
            return True
        with self._lock:
            if self.cancellation_requested(metadata.task_id):
                return False
            self._work.setdefault(metadata.task_id, set()).add((queue_name, metadata.work_id))
        return True

    def _remove_work(
        self,
        queue_name: str,
        metadata: QueueTaskMetadata,
    ) -> tuple[bool, bool]:
        """Remove one work item and report whether it existed and made its task idle."""
        removed = False
        with self._lock:
            entries = self._work.get(metadata.task_id)
            if entries is not None:
                entry = (queue_name, metadata.work_id)
                removed = entry in entries
                entries.discard(entry)
                if not entries:
                    self._work.pop(metadata.task_id, None)
            became_idle = (
                removed
                and not self._work.get(metadata.task_id)
                and not self._active.get(metadata.task_id)
            )
            return removed, became_idle

    async def discard(self, queue_name: str, message: Any) -> None:
        """Discard work that was never durably enqueued and finalize if it was last."""
        metadata = (
            message if isinstance(message, QueueTaskMetadata) else extract_task_metadata(message)
        )
        if metadata is None:
            return
        _removed, became_idle = self._remove_work(queue_name, metadata)
        callback = self._finalize_before_ack
        if became_idle and callback is not None:
            await callback(metadata)

    async def prepare_ack(
        self,
        queue_name: str,
        message: Any,
    ) -> Optional[QueueTaskMetadata]:
        """Remove work provisionally and finalize its task before the last durable ACK."""
        metadata = (
            message if isinstance(message, QueueTaskMetadata) else extract_task_metadata(message)
        )
        if metadata is None:
            return None

        removed, became_idle = self._remove_work(queue_name, metadata)
        if not removed:
            return None
        if not became_idle:
            return metadata

        callback = self._finalize_before_ack
        if callback is None:
            return metadata
        try:
            await callback(metadata)
        except BaseException:
            self.rollback_ack(queue_name, metadata)
            raise
        return metadata

    def rollback_ack(self, queue_name: str, metadata: QueueTaskMetadata) -> None:
        """Restore provisionally removed work when finalization or ACK fails."""
        with self._lock:
            self._work.setdefault(metadata.task_id, set()).add((queue_name, metadata.work_id))

    def has_work(self, task_id: str, exclude_work_id: Optional[str] = None) -> bool:
        with self._lock:
            if exclude_work_id is not None:
                return any(
                    work_id != exclude_work_id
                    for _queue_name, work_id in self._work.get(task_id, ())
                )
            return bool(self._work.get(task_id) or self._active.get(task_id))

    def cancellation_requested(self, task_id: str) -> bool:
        callback = self._is_cancellation_requested
        return bool(callback is not None and callback(task_id))

    def record_failure(self, task_id: str, error: str) -> None:
        """Record the first failed work item owned by a task."""
        with self._lock:
            self._failures.setdefault(task_id, error)

    def failure(self, task_id: str) -> Optional[str]:
        with self._lock:
            return self._failures.get(task_id)

    def clear_failure(self, task_id: str) -> None:
        with self._lock:
            self._failures.pop(task_id, None)

    def register_active(
        self,
        task_id: str,
        active_task: asyncio.Task[Any],
    ) -> None:
        with self._lock:
            cancelled = self.cancellation_requested(task_id)
            if not cancelled:
                self._active.setdefault(task_id, set()).add(active_task)
        if cancelled:
            active_task.get_loop().call_soon(active_task.cancel)

    def unregister_active(self, task_id: str, active_task: asyncio.Task[Any]) -> None:
        with self._lock:
            entries = self._active.get(task_id)
            if entries is not None:
                entries.discard(active_task)
                if not entries:
                    self._active.pop(task_id, None)

    def cancel_active(self, task_id: str) -> None:
        with self._lock:
            active = list(self._active.get(task_id, ()))
        for task in active:
            task.get_loop().call_soon_threadsafe(task.cancel)
