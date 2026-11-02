# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0
"""Bridge QueueFS lifecycle events to Store-authoritative Task Work."""

import asyncio
from typing import TYPE_CHECKING

from openviking.service.task_domain import TERMINAL_TASK_STATUSES
from openviking.service.task_store import WorkRegistration
from openviking.service.task_tracker_concurrency import KeyedAsyncLockPool, run_to_completion
from openviking.service.task_work_index import (
    TaskWorkRejected,
    bind_task_context,
    extract_task_metadata,
    prepare_task_payload,
    record_enqueued_work,
)
from openviking.storage.queuefs.process_result import ProcessOutcome, ProcessResult
from openviking.storage.queuefs.queue_middleware import (
    ClearContext,
    ClearNext,
    EnqueueContext,
    EnqueueNext,
    ProcessContext,
    ProcessNext,
    QueueMiddleware,
)

if TYPE_CHECKING:
    from openviking.service.task_tracker import TaskTracker
    from openviking.service.task_work_index import QueueTaskMetadata


class TaskWorkQueueMiddleware(QueueMiddleware):
    """Map tracked queue deliveries to semantic TaskTracker operations."""

    def __init__(self) -> None:
        self._enqueue_locks = KeyedAsyncLockPool()

    @property
    def _tracker(self) -> "TaskTracker":
        from openviking.service.task_tracker import get_task_tracker

        return get_task_tracker()

    async def enqueue(self, ctx: EnqueueContext, call_next: EnqueueNext) -> str:
        if not isinstance(ctx.payload, dict):
            return await call_next(ctx)
        ctx.payload, metadata = prepare_task_payload(ctx.payload)
        ctx.work_id = metadata.work_id if metadata is not None else None
        if metadata is None:
            return await call_next(ctx)
        if not metadata.account_id or not metadata.user_id:
            raise TaskWorkRejected(f"Task {metadata.task_id} work is missing owner metadata")

        key = (metadata.account_id, metadata.user_id, metadata.task_id, metadata.work_id)
        async with self._enqueue_locks.acquire(key):
            return await self._enqueue_registered(ctx, call_next, metadata)

    async def _enqueue_registered(
        self,
        ctx: EnqueueContext,
        call_next: EnqueueNext,
        metadata: "QueueTaskMetadata",
    ) -> str:
        registration = WorkRegistration.REJECTED

        async def register() -> None:
            nonlocal registration
            registration = await self._tracker.register_work(
                metadata.task_id,
                metadata.work_id,
                ctx.queue,
                account_id=metadata.account_id,
                user_id=metadata.user_id,
            )

        await run_to_completion(register)
        if registration is WorkRegistration.REJECTED:
            raise TaskWorkRejected(
                f"Task {metadata.task_id} is unavailable; rejected work for {ctx.queue}"
            )
        try:
            message_id = await call_next(ctx)
        except asyncio.CancelledError:
            if ctx.committed:
                record_enqueued_work(ctx.queue, metadata.work_id)
            elif registration is WorkRegistration.CREATED:
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
            if ctx.committed:
                record_enqueued_work(ctx.queue, metadata.work_id)
            elif registration is WorkRegistration.CREATED:
                failure_message = str(exc) or type(exc).__name__
                await run_to_completion(
                    lambda: self._tracker.mark_work_failed(
                        metadata.task_id,
                        metadata.work_id,
                        failure_message,
                        account_id=metadata.account_id,
                        user_id=metadata.user_id,
                    )
                )
                record_enqueued_work(ctx.queue, metadata.work_id)
            # An existing work can still have an unacknowledged delivery. A
            # failed replacement enqueue must preserve its state so recovery
            # can execute the original rather than ACK it as terminal.
            raise

        if ctx.committed:
            record_enqueued_work(ctx.queue, metadata.work_id)
        elif registration is WorkRegistration.CREATED:
            await run_to_completion(
                lambda: self._tracker.discard_work(
                    metadata.task_id,
                    metadata.work_id,
                    account_id=metadata.account_id,
                    user_id=metadata.user_id,
                )
            )
        return message_id

    async def process(self, ctx: ProcessContext, call_next: ProcessNext) -> ProcessResult:
        metadata = extract_task_metadata(ctx.message)
        if metadata is None:
            return await call_next(ctx)
        if not metadata.account_id or not metadata.user_id:
            raise TaskWorkRejected(f"Task {metadata.task_id} work is missing owner metadata")

        duplicate = await self._reconcile_delivery(metadata, ctx.queue)
        if duplicate is not None:
            return duplicate

        if await self._tracker.is_cancelling(
            metadata.task_id,
            account_id=metadata.account_id,
            user_id=metadata.user_id,
        ):
            result = await ctx.cancel()
            await self._persist_result(metadata, result)
            return result

        await self._tracker.start_work(
            metadata.task_id,
            metadata.work_id,
            account_id=metadata.account_id,
            user_id=metadata.user_id,
        )
        await self._tracker.register_running_task(
            metadata.task_id,
            work_id=metadata.work_id,
            account_id=metadata.account_id,
            user_id=metadata.user_id,
        )
        enqueued_work: list[tuple[str, str]] = []
        try:
            with bind_task_context(
                metadata.task_id,
                metadata.account_id,
                metadata.user_id,
                enqueued_work=enqueued_work,
                work_id=metadata.work_id,
            ):
                try:
                    result = await call_next(ctx)
                except asyncio.CancelledError:
                    if not await self._tracker.is_cancelling(
                        metadata.task_id,
                        account_id=metadata.account_id,
                        user_id=metadata.user_id,
                    ):
                        raise
                    result = ProcessResult.cancelled()
                except Exception as exc:
                    await self._tracker.record_work_error(
                        metadata.task_id,
                        metadata.work_id,
                        str(exc) or type(exc).__name__,
                        account_id=metadata.account_id,
                        user_id=metadata.user_id,
                    )
                    raise
            await self._persist_result(metadata, result)
            return result
        finally:
            await self._tracker.unregister_running_task(metadata.task_id)

    async def _reconcile_delivery(
        self,
        metadata: "QueueTaskMetadata",
        queue_name: str,
    ) -> ProcessResult | None:
        from openviking.service.task_domain import WorkState

        status = await self._tracker.get_task_status(
            metadata.task_id,
            account_id=metadata.account_id,
            user_id=metadata.user_id,
        )
        state = await self._tracker.get_work_state(
            metadata.task_id,
            metadata.work_id,
            account_id=metadata.account_id,
            user_id=metadata.user_id,
        )
        if status is None or status in TERMINAL_TASK_STATUSES:
            if state is not None:
                await self._tracker.settle_work(
                    metadata.task_id,
                    metadata.work_id,
                    account_id=metadata.account_id,
                    user_id=metadata.user_id,
                )
            return ProcessResult.success()
        if state is None:
            await self._tracker.reconcile_delivery(
                metadata.task_id,
                metadata.work_id,
                queue_name,
                account_id=metadata.account_id,
                user_id=metadata.user_id,
            )
            return None
        if state in (WorkState.DONE, WorkState.FAILED):
            await self._tracker.settle_work(
                metadata.task_id,
                metadata.work_id,
                account_id=metadata.account_id,
                user_id=metadata.user_id,
            )
            return ProcessResult.success()
        return None

    async def _persist_result(
        self,
        metadata: "QueueTaskMetadata",
        result: ProcessResult,
    ) -> None:
        if result.outcome is ProcessOutcome.FAILED:
            await self._tracker.mark_work_failed(
                metadata.task_id,
                metadata.work_id,
                result.error or "Queue handler failed",
                errors=result.errors,
                account_id=metadata.account_id,
                user_id=metadata.user_id,
            )
        elif result.outcome is ProcessOutcome.REQUEUED:
            await self._tracker.mark_work_requeued(
                metadata.task_id,
                metadata.work_id,
                delta=1,
                account_id=metadata.account_id,
                user_id=metadata.user_id,
            )
        else:
            vector_written = bool(
                isinstance(result.value, dict) and result.value.get("vector_written")
            )
            await self._tracker.mark_work_done(
                metadata.task_id,
                metadata.work_id,
                vector_written=vector_written,
                account_id=metadata.account_id,
                user_id=metadata.user_id,
            )

    async def clear(self, ctx: ClearContext, call_next: ClearNext) -> None:
        tracked = [
            metadata
            for message in ctx.messages
            if (metadata := extract_task_metadata(message)) is not None
            and metadata.account_id
            and metadata.user_id
        ]
        await call_next(ctx)
        if not ctx.committed:
            return
        for metadata in tracked:
            await run_to_completion(
                lambda metadata=metadata: self._tracker.settle_work(
                    metadata.task_id,
                    metadata.work_id,
                    account_id=metadata.account_id,
                    user_id=metadata.user_id,
                )
            )
