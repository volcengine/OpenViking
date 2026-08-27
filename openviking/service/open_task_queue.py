# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0
"""QueueFS-backed external compile task queue."""

from __future__ import annotations

import json
import math
import time
from contextlib import asynccontextmanager
from copy import deepcopy
from dataclasses import dataclass
from typing import Any, AsyncIterator, Callable, Optional
from uuid import uuid4

from openviking.core.identifiers import validate_account_id, validate_user_id
from openviking.pyagfs import AsyncAGFSClient
from openviking.pyagfs.exceptions import AGFSNotFoundError
from openviking.service.task_store import PersistentTaskStore, task_record_path
from openviking.service.task_tracker import TaskRecord, TaskStatus
from openviking.storage.queuefs.named_queue import NamedQueue
from openviking_cli.exceptions import (
    ConflictError,
    FailedPreconditionError,
    InvalidArgumentError,
    NotFoundError,
)

DEFAULT_LEASE_SECONDS = 600.0
QUEUE_MOUNT_POINT = "/queue"
OPEN_COMPILE_QUEUE = "OpenCompileTask"
OPEN_TASK_AUTH_KEY = "open_task_queue"
OPEN_TASK_META_KEY = "__open_task_queue"
MAX_CLAIM_DEQUEUE_ATTEMPTS = 100

_COMPILE_TASK_TYPE = "compile"
_TERMINAL_STATUSES = {TaskStatus.COMPLETED, TaskStatus.FAILED}


@dataclass(frozen=True)
class _ClaimResult:
    task: Optional[TaskRecord]
    ack_message: bool = False


class OpenTaskQueueService:
    """Open compile queue API backed by QueueFS and standard task records."""

    def __init__(
        self,
        agfs: Any,
        *,
        lease_seconds: float = DEFAULT_LEASE_SECONDS,
        queue_factory: Optional[Callable[[str], Any]] = None,
    ) -> None:
        if isinstance(agfs, AsyncAGFSClient):
            self._agfs = agfs
            sync_agfs = agfs._client
        else:
            sync_agfs = agfs
            self._agfs = AsyncAGFSClient(agfs)
        self._store = PersistentTaskStore(self._agfs)
        self._lease_seconds = float(lease_seconds)
        self._queue = (
            queue_factory(OPEN_COMPILE_QUEUE)
            if queue_factory is not None
            else NamedQueue(sync_agfs, QUEUE_MOUNT_POINT, OPEN_COMPILE_QUEUE)
        )

    async def create_compile_task(
        self,
        *,
        account_id: str,
        user_id: str,
        payload: dict[str, Any],
    ) -> TaskRecord:
        _validate_owner(account_id, user_id)
        task = TaskRecord(
            task_id=str(uuid4()),
            task_type=_COMPILE_TASK_TYPE,
            status=TaskStatus.PENDING,
            account_id=account_id,
            user_id=user_id,
            stage="queued",
            meta={
                OPEN_TASK_META_KEY: OPEN_COMPILE_QUEUE,
                "payload": deepcopy(payload),
                "attempt": 0,
            },
        )
        await self._store.create(task)
        try:
            await self._queue.enqueue(_queue_delivery_payload(task))
        except Exception as exc:
            await self._mark_enqueue_failed(task, exc)
            raise
        return task

    async def claim_compile_task(
        self,
        *,
        worker_account_id: str,
        worker_user_id: str,
    ) -> Optional[TaskRecord]:
        _validate_owner(worker_account_id, worker_user_id)
        for _ in range(MAX_CLAIM_DEQUEUE_ATTEMPTS):
            message = await self._queue.dequeue_raw()
            if message is None:
                return None

            parsed = _parse_queue_message(message)
            if parsed is None:
                await self._ack_malformed_message(message)
                continue
            msg_id, payload = parsed

            if payload.get("task_type") != _COMPILE_TASK_TYPE:
                await self._queue.ack(msg_id, message)
                continue

            owner = _queue_owner(payload)
            if owner is None:
                await self._queue.ack(msg_id, message)
                continue

            claim = await self._claim_task(
                account_id=owner[0],
                user_id=owner[1],
                claimed_by_account_id=worker_account_id,
                claimed_by_user_id=worker_user_id,
                task_id=str(payload.get("task_id", "")),
                queue_message_id=msg_id,
            )
            if claim.task is not None:
                return claim.task
            if claim.ack_message:
                await self._queue.ack(msg_id, message)
        return None

    async def update_task(
        self,
        *,
        account_id: str,
        user_id: str,
        task_id: str,
        lease_id: str,
        updates: dict[str, Any],
    ) -> TaskRecord:
        if not updates:
            raise InvalidArgumentError("At least one task update field is required")

        def apply(task: TaskRecord, now: float) -> None:
            if task.status != TaskStatus.RUNNING:
                raise FailedPreconditionError("Task is not running")
            for field_name in ("message", "progress", "details"):
                if field_name in updates:
                    task.meta[field_name] = updates[field_name]
            if "stage" in updates:
                task.stage = updates["stage"]
            task.meta["lease_expires_at"] = now + self._lease_seconds

        return await self._mutate_with_lease(
            account_id=account_id,
            user_id=user_id,
            task_id=task_id,
            lease_id=lease_id,
            mutate=apply,
        )

    async def complete_task(
        self,
        *,
        account_id: str,
        user_id: str,
        task_id: str,
        lease_id: str,
        result: dict[str, Any],
    ) -> TaskRecord:
        return await self._finish_task(
            account_id=account_id,
            user_id=user_id,
            task_id=task_id,
            lease_id=lease_id,
            status=TaskStatus.COMPLETED,
            result=result,
            error=None,
        )

    async def fail_task(
        self,
        *,
        account_id: str,
        user_id: str,
        task_id: str,
        lease_id: str,
        error: dict[str, Any],
    ) -> TaskRecord:
        return await self._finish_task(
            account_id=account_id,
            user_id=user_id,
            task_id=task_id,
            lease_id=lease_id,
            status=TaskStatus.FAILED,
            result=None,
            error=error,
        )

    async def ack_task(
        self,
        *,
        account_id: str,
        user_id: str,
        task_id: str,
        lease_id: str,
        ack_by_account_id: str,
        ack_by_user_id: str,
    ) -> TaskRecord:
        queue_message_id = ""

        def mark_acked(task: TaskRecord, now: float) -> None:
            nonlocal queue_message_id
            if task.status not in _TERMINAL_STATUSES:
                raise FailedPreconditionError("Only completed or failed tasks can be acked")
            queue_message_id = str(_task_auth(task).get("queue_message_id") or "")
            if not queue_message_id:
                raise FailedPreconditionError("Task has no QueueFS delivery to ack")
            task.meta["acknowledged_at"] = task.meta.get("acknowledged_at") or now
            task.meta["ack_by_account_id"] = task.meta.get("ack_by_account_id") or ack_by_account_id
            task.meta["ack_by"] = task.meta.get("ack_by") or ack_by_user_id

        task = await self._mutate_with_lease(
            account_id=account_id,
            user_id=user_id,
            task_id=task_id,
            lease_id=lease_id,
            mutate=mark_acked,
            require_running=False,
        )
        await self._queue.ack(queue_message_id, _queue_delivery_payload(task))
        return task

    async def _finish_task(
        self,
        *,
        account_id: str,
        user_id: str,
        task_id: str,
        lease_id: str,
        status: TaskStatus,
        result: Optional[dict[str, Any]],
        error: Optional[dict[str, Any]],
    ) -> TaskRecord:
        def finish(task: TaskRecord, now: float) -> None:
            if task.status != TaskStatus.RUNNING:
                raise FailedPreconditionError("Task is not running")
            task.status = status
            task.stage = status.value
            task.result = deepcopy(result)
            task.error = error.get("message") if error else None
            task.meta["error"] = deepcopy(error)
            task.meta["lease_expires_at"] = now + self._lease_seconds
            if status == TaskStatus.COMPLETED:
                task.meta["progress"] = 1.0

        return await self._mutate_with_lease(
            account_id=account_id,
            user_id=user_id,
            task_id=task_id,
            lease_id=lease_id,
            mutate=finish,
        )

    async def _claim_task(
        self,
        *,
        account_id: str,
        user_id: str,
        claimed_by_account_id: str,
        claimed_by_user_id: str,
        task_id: str,
        queue_message_id: str,
    ) -> _ClaimResult:
        try:
            _validate_owner(account_id, user_id)
            _validate_task_id(task_id)
        except InvalidArgumentError:
            return _ClaimResult(None, ack_message=True)

        now = time.time()
        async with self._locked_task(account_id, user_id, task_id):
            task = await self._read_task(account_id, user_id, task_id)
            if task is None or not _is_open_compile_task(task):
                return _ClaimResult(None, ack_message=True)
            if task.meta.get("acknowledged_at") is not None:
                return _ClaimResult(None, ack_message=True)
            if task.status == TaskStatus.RUNNING and not _lease_expired(task, now):
                return _ClaimResult(None)

            updated = deepcopy(task)
            updated.auth[OPEN_TASK_AUTH_KEY] = {
                "lease_id": f"lease_{uuid4().hex}",
                "queue_message_id": queue_message_id,
            }
            updated.meta["lease_expires_at"] = now + self._lease_seconds
            updated.meta["claimed_by_account_id"] = claimed_by_account_id
            updated.meta["claimed_by_user_id"] = claimed_by_user_id
            if updated.status not in _TERMINAL_STATUSES:
                updated.status = TaskStatus.RUNNING
                updated.stage = "running"
                updated.meta["attempt"] = int(updated.meta.get("attempt") or 0) + 1
            updated.updated_at = _next_updated_at(task, now=now)
            await self._store.update(updated)
            return _ClaimResult(updated)

    async def _mutate_with_lease(
        self,
        *,
        account_id: str,
        user_id: str,
        task_id: str,
        lease_id: str,
        mutate: Callable[[TaskRecord, float], None],
        require_running: bool = True,
    ) -> TaskRecord:
        _validate_owner(account_id, user_id)
        _validate_task_id(task_id)
        if not lease_id:
            raise InvalidArgumentError("lease_id is required")

        now = time.time()
        async with self._locked_task(account_id, user_id, task_id):
            task = await self._read_task(account_id, user_id, task_id)
            if task is None or not _is_open_compile_task(task):
                raise NotFoundError(task_id, "task")
            self._check_lease(task, lease_id, now)
            if require_running and task.status != TaskStatus.RUNNING:
                raise FailedPreconditionError("Task is not running")

            updated = deepcopy(task)
            mutate(updated, now)
            updated.updated_at = _next_updated_at(task, now=now)
            await self._store.update(updated)
            return updated

    async def _mark_enqueue_failed(self, task: TaskRecord, error: Exception) -> None:
        async with self._locked_task(task.account_id or "", task.user_id or "", task.task_id):
            latest = await self._read_task(task.account_id or "", task.user_id or "", task.task_id)
            if latest is None:
                return
            updated = deepcopy(latest)
            updated.status = TaskStatus.FAILED
            updated.stage = TaskStatus.FAILED.value
            updated.error = str(error)
            updated.meta["error"] = {
                "code": "QUEUE_ENQUEUE_FAILED",
                "message": str(error),
            }
            updated.updated_at = _next_updated_at(latest)
            await self._store.update(updated)

    async def _read_task(
        self,
        account_id: str,
        user_id: str,
        task_id: str,
    ) -> Optional[TaskRecord]:
        payload = await self._store.get(task_id, account_id=account_id, user_id=user_id)
        if payload is None:
            return None
        data = dict(payload)
        data["status"] = TaskStatus(data["status"])
        return TaskRecord(**data)

    async def _ack_malformed_message(self, message: Any) -> None:
        msg_id = _queue_message_id(message)
        if msg_id:
            await self._queue.ack(msg_id, message)

    @staticmethod
    def _check_lease(task: TaskRecord, lease_id: str, now: float) -> None:
        if _task_auth(task).get("lease_id") != lease_id:
            raise ConflictError("Task lease does not match", resource=task.task_id)
        if _lease_expired(task, now):
            raise ConflictError("Task lease has expired", resource=task.task_id)

    @asynccontextmanager
    async def _locked_task(
        self,
        account_id: str,
        user_id: str,
        task_id: str,
    ) -> AsyncIterator[None]:
        try:
            lease = await self._agfs.pathlock_acquire_exact(
                task_record_path(account_id, user_id, task_id),
                timeout_secs=10.0,
            )
        except AGFSNotFoundError:
            yield
            return
        try:
            yield
        finally:
            await self._agfs.pathlock_release(lease)


def open_task_to_dict(task: TaskRecord, *, include_lease_id: bool = False) -> dict[str, Any]:
    meta = task.meta
    data = task.to_dict()
    data.pop("meta", None)
    data["account_id"] = task.account_id
    data["user_id"] = task.user_id
    data["created_by_user_id"] = task.user_id
    data["payload"] = deepcopy(meta.get("payload") or {})
    data["progress"] = meta.get("progress")
    data["message"] = meta.get("message")
    data["details"] = deepcopy(meta.get("details") or {})
    data["attempt"] = int(meta.get("attempt") or 0)
    data["lease_expires_at"] = meta.get("lease_expires_at")
    data["claimed_by_account_id"] = meta.get("claimed_by_account_id")
    data["claimed_by_user_id"] = meta.get("claimed_by_user_id")
    data["acknowledged_at"] = meta.get("acknowledged_at")
    data["ack_by_account_id"] = meta.get("ack_by_account_id")
    data["ack_by"] = meta.get("ack_by")
    if meta.get("error") is not None:
        data["error"] = deepcopy(meta["error"])
    if include_lease_id:
        data["lease_id"] = _task_auth(task).get("lease_id")
    return data


def _queue_delivery_payload(task: TaskRecord) -> dict[str, Any]:
    return {
        "task_id": task.task_id,
        "task_type": task.task_type,
        "account_id": task.account_id,
        "user_id": task.user_id,
        "created_at": task.created_at,
    }


def _parse_queue_message(message: Any) -> Optional[tuple[str, dict[str, Any]]]:
    msg_id = _queue_message_id(message)
    payload = _queue_payload(message)
    if not msg_id or not isinstance(payload, dict):
        return None
    return msg_id, payload


def _queue_message_id(message: Any) -> str:
    if not isinstance(message, dict):
        return ""
    msg_id = message.get("id")
    return str(msg_id) if msg_id else ""


def _queue_payload(message: Any) -> Optional[dict[str, Any]]:
    if not isinstance(message, dict):
        return None
    payload: Any = message.get("data", message)
    if isinstance(payload, bytes):
        payload = payload.decode("utf-8")
    if isinstance(payload, str):
        try:
            payload = json.loads(payload)
        except (TypeError, ValueError):
            return None
    return payload if isinstance(payload, dict) else None


def _queue_owner(payload: dict[str, Any]) -> Optional[tuple[str, str]]:
    account_id = payload.get("account_id")
    user_id = payload.get("user_id")
    if not isinstance(account_id, str) or not isinstance(user_id, str):
        return None
    try:
        _validate_owner(account_id, user_id)
    except InvalidArgumentError:
        return None
    return account_id, user_id


def _task_auth(task: TaskRecord) -> dict[str, Any]:
    auth = task.auth.get(OPEN_TASK_AUTH_KEY)
    return auth if isinstance(auth, dict) else {}


def _is_open_compile_task(task: TaskRecord) -> bool:
    return (
        task.task_type == _COMPILE_TASK_TYPE
        and task.meta.get(OPEN_TASK_META_KEY) == OPEN_COMPILE_QUEUE
    )


def _lease_expired(task: TaskRecord, now: float) -> bool:
    expires_at = task.meta.get("lease_expires_at")
    return expires_at is not None and float(expires_at) <= now


def _next_updated_at(task: TaskRecord, *, now: Optional[float] = None) -> float:
    current = time.time() if now is None else now
    return max(current, math.nextafter(task.updated_at, math.inf))


def _validate_account(account_id: str) -> None:
    error = validate_account_id(account_id)
    if error:
        raise InvalidArgumentError(error)


def _validate_owner(account_id: str, user_id: str) -> None:
    _validate_account(account_id)
    error = validate_user_id(user_id)
    if error:
        raise InvalidArgumentError(error)


def _validate_task_id(task_id: str) -> None:
    if not isinstance(task_id, str) or not task_id or "/" in task_id or "\\" in task_id:
        raise InvalidArgumentError("Invalid task id")
