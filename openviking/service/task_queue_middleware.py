# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0
"""Adapt the existing in-memory TaskWorkIndex to queue lifecycle middleware."""

import asyncio

from openviking.service.task_work_index import (
    TaskWorkIndex,
    TaskWorkRejected,
    bind_task_context,
    extract_task_metadata,
    prepare_task_payload,
    record_enqueued_work,
)
from openviking.storage.queuefs.process_result import ProcessOutcome, ProcessResult
from openviking.storage.queuefs.queue_middleware import (
    AckContext,
    AckNext,
    ClearContext,
    ClearNext,
    EnqueueContext,
    EnqueueNext,
    ProcessContext,
    ProcessNext,
    QueueMiddleware,
)


class TaskWorkQueueMiddleware(QueueMiddleware):
    """Keep QueueFS as the durable source of truth; no new task state or store."""

    def __init__(self, index: TaskWorkIndex) -> None:
        self._index = index

    async def enqueue(self, ctx: EnqueueContext, call_next: EnqueueNext) -> str:
        if not isinstance(ctx.payload, dict):
            return await call_next(ctx)
        ctx.payload, metadata = prepare_task_payload(ctx.payload)
        ctx.work_id = metadata.work_id if metadata is not None else None
        if not self._index.register(ctx.queue, metadata):
            raise TaskWorkRejected(
                f"Task {metadata.task_id} is cancelling; rejected work for {ctx.queue}"
            )
        try:
            result = await call_next(ctx)
        except asyncio.CancelledError:
            if metadata is not None:
                if ctx.committed:
                    record_enqueued_work(ctx.queue, metadata.work_id)
                else:
                    await self._index.discard(ctx.queue, metadata)
            raise
        except BaseException as exc:
            if metadata is not None:
                if ctx.committed:
                    record_enqueued_work(ctx.queue, metadata.work_id)
                else:
                    await self._index.fail_enqueue(
                        ctx.queue,
                        metadata,
                        str(exc),
                    )
                    record_enqueued_work(ctx.queue, metadata.work_id)
            raise
        if metadata is not None:
            if ctx.committed:
                record_enqueued_work(ctx.queue, metadata.work_id)
            else:
                await self._index.discard(ctx.queue, metadata)
        return result

    async def process(self, ctx: ProcessContext, call_next: ProcessNext) -> ProcessResult:
        metadata = extract_task_metadata(ctx.message)
        if metadata is None:
            return await call_next(ctx)

        active_task = asyncio.current_task()
        enqueued_work = []
        with bind_task_context(
            metadata.task_id,
            metadata.account_id,
            metadata.user_id,
            enqueued_work=enqueued_work,
            work_id=metadata.work_id,
        ):
            if self._index.cancellation_requested(metadata.task_id):
                result = await ctx.cancel()
                self._record_result(metadata, result)
                return result
            if active_task is not None:
                self._index.register_active(
                    metadata.task_id,
                    active_task,
                    work_id=metadata.work_id,
                )
            try:
                result = await call_next(ctx)
            except asyncio.CancelledError:
                if not self._index.cancellation_requested(metadata.task_id):
                    raise
                # Handler unwinding finished; settle user cancellation before ACK.
                result = ProcessResult.cancelled()
            except Exception as exc:
                self._index.record_failure(metadata.task_id, str(exc))
                raise
            finally:
                if active_task is not None:
                    self._index.unregister_active(metadata.task_id, active_task)
            self._record_result(metadata, result)
            return result

    def _record_result(self, metadata, result: ProcessResult) -> None:
        outcome = {
            ProcessOutcome.SUCCESS: "completed",
            ProcessOutcome.FAILED: "failed",
            ProcessOutcome.REQUEUED: "requeued",
            ProcessOutcome.CANCELLED: "cancelled",
        }[result.outcome]
        self._index.record_result(
            metadata,
            outcome,
            error=result.error,
            errors=result.errors,
        )

    async def ack(self, ctx: AckContext, call_next: AckNext) -> None:
        prepared = None
        try:
            if ctx.message is not None:
                prepared = await self._index.prepare_ack(ctx.queue, ctx.message)
            await call_next(ctx)
        finally:
            if prepared is not None and not ctx.committed:
                self._index.rollback_ack(ctx.queue, prepared)

    async def clear(self, ctx: ClearContext, call_next: ClearNext) -> None:
        prepared = []
        try:
            for message in ctx.messages:
                metadata = await self._index.prepare_ack(ctx.queue, message)
                if metadata is not None:
                    prepared.append(metadata)
            await call_next(ctx)
        finally:
            if not ctx.committed:
                for metadata in prepared:
                    self._index.rollback_ack(ctx.queue, metadata)
