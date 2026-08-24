# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0
"""Run persisted session memory-extraction jobs on the service event loop.

Queue workers use separate event loops, while Session objects belong to the
service loop. The processor forwards work with ``run_coroutine_threadsafe`` and
keeps the current queue work ID so the job can wait for descendants without
waiting for itself.
"""

import asyncio
import concurrent.futures
import json
from typing import TYPE_CHECKING, Any, Dict, Optional

from openviking.observability.context import (
    bind_root_observability_context,
    reset_root_observability_context,
)
from openviking.server.identity import RequestContext, Role
from openviking.service.task_context import bind_task_context
from openviking.service.task_tracker import get_task_tracker
from openviking.service.task_work_hook import extract_task_metadata
from openviking.storage.queuefs.named_queue import DequeueHandlerBase
from openviking.storage.queuefs.queue_hook import DiscardReason, ProcessResult
from openviking.storage.queuefs.session_commit_msg import SessionCommitMsg
from openviking.telemetry.span_models import create_root_span_attributes
from openviking_cli.session.user_id import UserIdentifier

if TYPE_CHECKING:
    from openviking.service.session_service import SessionService


class SessionCommitProcessor(DequeueHandlerBase):
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

    async def _process(
        self,
        msg: SessionCommitMsg,
        ctx: RequestContext,
        *,
        current_work_id: Optional[str] = None,
    ) -> bool:
        # Bind after switching loops so extraction telemetry has the task owner.
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
                processed = await session.resume_queued_commit(msg, current_work_id=current_work_id)
            return processed
        finally:
            reset_root_observability_context(root_context_token)

    async def _finalize_cancelled(self, msg: SessionCommitMsg, ctx: RequestContext) -> None:
        session = self._session_service.session(
            ctx,
            msg.session_id,
            session_uri=msg.session_uri,
        )
        if await session.exists():
            await session.finalize_cancelled_commit(msg.archive_uri)

    async def on_discard(
        self,
        data: Optional[Dict[str, Any]],
        *,
        reason: DiscardReason,
        handler_started: bool,
    ) -> ProcessResult:
        del reason, handler_started
        if not data:
            return ProcessResult.failed("Queue message is empty")

        try:
            msg, ctx = self._parse_message(data)
        except (json.JSONDecodeError, KeyError, TypeError, ValueError) as exc:
            return ProcessResult.failed(exc)

        future = asyncio.run_coroutine_threadsafe(
            self._finalize_cancelled(msg, ctx),
            self._service_loop,
        )
        try:
            await asyncio.wrap_future(future)
        except asyncio.CancelledError:
            future.cancel()
            raise
        return ProcessResult.cancelled()

    async def on_dequeue(self, data: Optional[Dict[str, Any]]) -> ProcessResult:
        if not data:
            return ProcessResult.failed("Queue message is empty")

        try:
            msg, ctx = self._parse_message(data)
        except (json.JSONDecodeError, KeyError, TypeError, ValueError) as exc:
            return ProcessResult.failed(exc)
        metadata = extract_task_metadata(data)
        current_work_id = metadata.work_id if metadata is not None else None
        future: concurrent.futures.Future[bool] = asyncio.run_coroutine_threadsafe(
            self._process(msg, ctx, current_work_id=current_work_id),
            self._service_loop,
        )
        try:
            processed = await asyncio.wrap_future(future)
        except asyncio.CancelledError:
            future.cancel()
            raise
        if not processed:
            return ProcessResult.requeue(msg.to_dict(), max_attempts=None)
        return ProcessResult.success()
