# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0
"""Queue consumer for restart-safe Session Phase 2 work."""

import asyncio
import concurrent.futures
import json
from typing import TYPE_CHECKING, Any, Dict, Optional

from openviking.server.identity import RequestContext, Role
from openviking.service.task_tracker import get_task_tracker
from openviking.storage.queuefs.named_queue import DequeueHandlerBase
from openviking.storage.queuefs.session_commit_msg import SessionCommitMsg
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

    async def _process(self, msg: SessionCommitMsg, ctx: RequestContext) -> None:
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
            return
        await session.load()
        await session.resume_queued_commit(msg)

    async def on_dequeue(
        self, data: Optional[Dict[str, Any]]
    ) -> Optional[Dict[str, Any]]:
        if not data:
            return None

        try:
            payload = data.get("data", data)
            if isinstance(payload, str):
                payload = json.loads(payload)
            msg = SessionCommitMsg(**payload)
            ctx = RequestContext(
                user=UserIdentifier.from_dict(msg.user),
                role=Role.USER,
                actor_peer_id=msg.actor_peer_id,
            )
        except (json.JSONDecodeError, KeyError, TypeError, ValueError) as exc:
            self.report_error(str(exc), data)
            return None
        future: concurrent.futures.Future[None] = asyncio.run_coroutine_threadsafe(
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
