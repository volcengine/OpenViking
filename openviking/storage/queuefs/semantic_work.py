# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0
"""Per-message semantic lifecycle, including Skill update cancellation and locks."""

from __future__ import annotations

import asyncio
from typing import TYPE_CHECKING, Any, Awaitable, Callable, TypeVar

from openviking.server.identity import RequestContext
from openviking.service.task_tracker_concurrency import run_to_completion
from openviking.storage.queuefs.process_result import ProcessResult
from openviking.storage.queuefs.semantic_executor import SemanticTreeStats
from openviking.storage.queuefs.semantic_lock import SemanticLockScope
from openviking.storage.queuefs.semantic_msg import SemanticMsg
from openviking.storage.viking_fs import get_viking_fs
from openviking.telemetry import (
    OperationTelemetry,
    register_telemetry,
    resolve_telemetry,
    unregister_telemetry,
)
from openviking.telemetry.request_wait_tracker import get_request_wait_tracker

if TYPE_CHECKING:
    from openviking.storage.queuefs.semantic_processor import SemanticProcessor

_Result = TypeVar("_Result")


class SemanticMessageWork:
    """Original Resource/Memory lifecycle; each dequeued message owns its scope."""

    def __init__(
        self,
        processor: SemanticProcessor,
        msg: SemanticMsg,
        caller_lock: dict[str, Any] | None,
    ) -> None:
        self.processor = processor
        self.msg = msg
        self.caller_lock = caller_lock
        self.scope: SemanticLockScope | None = None

    def start(self) -> None:
        pass

    def resolve_telemetry(self) -> OperationTelemetry | None:
        return resolve_telemetry(self.msg.telemetry_id)

    async def acquire_lock(self, ctx: RequestContext) -> bool:
        self.scope = await SemanticLockScope.resolve(
            self.msg.lock_handoff,
            caller_lock=self.caller_lock,
            fallback_path_factory=lambda: get_viking_fs()._uri_to_path(self.msg.uri, ctx=ctx),
        )
        return True

    async def run_write(self, factory: Callable[[], Awaitable[_Result]]) -> _Result:
        return await factory()

    async def finish_processing(self, succeeded: bool) -> None:
        assert self.scope is not None
        await self.scope.close()

    def failure_result(self, stats: SemanticTreeStats | None) -> ProcessResult | None:
        return None

    async def reenqueue(self) -> None:
        await self.processor._reenqueue_semantic_msg(self.msg)

    async def reenqueue_after_error(self) -> None:
        await self.reenqueue()

    async def skip(self) -> None:
        pass

    async def cancel(self) -> None:
        pass

    async def cancel_queued(self) -> None:
        if self.msg.telemetry_id and self.msg.id:
            get_request_wait_tracker().mark_semantic_done(self.msg.telemetry_id, self.msg.id)
        await self.processor._release_cancelled_semantic_lock(self.msg)

    async def close(self) -> None:
        pass


class SkillSemanticMessageWork(SemanticMessageWork):
    """Keep Skill tracking and package ownership alive until writes settle.

    A failed run retains its lease for retry; cancellation drains started work
    before update rollback can proceed. None of this state is shared by messages.
    """

    def __init__(
        self,
        processor: SemanticProcessor,
        msg: SemanticMsg,
        caller_lock: dict[str, Any] | None,
    ) -> None:
        super().__init__(processor, msg, caller_lock)
        self._internal_tracking = False
        self._lock_started = False
        self._lock_closed = False

    def start(self) -> None:
        msg = self.msg
        if not msg.telemetry_id:
            msg.telemetry_id = OperationTelemetry(
                operation="skill_index", enabled=False
            ).telemetry_id
        tracker = get_request_wait_tracker()
        self._internal_tracking = not tracker.has_request(msg.telemetry_id)
        tracker.retain_request(msg.telemetry_id, msg.id)
        tracker.register_semantic_root(msg.telemetry_id, msg.id)
        if self._internal_tracking:
            tracker.cleanup(msg.telemetry_id)

    def resolve_telemetry(self) -> OperationTelemetry:
        collector = super().resolve_telemetry()
        if collector is None:
            collector = OperationTelemetry(operation="skill_index", enabled=False)
            collector.telemetry_id = self.msg.telemetry_id
            register_telemetry(collector)
        return collector

    async def acquire_lock(self, ctx: RequestContext) -> bool:
        self._lock_started = True
        self.scope = await self.processor._resolve_skill_semantic_lock(
            self.msg, ctx, self.caller_lock
        )
        return self.scope.lock is not None

    async def run_write(self, factory: Callable[[], Awaitable[_Result]]) -> _Result:
        return await run_to_completion(factory)

    async def finish_processing(self, succeeded: bool) -> None:
        async def finish():
            if not self.msg.skip_vectorization:
                await get_request_wait_tracker().wait_for_embeddings(
                    self.msg.telemetry_id,
                    stop_waiting=self.processor._embedding_worker_stopped,
                )
            if succeeded:
                assert self.scope is not None
                await self.scope.close()
                self._lock_closed = True

        # On failure retain the lease until reenqueue hands it to the retry.
        await run_to_completion(finish)

    def failure_result(self, stats: SemanticTreeStats | None) -> ProcessResult | None:
        if stats is None or not stats.failures:
            return None
        for failure in stats.failures:
            get_request_wait_tracker().mark_semantic_failed(
                self.msg.telemetry_id, self.msg.id, failure
            )
        # Report every file error, but settle this queue item only once.
        self.processor._merge_request_stats(self.msg.telemetry_id, error_count=len(stats.failures))
        return ProcessResult.failed("\n".join(stats.failures))

    async def reenqueue(self) -> None:
        async def enqueue(queue, msg):
            if self.scope is not None and self.scope.lock is not None and self.scope._owned:
                await run_to_completion(
                    lambda: self.processor._enqueue_skill_retry(queue, msg, self.scope)
                )
            else:
                await run_to_completion(lambda: queue.enqueue(msg))

        # The shared circuit-breaker delay remains interruptible. Only the
        # actual lock handoff and enqueue operation must finish once started.
        await self.processor._reenqueue_semantic_msg(self.msg, enqueue=enqueue)

    async def reenqueue_after_error(self) -> None:
        try:
            await self.reenqueue()
        except asyncio.CancelledError:
            await run_to_completion(
                lambda: self.processor._release_cancelled_semantic_lock(self.msg)
            )
            get_request_wait_tracker().mark_semantic_done(self.msg.telemetry_id, self.msg.id)
            raise

    async def skip(self) -> None:
        if self.msg.lock_handoff is not None:
            await run_to_completion(
                lambda: self.processor._release_cancelled_semantic_lock(self.msg)
            )

    async def cancel(self) -> None:
        if not self._lock_started:
            # Cancellation during the circuit-breaker delay precedes adoption.
            await self.skip()
        get_request_wait_tracker().mark_semantic_done(self.msg.telemetry_id, self.msg.id)

    async def close(self) -> None:
        if self.scope is not None and not self._lock_closed:
            await run_to_completion(self.scope.close)
        if self._internal_tracking and get_request_wait_tracker().is_complete(
            self.msg.telemetry_id
        ):
            unregister_telemetry(self.msg.telemetry_id)

    async def cancel_queued(self) -> None:
        # Skill rollback must observe release before this item is settled.
        await self.processor._release_cancelled_semantic_lock(self.msg)
        if self.msg.telemetry_id and self.msg.id:
            get_request_wait_tracker().mark_semantic_done(self.msg.telemetry_id, self.msg.id)
