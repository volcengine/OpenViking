# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0
"""Runtime index of logical queue Work owned by tracked Tasks.

QueueFS remains the durable delivery source of truth. This index separates
logical Work state from delivery counts so a replacement delivery can retain
the same Work ID without allowing the old delivery's ACK to finish the Task.
"""

from __future__ import annotations

import asyncio
import json
import threading
import time
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass
from typing import Any, Awaitable, Callable, Dict, Generator, Iterable, Mapping, Optional
from uuid import uuid4

TASK_WORK_ID_FIELD = "_task_work_id"
TASK_ACCOUNT_ID_FIELD = "_task_account_id"
TASK_USER_ID_FIELD = "_task_user_id"
_TERMINAL_WORK_STATUSES = frozenset({"completed", "failed", "cancelled"})


class TaskWorkRejected(Exception):
    """Task cancellation prevented descendant queue work from being created."""


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


@dataclass
class WorkTiming:
    """Timestamps for one logical Work across all of its deliveries."""

    enqueued_at: float
    started_at: Optional[float] = None
    finished_at: Optional[float] = None


@dataclass
class WorkRecord:
    """Mutable process-local state for one logical Work."""

    task_id: str
    work_id: str
    queue_name: str
    status: str = "pending"
    delivery_count: int = 0
    requeue_count: int = 0
    error: Optional[str] = None
    errors: Optional[list[str]] = None
    context_count: int = 0
    timing: Optional[WorkTiming] = None

    @property
    def terminal(self) -> bool:
        return self.status in _TERMINAL_WORK_STATUSES


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
    """Thread-safe, rebuildable index of logical Work and active handlers."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._work: Dict[str, Dict[str, WorkRecord]] = {}
        self._delivery_counts: Dict[str, int] = {}
        self._active: Dict[str, set[asyncio.Task[Any]]] = {}
        self._failures: Dict[str, str] = {}
        self._work_timings: Dict[str, Dict[str, WorkTiming]] = {}
        self._active_work_ids: Dict[tuple[str, asyncio.Task[Any]], str] = {}
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
        """Rebuild unacknowledged deliveries and return Task owners to restore."""
        work: Dict[str, Dict[str, WorkRecord]] = {}
        delivery_counts: Dict[str, int] = {}
        owners: Dict[str, tuple[str, str]] = {}
        now = time.time()
        for queue_name, messages in snapshots.items():
            for message in messages:
                metadata = extract_task_metadata(message)
                if metadata is None:
                    continue
                records = work.setdefault(metadata.task_id, {})
                record = records.setdefault(
                    metadata.work_id,
                    WorkRecord(
                        task_id=metadata.task_id,
                        work_id=metadata.work_id,
                        queue_name=queue_name,
                        timing=WorkTiming(enqueued_at=now),
                    ),
                )
                record.delivery_count += 1
                delivery_counts[metadata.task_id] = delivery_counts.get(metadata.task_id, 0) + 1
                if metadata.account_id and metadata.user_id:
                    owners[metadata.task_id] = (metadata.account_id, metadata.user_id)
        with self._lock:
            self._work = work
            self._delivery_counts = delivery_counts
            self._failures = {}
        return owners

    def _add_delivery_locked(self, task_id: str) -> None:
        self._delivery_counts[task_id] = self._delivery_counts.get(task_id, 0) + 1

    def _remove_delivery_locked(self, task_id: str) -> None:
        remaining = self._delivery_counts.get(task_id, 0) - 1
        if remaining > 0:
            self._delivery_counts[task_id] = remaining
        else:
            self._delivery_counts.pop(task_id, None)

    def register(self, queue_name: str, metadata: Optional[QueueTaskMetadata]) -> bool:
        """Reject cancelled Work or provisionally register one delivery."""
        if metadata is None:
            return True
        with self._lock:
            if self.cancellation_requested(metadata.task_id):
                return False
            records = self._work.setdefault(metadata.task_id, {})
            record = records.get(metadata.work_id)
            now = time.time()
            if record is None:
                record = WorkRecord(
                    task_id=metadata.task_id,
                    work_id=metadata.work_id,
                    queue_name=queue_name,
                    timing=WorkTiming(enqueued_at=now),
                )
                records[metadata.work_id] = record
            elif record.queue_name != queue_name:
                raise ValueError(
                    f"Work {metadata.work_id} already belongs to queue {record.queue_name}"
                )
            record.delivery_count += 1
            self._add_delivery_locked(metadata.task_id)
            timings = self._work_timings.get(metadata.task_id)
            if timings is not None:
                record.timing = timings.setdefault(metadata.work_id, record.timing)
        return True

    def _remove_delivery(
        self,
        queue_name: str,
        metadata: QueueTaskMetadata,
    ) -> tuple[bool, bool]:
        """Remove one delivery and report whether its Task became idle."""
        with self._lock:
            record = self._work.get(metadata.task_id, {}).get(metadata.work_id)
            removed = bool(
                record is not None and record.queue_name == queue_name and record.delivery_count > 0
            )
            if removed:
                record.delivery_count -= 1
                self._remove_delivery_locked(metadata.task_id)
            became_idle = removed and not self._has_work_locked(metadata.task_id)
            return removed, became_idle

    def _has_delivery_locked(self, task_id: str, exclude_work_id: Optional[str] = None) -> bool:
        if exclude_work_id is not None:
            record = self._work.get(task_id, {}).get(exclude_work_id)
            excluded = record.delivery_count if record is not None else 0
            return self._delivery_counts.get(task_id, 0) > excluded
        return self._delivery_counts.get(task_id, 0) > 0

    def _has_work_locked(self, task_id: str, exclude_work_id: Optional[str] = None) -> bool:
        if self._has_delivery_locked(task_id, exclude_work_id):
            return True
        if exclude_work_id is not None:
            return False
        return bool(self._active.get(task_id))

    async def discard(self, queue_name: str, message: Any) -> None:
        """Discard a provisionally registered delivery that did not commit."""
        metadata = (
            message if isinstance(message, QueueTaskMetadata) else extract_task_metadata(message)
        )
        if metadata is None:
            return
        removed, became_idle = self._remove_delivery(queue_name, metadata)
        if removed:
            with self._lock:
                record = self._work.get(metadata.task_id, {}).get(metadata.work_id)
                if record is not None and record.delivery_count == 0 and not record.terminal:
                    self._work[metadata.task_id].pop(metadata.work_id, None)
                timings = self._work_timings.get(metadata.task_id)
                if timings is not None:
                    timings.pop(metadata.work_id, None)
        callback = self._finalize_before_ack
        if became_idle and callback is not None:
            await callback(metadata)

    async def fail_enqueue(
        self,
        queue_name: str,
        metadata: QueueTaskMetadata,
        error: str,
    ) -> None:
        """Keep a failed logical Work visible after its delivery failed to persist."""
        self.record_result(metadata, "failed", error=error)
        removed, became_idle = self._remove_delivery(queue_name, metadata)
        if not removed:
            return
        callback = self._finalize_before_ack
        if became_idle and callback is not None:
            await callback(metadata)

    async def prepare_ack(
        self,
        queue_name: str,
        message: Any,
    ) -> Optional[QueueTaskMetadata]:
        """Provisionally remove one delivery and finalize before the last ACK."""
        metadata = (
            message if isinstance(message, QueueTaskMetadata) else extract_task_metadata(message)
        )
        if metadata is None:
            return None

        removed, became_idle = self._remove_delivery(queue_name, metadata)
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
        """Restore a provisionally removed delivery when finalization or ACK fails."""
        with self._lock:
            record = self._work.get(metadata.task_id, {}).get(metadata.work_id)
            if record is None:
                record = WorkRecord(
                    task_id=metadata.task_id,
                    work_id=metadata.work_id,
                    queue_name=queue_name,
                    timing=WorkTiming(enqueued_at=time.time()),
                )
                self._work.setdefault(metadata.task_id, {})[metadata.work_id] = record
            record.delivery_count += 1
            self._add_delivery_locked(metadata.task_id)

    def has_work(self, task_id: str, exclude_work_id: Optional[str] = None) -> bool:
        with self._lock:
            return self._has_work_locked(task_id, exclude_work_id)

    def has_other_active(
        self,
        task_id: str,
        current_task: Optional[asyncio.Task[Any]],
    ) -> bool:
        """Return whether another in-process handler can finish producing Work."""
        with self._lock:
            return any(task is not current_task for task in self._active.get(task_id, ()))

    def cancellation_requested(self, task_id: str) -> bool:
        callback = self._is_cancellation_requested
        return bool(callback is not None and callback(task_id))

    def record_result(
        self,
        metadata: QueueTaskMetadata,
        outcome: str,
        *,
        error: Optional[str] = None,
        errors: Optional[Iterable[str]] = None,
    ) -> None:
        """Apply one delivery result to its logical Work monotonically."""
        with self._lock:
            record = self._work.get(metadata.task_id, {}).get(metadata.work_id)
            if record is None or record.terminal:
                return
            if outcome == "requeued":
                record.requeue_count += 1
                record.status = "pending"
                return
            if outcome not in _TERMINAL_WORK_STATUSES:
                raise ValueError(f"Unsupported Work outcome: {outcome}")
            record.status = outcome
            record.error = error if outcome == "failed" and error else None
            if outcome == "failed":
                details = [str(value) for value in errors or () if str(value)]
                record.errors = details or ([error] if error else [])
            if record.timing is not None:
                record.timing.finished_at = max(
                    record.timing.started_at or record.timing.enqueued_at,
                    time.time(),
                )
            if outcome == "failed" and error:
                self._failures.setdefault(metadata.task_id, error)

    def record_context_count(self, task_id: str, work_id: str, delta: int = 1) -> None:
        """Add a result count to one Work without changing its lifecycle."""
        if delta <= 0:
            return
        with self._lock:
            record = self._work.get(task_id, {}).get(work_id)
            if record is not None:
                record.context_count += delta

    def record_failure(self, task_id: str, error: str) -> None:
        """Record the first failed Work owned by a Task."""
        with self._lock:
            self._failures.setdefault(task_id, error)

    def failure(self, task_id: str) -> Optional[str]:
        with self._lock:
            return self._failures.get(task_id)

    def clear_failure(self, task_id: str) -> None:
        with self._lock:
            self._failures.pop(task_id, None)

    async def wait_for_work(
        self,
        task_id: str,
        work_ids: Iterable[str],
        *,
        timeout: Optional[float] = None,
        poll_interval: float = 0.05,
        stop_waiting: Optional[Callable[[], bool]] = None,
    ) -> None:
        """Wait until exactly the supplied logical Work IDs are terminal."""
        requested = frozenset(str(work_id) for work_id in work_ids)
        if not requested:
            return

        deadline = None if timeout is None else time.monotonic() + timeout
        while True:
            with self._lock:
                records = self._work.get(task_id, {})
                missing = requested.difference(records)
                done = not missing and all(records[work_id].terminal for work_id in requested)
            if done:
                return
            if missing:
                raise KeyError(f"Unknown Work for Task {task_id}: {sorted(missing)}")
            if stop_waiting is not None and stop_waiting():
                raise asyncio.CancelledError("Worker stopped with queued Work")
            remaining = None if deadline is None else deadline - time.monotonic()
            if remaining is not None and remaining <= 0:
                raise TimeoutError(f"Timed out waiting for Work in Task {task_id}")
            await asyncio.sleep(
                poll_interval if remaining is None else min(poll_interval, remaining)
            )

    async def wait_for_task_work(
        self,
        task_id: str,
        *,
        exclude_work_id: Optional[str] = None,
        timeout: Optional[float] = None,
        poll_interval: float = 0.05,
    ) -> None:
        """Drain Task deliveries, including newly enqueued and recovered Work.

        Only the Task coordinator may use this barrier, excluding its own Work.
        Inline coordinators have no delivery to exclude. Other workers must wait
        for explicit Work IDs to avoid waiting on each other.
        """
        deadline = None if timeout is None else time.monotonic() + timeout
        while True:
            with self._lock:
                pending = self._has_delivery_locked(task_id, exclude_work_id)
            if not pending:
                return
            remaining = None if deadline is None else deadline - time.monotonic()
            if remaining is not None and remaining <= 0:
                raise TimeoutError(f"Timed out waiting for Work in Task {task_id}")
            await asyncio.sleep(
                poll_interval if remaining is None else min(poll_interval, remaining)
            )

    def queue_status(
        self,
        task_id: str,
        work_ids: Optional[Iterable[str]] = None,
    ) -> Dict[str, Dict[str, object]]:
        """Project queue counters from logical Work records."""
        selected = None if work_ids is None else frozenset(str(value) for value in work_ids)
        with self._lock:
            records = [
                record
                for work_id, record in self._work.get(task_id, {}).items()
                if selected is None or work_id in selected
            ]
            result: Dict[str, Dict[str, object]] = {}
            for record in records:
                status = result.setdefault(
                    record.queue_name,
                    {
                        "processed": 0,
                        "requeue_count": 0,
                        "error_count": 0,
                        "errors": [],
                    },
                )
                if record.status == "completed":
                    status["processed"] += 1
                status["requeue_count"] += record.requeue_count
                if record.status == "failed":
                    errors = record.errors or ([record.error] if record.error else [])
                    status["error_count"] += len(errors) or 1
                    status["errors"].extend({"message": message} for message in errors)
            return result

    def queue_timing(self, task_id: str) -> Dict[str, Dict[str, float]]:
        """Project queue wait and execution timing in milliseconds."""
        with self._lock:
            result: Dict[str, Dict[str, float]] = {}
            for record in self._work.get(task_id, {}).values():
                timing = record.timing
                metrics = result.setdefault(
                    record.queue_name.lower(),
                    {"queue_wait_ms": 0.0, "execute_ms": 0.0},
                )
                if timing is None or timing.started_at is None:
                    continue
                metrics["queue_wait_ms"] += max(
                    0.0, (timing.started_at - timing.enqueued_at) * 1000.0
                )
                if timing.finished_at is not None:
                    metrics["execute_ms"] += max(
                        0.0, (timing.finished_at - timing.started_at) * 1000.0
                    )
            return result

    def context_count(self, task_id: str) -> int:
        with self._lock:
            return sum(record.context_count for record in self._work.get(task_id, {}).values())

    def init_processing(self, task_id: str) -> None:
        with self._lock:
            timings = self._work_timings.setdefault(task_id, {})
            for work_id, record in self._work.get(task_id, {}).items():
                if record.timing is not None:
                    timings.setdefault(work_id, record.timing)

    def processing_seconds(self, task_id: str) -> Optional[float]:
        with self._lock:
            timings = self._work_timings.get(task_id)
            if timings is None:
                return None
            now = time.time()
            intervals: list[tuple[float, float]] = []
            for timing in timings.values():
                if timing.started_at is None:
                    continue
                end = timing.finished_at if timing.finished_at is not None else now
                intervals.append((timing.started_at, max(timing.started_at, end)))
            intervals.sort()
        if not intervals:
            return None

        elapsed = 0.0
        current_start, current_end = intervals[0]
        for start, end in intervals[1:]:
            if start <= current_end:
                current_end = max(current_end, end)
            else:
                elapsed += current_end - current_start
                current_start, current_end = start, end
        return elapsed + current_end - current_start

    def forget_processing(self, task_id: str) -> None:
        with self._lock:
            self._work_timings.pop(task_id, None)

    def forget_task(self, task_id: str) -> None:
        """Release all process-local state after the owning Task is removed."""
        with self._lock:
            self._work.pop(task_id, None)
            self._delivery_counts.pop(task_id, None)
            self._active.pop(task_id, None)
            self._failures.pop(task_id, None)
            self._work_timings.pop(task_id, None)
            active_keys = [key for key in self._active_work_ids if key[0] == task_id]
            for key in active_keys:
                self._active_work_ids.pop(key, None)

    def register_active(
        self,
        task_id: str,
        active_task: asyncio.Task[Any],
        *,
        work_id: Optional[str] = None,
    ) -> None:
        with self._lock:
            cancelled = self.cancellation_requested(task_id)
            if not cancelled:
                self._active.setdefault(task_id, set()).add(active_task)
                now = time.time()
                timing_id = work_id or f"inline:{id(active_task)}"
                record = self._work.get(task_id, {}).get(timing_id)
                if record is not None:
                    timing = record.timing
                    if timing is not None and timing.started_at is None:
                        timing.started_at = now
                    if not record.terminal:
                        record.status = "running"
                timings = self._work_timings.get(task_id)
                if timings is not None:
                    timing = timings.setdefault(timing_id, WorkTiming(enqueued_at=now))
                    if timing.started_at is None:
                        timing.started_at = now
                    timing.finished_at = None
                    self._active_work_ids[(task_id, active_task)] = timing_id
        if cancelled:
            active_task.get_loop().call_soon(active_task.cancel)

    def unregister_active(self, task_id: str, active_task: asyncio.Task[Any]) -> None:
        with self._lock:
            timing_id = self._active_work_ids.pop((task_id, active_task), None)
            timings = self._work_timings.get(task_id)
            if timing_id is not None and timings is not None:
                timing = timings.get(timing_id)
                if timing is not None and timing.started_at is not None:
                    timing.finished_at = max(timing.started_at, time.time())
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
