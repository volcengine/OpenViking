# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0
"""Queue consumer for restart-safe Session Phase 2 work."""

import asyncio
import concurrent.futures
import json
from typing import TYPE_CHECKING, Any, Dict, Optional

from openviking.observability.context import (
    bind_root_observability_context,
    reset_root_observability_context,
)
from openviking.server.identity import RequestContext, Role
from openviking.service.task_tracker import get_task_tracker
from openviking.service.task_work_index import bind_task_context
from openviking.storage.queuefs.named_queue import DequeueHandlerBase
from openviking.storage.queuefs.session_commit_msg import SessionCommitMsg
from openviking.telemetry.span_models import create_root_span_attributes
from openviking_cli.session.user_id import UserIdentifier

if TYPE_CHECKING:
    from openviking.service.session_service import SessionService


class SessionCommitProcessor(DequeueHandlerBase):
    # A commit whose predecessor is still running is put back on the queue. With
    # no delay that is a hot loop: the same messages are dequeued, found blocked,
    # and re-enqueued as fast as the consumer can turn, which is how a queue of
    # 33 waiters reached a requeue count in the hundreds of thousands without
    # draining. The wait is paced instead, doubling from 50ms and holding at 2s
    # so a long Phase 2 costs at most one poll every two seconds per waiter.
    _DEPENDENCY_WAIT_BASE_DELAY = 0.05
    _DEPENDENCY_WAIT_MAX_DELAY = 2.0
    _DEPENDENCY_WAIT_MAX_DOUBLING = 8

    def __init__(
        self,
        session_service: "SessionService",
        service_loop: asyncio.AbstractEventLoop,
    ) -> None:
        self._session_service = session_service
        self._service_loop = service_loop

    @staticmethod
    def _parse_message(data: Dict[str, Any]) -> tuple[SessionCommitMsg, RequestContext]:
        payload = data.get("data", data)
        if isinstance(payload, str):
            payload = json.loads(payload)
        msg = SessionCommitMsg.from_dict(payload)
        ctx = RequestContext(
            user=UserIdentifier.from_dict(msg.user),
            role=Role.USER,
        )
        return msg, ctx

    async def _process(self, msg: SessionCommitMsg, ctx: RequestContext) -> bool:
        # Bind a root observability context so Phase-2 extraction VLM/embedding
        # token events are attributed to the committing account/user rather than
        # "__unknown__" (mirrors SemanticProcessor.on_dequeue). Must bind inside
        # this coroutine: on_dequeue hops loops via run_coroutine_threadsafe, so
        # a context bound there would not propagate here.
        root_attrs = create_root_span_attributes(
            http_method="QUEUE",
            http_route="/queuefs/session_commit",
            request_id=msg.task_id,
            url_path=msg.session_uri,
        )
        root_attrs.account_id = ctx.account_id
        root_attrs.user_id = ctx.user.user_id
        root_context_token = bind_root_observability_context(root_attrs)
        try:
            session = self._session_service.session(
                ctx,
                msg.session_id,
                session_uri=msg.session_uri,
            )
            if not await session.exists():
                error = f"Session '{msg.session_id}' no longer exists"
                tracker = get_task_tracker()
                await tracker.create(
                    "session_commit",
                    resource_id=msg.session_id,
                    account_id=ctx.account_id,
                    user_id=ctx.user.user_id,
                    task_id=msg.task_id,
                )
                await tracker.fail(
                    msg.task_id,
                    error,
                    account_id=ctx.account_id,
                    user_id=ctx.user.user_id,
                )
                return True
            await session.load()
            with bind_task_context(msg.task_id, ctx.account_id, ctx.user.user_id):
                processed = await session.resume_queued_commit(msg)
            if not processed:
                from openviking.storage.queuefs import QueueManager, get_queue_manager

                await asyncio.sleep(self._dependency_wait_delay(msg.dependency_wait_count))
                payload = msg.to_dict()
                payload["dependency_wait_count"] = msg.dependency_wait_count + 1
                await get_queue_manager().enqueue(
                    QueueManager.SESSION_COMMIT,
                    payload,
                )
                self.report_requeue()
            return processed
        finally:
            reset_root_observability_context(root_context_token)

    @classmethod
    def _dependency_wait_delay(cls, wait_count: int) -> float:
        """Seconds to wait before putting a dependency-blocked commit back.

        Doubles per attempt and then holds flat, so an unusually long Phase 2
        cannot turn into an unbounded wait for the commits queued behind it.
        """
        doublings = min(max(wait_count, 0), cls._DEPENDENCY_WAIT_MAX_DOUBLING)
        return min(cls._DEPENDENCY_WAIT_BASE_DELAY * (2**doublings), cls._DEPENDENCY_WAIT_MAX_DELAY)

    async def _finalize_cancelled(self, msg: SessionCommitMsg, ctx: RequestContext) -> None:
        session = self._session_service.session(
            ctx,
            msg.session_id,
            session_uri=msg.session_uri,
        )
        if await session.exists():
            await session.finalize_cancelled_commit(msg.archive_uri)

    async def on_cancelled(self, data: Optional[Dict[str, Any]]) -> Optional[Dict[str, Any]]:
        if not data:
            return None

        try:
            msg, ctx = self._parse_message(data)
        except (json.JSONDecodeError, KeyError, TypeError, ValueError) as exc:
            self.report_error(str(exc), data)
            return None

        future = asyncio.run_coroutine_threadsafe(
            self._finalize_cancelled(msg, ctx),
            self._service_loop,
        )
        try:
            await asyncio.wrap_future(future)
            self.report_success()
        except asyncio.CancelledError:
            future.cancel()
            raise
        return None

    async def on_dequeue(self, data: Optional[Dict[str, Any]]) -> Optional[Dict[str, Any]]:
        if not data:
            return None

        try:
            msg, ctx = self._parse_message(data)
        except (json.JSONDecodeError, KeyError, TypeError, ValueError) as exc:
            self.report_error(str(exc), data)
            return None
        future: concurrent.futures.Future[bool] = asyncio.run_coroutine_threadsafe(
            self._process(msg, ctx),
            self._service_loop,
        )
        try:
            await asyncio.wrap_future(future)
            self.report_success()
        except asyncio.CancelledError:
            future.cancel()
            raise
        return None
