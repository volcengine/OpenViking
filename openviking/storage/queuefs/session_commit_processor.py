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
    # Dependency-wait backoff (issue #4345): when a commit cannot run because
    # its predecessor is still pending, re-enqueueing immediately hot-loops the
    # queue (observed >800k requeues behind ~33 waiting commits). Exponential
    # backoff with a cap bounds the churn while never dropping the message.
    DEP_WAIT_BASE_S = 0.1
    DEP_WAIT_MAX_S = 3.0
    DEP_WAIT_MAX_HOLD_S = 1.0

    def __init__(
        self,
        session_service: "SessionService",
        service_loop: asyncio.AbstractEventLoop,
    ) -> None:
        self._session_service = session_service
        self._service_loop = service_loop
        # key -> (attempt_count, next_attempt_monotonic). In-memory only:
        # a restart just resumes the backoff schedule from the base delay.
        self._dep_wait: Dict[str, tuple] = {}

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
            if processed:
                self._dep_wait.pop(self._dep_wait_key(msg), None)
            else:
                await self._requeue_with_backoff(msg)
            return processed
        finally:
            reset_root_observability_context(root_context_token)

    @classmethod
    def _dep_wait_key(cls, msg: SessionCommitMsg) -> str:
        return f"{msg.session_id}:{msg.archive_uri}"

    @classmethod
    def _dep_wait_delay_s(cls, attempt: int) -> float:
        return min(
            cls.DEP_WAIT_BASE_S * (2 ** min(attempt, 6)),
            cls.DEP_WAIT_MAX_S,
        )

    async def _requeue_with_backoff(self, msg: SessionCommitMsg) -> None:
        """Re-enqueue a dependency-waiting commit after bounded backoff.

        The predecessor commit is still pending (``resume_queued_commit``
        returned False). Holding this worker briefly and applying exponential
        backoff keeps the dequeue/ack/re-enqueue cycle bounded instead of
        spinning at full consumer speed.
        """
        import time

        from openviking.storage.queuefs import QueueManager, get_queue_manager

        key = self._dep_wait_key(msg)
        now = time.monotonic()
        attempt, next_at = self._dep_wait.get(key, (0, 0.0))
        if now < next_at:
            await asyncio.sleep(min(next_at - now, self.DEP_WAIT_MAX_HOLD_S))
        attempt += 1
        delay = self._dep_wait_delay_s(attempt)
        self._dep_wait[key] = (attempt, time.monotonic() + delay)
        await get_queue_manager().enqueue(
            QueueManager.SESSION_COMMIT,
            msg.to_dict(),
        )
        self.report_requeue()

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
