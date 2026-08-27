# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0
"""Map queue lifecycle events to task and work state.

This module owns the reserved task fields carried by queue messages and the
``QueueMiddleware`` implementation that translates transport operations into
``TaskTracker`` operations. QueueFS remains unaware of task semantics.
"""

from __future__ import annotations

import asyncio
import json
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any, Awaitable, Callable, Dict, Mapping, Optional, TypeVar
from uuid import uuid4

from openviking.service.task_context import bind_task_context, get_task_context
from openviking.service.task_domain import TaskWorkRejected, WorkState
from openviking.storage.queuefs.queue_hook import (
    AckContext,
    AckNext,
    DiscardReason,
    EnqueueContext,
    EnqueueKind,
    EnqueueNext,
    ProcessContext,
    ProcessNext,
    ProcessOutcome,
    ProcessResult,
    QueueEnqueueRejected,
    QueueMiddleware,
)
from openviking.utils.async_utils import run_to_completion

if TYPE_CHECKING:
    from openviking.service.task_tracker import TaskTracker
    from openviking.storage.queuefs.queue_manager import QueueManager

TASK_WORK_ID_FIELD = "_task_work_id"
TASK_ACCOUNT_ID_FIELD = "_task_account_id"
TASK_USER_ID_FIELD = "_task_user_id"

_ResultT = TypeVar("_ResultT")


@dataclass(frozen=True)
class QueueTaskMetadata:
    task_id: str
    work_id: str
    account_id: str = ""
    user_id: str = ""


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


def _owner_from_payload(payload: Mapping[str, object]) -> tuple[str, str]:
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
    data: Mapping[str, Any],
    *,
    force_new_work_id: bool = False,
) -> tuple[Dict[str, Any], Optional[QueueTaskMetadata]]:
    """Copy a payload and attach work identity when it belongs to a task."""
    payload = dict(data)
    current = get_task_context()
    task_id = payload.get("task_id") or (current.task_id if current is not None else "")
    if not task_id:
        return payload, None

    task_id = str(task_id)
    account_id = str(payload.get(TASK_ACCOUNT_ID_FIELD) or "")
    user_id = str(payload.get(TASK_USER_ID_FIELD) or "")
    if current is not None and current.task_id == task_id:
        # The execution context identifies the task owner. Payload account
        # fields may identify a resource owner instead.
        account_id = current.account_id
        user_id = current.user_id
    elif not account_id or not user_id:
        legacy_account_id, legacy_user_id = _owner_from_payload(payload)
        account_id = account_id or legacy_account_id
        user_id = user_id or legacy_user_id

    work_id = str(uuid4() if force_new_work_id else payload.get(TASK_WORK_ID_FIELD) or uuid4())
    payload["task_id"] = task_id
    payload[TASK_WORK_ID_FIELD] = work_id
    if account_id and user_id:
        payload[TASK_ACCOUNT_ID_FIELD] = account_id
        payload[TASK_USER_ID_FIELD] = user_id
    return payload, QueueTaskMetadata(task_id, work_id, account_id, user_id)


def _apply_task_payload(target: Any, stamped: Mapping[str, Any]) -> None:
    if isinstance(target, dict):
        target.update(stamped)
        return
    for key in ("task_id", TASK_WORK_ID_FIELD, TASK_ACCOUNT_ID_FIELD, TASK_USER_ID_FIELD):
        if key in stamped:
            setattr(target, key, stamped[key])


async def _register_enqueue_work(
    payload: Any,
    queue_name: str,
) -> Optional[QueueTaskMetadata]:
    payload_dict: Optional[Dict[str, Any]]
    if isinstance(payload, dict):
        payload_dict = payload
    elif hasattr(payload, "to_dict"):
        payload_dict = payload.to_dict()
    else:
        payload_dict = None
    if payload_dict is None:
        return None

    stamped, metadata = prepare_task_payload(payload_dict, force_new_work_id=False)
    if metadata is None:
        return None
    if not metadata.account_id or not metadata.user_id:
        raise TaskWorkRejected(f"Task {metadata.task_id} work is missing owner metadata")

    from openviking.service.task_tracker import get_task_tracker

    try:
        tracker = get_task_tracker()
    except RuntimeError:
        return None

    ok = await tracker.register_work(
        metadata.task_id,
        metadata.work_id,
        queue_name,
        account_id=metadata.account_id,
        user_id=metadata.user_id,
    )
    if not ok:
        raise TaskWorkRejected(
            f"Task {metadata.task_id} is unavailable; rejected work for {queue_name}"
        )
    _apply_task_payload(payload, stamped)
    return metadata


async def _mark_enqueue_failed(metadata: Optional[QueueTaskMetadata], message: str) -> None:
    if metadata is None:
        return
    from openviking.service.task_tracker import get_task_tracker

    await get_task_tracker().mark_work_failed(
        metadata.task_id,
        metadata.work_id,
        message,
        account_id=metadata.account_id,
        user_id=metadata.user_id,
    )


async def _discard_enqueue_work(metadata: Optional[QueueTaskMetadata]) -> None:
    if metadata is None:
        return
    from openviking.service.task_tracker import get_task_tracker

    await get_task_tracker().discard_work(
        metadata.task_id,
        metadata.work_id,
        account_id=metadata.account_id,
        user_id=metadata.user_id,
    )


async def enqueue_with_task_work(
    payload: Any,
    queue_name: str,
    enqueue: Callable[[Any], Awaitable[_ResultT]],
    *,
    false_failure_message: Optional[str] = None,
    exception_message_prefix: Optional[str] = None,
) -> _ResultT:
    """Pre-register task work so enqueue failures remain visible to waiters."""
    metadata = await _register_enqueue_work(payload, queue_name)
    try:
        result = await enqueue(payload)
    except Exception as exc:
        message = f"{exception_message_prefix}: {exc}" if exception_message_prefix else str(exc)
        await _mark_enqueue_failed(metadata, message)
        raise
    if result == "deduplicated":
        await _discard_enqueue_work(metadata)
    elif false_failure_message is not None and not result:
        await _mark_enqueue_failed(metadata, false_failure_message)
    return result


def extract_task_metadata(message: Any) -> Optional[QueueTaskMetadata]:
    """Read task metadata from a QueueFS envelope or its inner payload."""
    payload = _payload_dict(message)
    if payload is None:
        return None
    task_id = payload.get("task_id")
    work_id = payload.get(TASK_WORK_ID_FIELD)
    if not work_id and isinstance(message, dict) and "data" in message and message.get("id"):
        # Older messages have no work ID; their stable envelope ID is sufficient
        # to restore the same work on every startup.
        work_id = f"queuefs:{message['id']}"
    if not task_id or not work_id:
        return None
    account_id = str(payload.get(TASK_ACCOUNT_ID_FIELD) or "")
    user_id = str(payload.get(TASK_USER_ID_FIELD) or "")
    if not account_id or not user_id:
        legacy_account_id, legacy_user_id = _owner_from_payload(payload)
        account_id = account_id or legacy_account_id
        user_id = user_id or legacy_user_id
    return QueueTaskMetadata(str(task_id), str(work_id), account_id, user_id)


class TaskWorkQueueMiddleware(QueueMiddleware):
    """Bridge queue operations to durable TaskTracker work state."""

    def __init__(self, tracker: "TaskTracker") -> None:
        self._tracker = tracker

    async def enqueue(self, ctx: EnqueueContext, call_next: EnqueueNext) -> str:
        if not isinstance(ctx.payload, dict):
            return await call_next(ctx)

        stamped, metadata = prepare_task_payload(
            ctx.payload,
            # Retry replacements are new physical work. New enqueue calls may
            # carry an owner-pre-registered work id that must be preserved.
            force_new_work_id=ctx.kind is EnqueueKind.RETRY,
        )
        ctx.payload = stamped
        if metadata is None:
            return await call_next(ctx)
        if not metadata.account_id or not metadata.user_id:
            raise TaskWorkRejected(f"Task {metadata.task_id} work is missing owner metadata")

        ok = await self._tracker.register_work(
            metadata.task_id,
            metadata.work_id,
            ctx.queue,
            account_id=metadata.account_id,
            user_id=metadata.user_id,
        )
        if not ok:
            raise TaskWorkRejected(
                f"Task {metadata.task_id} is unavailable; rejected work for {ctx.queue}"
            )

        try:
            return await call_next(ctx)
        except (asyncio.CancelledError, QueueEnqueueRejected):
            if not ctx.committed:
                await run_to_completion(
                    lambda: self._tracker.discard_work(
                        metadata.task_id,
                        metadata.work_id,
                        account_id=metadata.account_id,
                        user_id=metadata.user_id,
                    )
                )
            raise
        except BaseException as exc:
            # A physical enqueue failure means no consumer will ever settle this
            # Work. Persist it as failed so request/task waiters see the error.
            if not ctx.committed:
                error_message = str(exc)
                await run_to_completion(
                    lambda: self._tracker.mark_work_failed(
                        metadata.task_id,
                        metadata.work_id,
                        error_message,
                        account_id=metadata.account_id,
                        user_id=metadata.user_id,
                    )
                )
            raise

    async def process(self, ctx: ProcessContext, call_next: ProcessNext) -> ProcessResult:
        metadata = extract_task_metadata(ctx.message)
        if metadata is None:
            return await call_next(ctx)
        if not metadata.account_id or not metadata.user_id:
            raise TaskWorkRejected(f"Task {metadata.task_id} work is missing owner metadata")

        state = await self._tracker.get_work_state(
            metadata.task_id,
            metadata.work_id,
            account_id=metadata.account_id,
            user_id=metadata.user_id,
        )
        if state is None:
            task = await self._tracker.get(
                metadata.task_id,
                account_id=metadata.account_id,
                user_id=metadata.user_id,
            )
            if task is not None and task.is_terminal():
                return ProcessResult.duplicate()
            await self._tracker.restore_work(
                metadata.task_id,
                metadata.work_id,
                ctx.queue,
                account_id=metadata.account_id,
                user_id=metadata.user_id,
            )
        elif state in {WorkState.REQUEUED, WorkState.DONE, WorkState.FAILED}:
            # Complete any finalization interrupted after the Work commit but
            # before the previous delivery reached ACK.
            await self._tracker.settle_work(
                metadata.task_id,
                metadata.work_id,
                account_id=metadata.account_id,
                user_id=metadata.user_id,
            )
            return ProcessResult.duplicate()

        if self._is_cancelling(metadata):
            result = await ctx.discard(
                DiscardReason.USER_CANCELLED,
                handler_started=False,
            )
            await self._persist_result(metadata, result)
            return result

        await self._tracker.start_work(
            metadata.task_id,
            metadata.work_id,
            account_id=metadata.account_id,
            user_id=metadata.user_id,
        )
        if self._is_cancelling(metadata):
            result = await ctx.discard(
                DiscardReason.USER_CANCELLED,
                handler_started=False,
            )
            await self._persist_result(metadata, result)
            return result

        self._tracker.register_active(
            metadata.task_id,
            account_id=metadata.account_id,
            user_id=metadata.user_id,
        )
        active = True

        async def release_active() -> None:
            nonlocal active
            if not active:
                return
            active = False
            await self._tracker.unregister_active(
                metadata.task_id,
                account_id=metadata.account_id,
                user_id=metadata.user_id,
            )

        try:
            with bind_task_context(metadata.task_id, metadata.account_id, metadata.user_id):
                try:
                    result = await call_next(ctx)
                except asyncio.CancelledError:
                    if not self._is_cancelling(metadata):
                        raise
                    task = asyncio.current_task()
                    if task is None or task.uncancel() > 0:
                        raise
                    # Prevent the cancellation poller from repeatedly cancelling
                    # cleanup and durable result persistence.
                    await release_active()
                    result = await ctx.discard(
                        DiscardReason.USER_CANCELLED,
                        handler_started=True,
                    )
            await self._persist_result(metadata, result)
            return result
        finally:
            await release_active()

    async def ack(self, ctx: AckContext, call_next: AckNext) -> None:
        """ACK is transport-only; terminal Work state is never rolled back."""
        await call_next(ctx)

    def _is_cancelling(self, metadata: QueueTaskMetadata) -> bool:
        return self._tracker.is_cancelling(
            metadata.task_id,
            account_id=metadata.account_id,
            user_id=metadata.user_id,
        )

    async def _persist_result(
        self,
        metadata: QueueTaskMetadata,
        result: ProcessResult,
    ) -> None:
        if result.outcome is ProcessOutcome.DUPLICATE:
            return
        if result.outcome is ProcessOutcome.FAILED:
            await self._tracker.mark_work_failed(
                metadata.task_id,
                metadata.work_id,
                result.error or "Queue handler failed",
                account_id=metadata.account_id,
                user_id=metadata.user_id,
            )
            return
        if result.outcome is ProcessOutcome.REQUEUE:
            await self._tracker.mark_work_requeued(
                metadata.task_id,
                metadata.work_id,
                delta=1,
                account_id=metadata.account_id,
                user_id=metadata.user_id,
            )
            return
        await self._tracker.mark_work_done(
            metadata.task_id,
            metadata.work_id,
            account_id=metadata.account_id,
            user_id=metadata.user_id,
        )


async def install_task_work_tracking(queue_manager: "QueueManager", tracker: "TaskTracker") -> None:
    """Restore queued work, then install task lifecycle tracking.

    Call this before queue workers start so recovered messages are represented
    in task state before they can be processed.
    """
    snapshots = await queue_manager.snapshot_all()
    for queue_name, messages in snapshots.items():
        for message in messages:
            metadata = extract_task_metadata(message)
            if metadata is None or not metadata.account_id or not metadata.user_id:
                continue
            await tracker.restore_work(
                metadata.task_id,
                metadata.work_id,
                queue_name,
                account_id=metadata.account_id,
                user_id=metadata.user_id,
            )
    queue_manager.register_middleware(TaskWorkQueueMiddleware(tracker))


__all__ = [
    "TASK_ACCOUNT_ID_FIELD",
    "TASK_USER_ID_FIELD",
    "TASK_WORK_ID_FIELD",
    "QueueTaskMetadata",
    "TaskWorkQueueMiddleware",
    "enqueue_with_task_work",
    "extract_task_metadata",
    "install_task_work_tracking",
    "prepare_task_payload",
]
