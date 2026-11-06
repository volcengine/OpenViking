# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0
"""Queue consumer for restart-safe Session Phase 2 work."""

import asyncio
import json
import time
from typing import TYPE_CHECKING, Any, Dict, Optional

from openviking.observability.context import (
    bind_root_observability_context,
    reset_root_observability_context,
)
from openviking.server.identity import RequestContext, Role
from openviking.service.task_domain import TaskStatus
from openviking.service.task_tracker import get_task_tracker
from openviking.service.task_work_index import bind_task_context_from_message
from openviking.storage.queuefs.named_queue import DequeueHandlerBase
from openviking.storage.queuefs.process_result import ProcessResult
from openviking.storage.queuefs.session_commit_msg import SessionCommitMsg
from openviking.telemetry.span_models import create_root_span_attributes
from openviking_cli.session.user_id import UserIdentifier
from openviking_cli.utils.logger import get_logger

if TYPE_CHECKING:
    from openviking.service.session_service import SessionService

logger = get_logger(__name__)
_DEFAULT_STALLED_COMMIT_TIMEOUT_SECONDS = 1800.0


def _session_commit_stall_timeout_seconds() -> float:
    try:
        from openviking_cli.utils.config import get_openviking_config

        value = get_openviking_config().queue_workers.session_commit
        return max(0.0, float(value.stalled_predecessor_timeout_seconds))
    except Exception:
        return _DEFAULT_STALLED_COMMIT_TIMEOUT_SECONDS


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
        with bind_task_context_from_message(msg.to_dict()):
            return await self._process_bound(msg, ctx)

    async def _process_bound(self, msg: SessionCommitMsg, ctx: RequestContext) -> bool:
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
                await tracker.fail(
                    msg.task_id,
                    error,
                    account_id=ctx.account_id,
                    user_id=ctx.user.user_id,
                )
                return True
            processed = await self._resume_with_stall_watchdog(session, msg, ctx)
            if not processed:
                from openviking.storage.queuefs import QueueManager, get_queue_manager

                await get_queue_manager().enqueue(
                    QueueManager.SESSION_COMMIT,
                    msg.to_dict(),
                )
            return processed
        finally:
            reset_root_observability_context(root_context_token)

    async def _resume_with_stall_watchdog(
        self,
        session: Any,
        msg: SessionCommitMsg,
        ctx: RequestContext,
    ) -> bool:
        timeout = _session_commit_stall_timeout_seconds()
        if timeout <= 0:
            return await session.resume_queued_commit(msg)

        monitor = asyncio.create_task(self._watch_stalled_commit(msg, ctx, timeout))
        try:
            return await session.resume_queued_commit(msg)
        finally:
            monitor.cancel()
            await asyncio.gather(monitor, return_exceptions=True)

    async def _watch_stalled_commit(
        self,
        msg: SessionCommitMsg,
        ctx: RequestContext,
        timeout: float,
    ) -> None:
        tracker = get_task_tracker()
        poll_interval = min(max(timeout / 10, 0.01), 30.0)
        while True:
            await asyncio.sleep(poll_interval)
            task = await tracker.get(
                msg.task_id,
                account_id=ctx.account_id,
                user_id=ctx.user.user_id,
            )
            if task is None or task.status not in (TaskStatus.PENDING, TaskStatus.RUNNING):
                return

            last_progress = tracker.last_progress_at(
                msg.task_id,
                account_id=ctx.account_id,
                user_id=ctx.user.user_id,
            )
            last_progress = max(task.updated_at, last_progress or task.updated_at)
            idle_seconds = time.time() - last_progress
            if idle_seconds < timeout:
                continue

            error = f"Session commit made no progress for {idle_seconds:.0f}s"
            logger.error(
                "Cancelling stalled session commit task=%s session=%s archive=%s idle=%.1fs "
                "timeout=%.1fs: %s",
                msg.task_id,
                msg.session_id,
                msg.archive_uri,
                idle_seconds,
                timeout,
                error,
            )
            try:
                await tracker.cancel(
                    msg.task_id,
                    account_id=ctx.account_id,
                    user_id=ctx.user.user_id,
                )
            except ValueError as exc:
                logger.warning("Could not cancel stalled session commit %s: %s", msg.task_id, exc)
            return

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
        # Failed registration must retain queue work; processing errors remain terminal.
        await get_task_tracker().create(
            "session_commit",
            resource_id=msg.session_id,
            account_id=ctx.account_id,
            user_id=ctx.user.user_id,
            task_id=msg.task_id,
        )
        with bind_task_context_from_message(data):
            try:
                processed = await self._process(msg, ctx)
            except Exception as exc:
                return ProcessResult.failed(str(exc))
        return ProcessResult.success() if processed else ProcessResult.requeued()
