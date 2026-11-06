# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0
"""Independent, durable retries for Compile output directory tags."""

import json
from asyncio import sleep
from typing import Any

from async_timeout import timeout

from openviking.server.identity import RequestContext, Role
from openviking.service.task_work_index import (
    TASK_WORK_ID_FIELD,
    TaskWorkRejected,
    bind_task_context_from_message,
    extract_task_metadata,
)
from openviking.storage.queuefs import QueueManager, get_queue_manager
from openviking.storage.queuefs.named_queue import DequeueHandlerBase
from openviking.storage.queuefs.process_result import ProcessResult
from openviking_cli.session.user_id import UserIdentifier
from openviking_cli.utils import get_logger

logger = get_logger(__name__)
ATTEMPT_TIMEOUT_SECONDS = 30.0


class CompileTargetTagProcessor(DequeueHandlerBase):
    """Keep one Work open until tags converge or its directory disappears.

    Retry counters and indexing progress travel in the durable queue payload.
    Backoff owns no directory lease and uses only this dedicated queue's slots.
    """

    def __init__(self, fs_service: Any) -> None:
        self._fs = fs_service

    async def on_dequeue(self, data: dict | None) -> ProcessResult:
        """Attempt once, then durably replace the delivery without replacing its Work."""
        if not data:
            return ProcessResult.success()
        try:
            payload = data.get("data", data)
            payload = json.loads(payload) if isinstance(payload, str) else dict(payload)
            metadata = extract_task_metadata(data)
            if metadata is None or not all(
                payload.get(key) for key in ("uri", "account_id", "user_id")
            ):
                raise ValueError("Missing Compile target tag ownership")
            payload[TASK_WORK_ID_FIELD] = metadata.work_id
            retry = max(0, int(payload.get("retry_count", 0)))
            ctx = RequestContext(
                UserIdentifier(payload["account_id"], payload["user_id"]),
                Role(payload.get("role") or "user"),
                group_ids=tuple(payload.get("group_ids") or ()),
                actor_peer_id=payload.get("actor_peer_id"),
                bypass_acl=bool(payload.get("bypass_acl", False)),
            )
        except (TypeError, ValueError, KeyError):
            return ProcessResult.failed("Invalid Compile target tag message")
        with bind_task_context_from_message(data):
            try:
                async with timeout(ATTEMPT_TIMEOUT_SECONDS):
                    if await self._fs.finalize_compile_directories(
                        payload["uri"], ctx, progress=payload
                    ):
                        return ProcessResult.success()
            except Exception as exc:
                self._warn(payload, retry, exc)
            payload["retry_count"] = retry + 1
            return await self._requeue(payload, min(60.0, 5.0 * 2 ** min(retry, 4)))

    async def _requeue(self, payload: dict, delay: float) -> ProcessResult:
        """Retain the original delivery through network failures and cancellation.

        Existing Work is reused so enqueue failures cannot create a failed child
        that later changes a successful repair Task into a failed Task.
        """
        failures = 0
        while True:
            await sleep(delay)
            try:
                async with timeout(ATTEMPT_TIMEOUT_SECONDS):
                    await get_queue_manager().enqueue(QueueManager.COMPILE_TARGET_TAG, payload)
                return ProcessResult.requeued()
            except TaskWorkRejected:
                return ProcessResult.cancelled()
            except Exception as exc:
                self._warn(payload, failures, exc)
                failures += 1
                delay = min(60.0, delay * 2)

    @staticmethod
    def _warn(payload: dict, attempt: int, error: Exception) -> None:
        """Log the first failure and exponentially spaced repeats without raw errors."""
        if attempt == 0 or attempt & (attempt - 1) == 0:
            logger.warning(
                "Compile target tag retry task_id=%s attempt=%d error_type=%s",
                payload.get("task_id"),
                attempt + 1,
                type(error).__name__,
            )
