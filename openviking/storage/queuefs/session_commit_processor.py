# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0
"""Queue consumer for restart-safe Session Phase 2 work."""

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
from openviking.storage.queuefs.process_result import ProcessResult
from openviking.storage.queuefs.session_commit_msg import SessionCommitMsg
from openviking.telemetry.span_models import create_root_span_attributes
from openviking_cli.session.user_id import UserIdentifier
from openviking_cli.utils.logger import get_logger

if TYPE_CHECKING:
    from openviking.service.session_service import SessionService

logger = get_logger(__name__)


class SessionCommitProcessor(DequeueHandlerBase):
    def __init__(
        self,
        session_service: "SessionService",
    ) -> None:
        self._session_service = session_service

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
        # "__unknown__" (mirrors SemanticProcessor.on_dequeue). Restore the
        # worker's previous context when processing finishes.
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

                await get_queue_manager().enqueue(
                    QueueManager.SESSION_COMMIT,
                    msg.to_dict(),
                )
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

    async def on_cancelled(self, data: Optional[Dict[str, Any]]) -> ProcessResult:
        if not data:
            return ProcessResult.cancelled()

        try:
            msg, ctx = self._parse_message(data)
        except (json.JSONDecodeError, KeyError, TypeError, ValueError) as exc:
            return ProcessResult.failed(str(exc))

        await self._finalize_cancelled(msg, ctx)
        return ProcessResult.cancelled()

    async def on_dequeue(self, data: Optional[Dict[str, Any]]) -> ProcessResult:
        if not data:
            return ProcessResult.success()

        try:
            msg, ctx = self._parse_message(data)
        except (json.JSONDecodeError, KeyError, TypeError, ValueError) as exc:
            return ProcessResult.failed(str(exc))
        try:
            processed = await self._process(msg, ctx)
        except Exception as exc:
            # A crash inside the Phase-2 worker (e.g. a torn read of the live
            # messages.jsonl raising json.JSONDecodeError from Session.load())
            # must still settle this delivery and the task record. Letting the
            # exception escape leaves the delivery unacked, the queue row stuck
            # in processing, and the task pending until the client times out;
            # a restart replays the same payload into the same crash.
            # Mirror the session-missing branch: reuse the Phase-1 task record
            # (tracker.create is idempotent for an explicit task_id owned by
            # the same user) and record the real error as a terminal failure.
            logger.error(
                "[SessionCommit] Phase-2 worker failed for %s: %s",
                msg.session_id,
                exc,
            )
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
                str(exc),
                account_id=ctx.account_id,
                user_id=ctx.user.user_id,
            )
            return ProcessResult.failed(str(exc))
        return ProcessResult.success() if processed else ProcessResult.requeued()
