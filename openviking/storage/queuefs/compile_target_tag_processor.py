# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0
"""Independent, durable retries for Compile output directory tags."""

import json
from asyncio import CancelledError, sleep
from typing import Any

from async_timeout import timeout

from openviking.server.error_mapping import is_not_found_error
from openviking.server.identity import RequestContext, Role
from openviking.service.task_tracker_concurrency import run_to_completion
from openviking.service.task_work_index import (
    TASK_WORK_ID_FIELD,
    TaskWorkRejected,
    bind_task_context_from_message,
    extract_task_metadata,
)
from openviking.storage.errors import HandoffRecoveryRequired
from openviking.storage.pathlock_transfer import PathLockGuard
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
    Backoff retains only the Compile admission lease, never a content lease.
    """

    def __init__(self, fs_service: Any) -> None:
        self._fs = fs_service

    async def on_cancelled(self, data: dict | None) -> ProcessResult:
        """Release a queued admission holder before cancelled work is acknowledged."""
        payload = data.get("data", data) if data else {}
        payload = json.loads(payload) if isinstance(payload, str) else payload
        if payload.get("target_lock"):
            guard = PathLockGuard(self._fs._ensure_initialized()._async_agfs)
            try:
                await guard.adopt(payload["target_lock"])
            except HandoffRecoveryRequired:
                pass  # An expired or already consumed holder needs no new reservation.
            finally:
                await guard.close()
        return ProcessResult.cancelled()

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
            handoff = payload.get("target_lock")
            if handoff:
                requests, owner = self._fs._compile_target_lock_scope(payload["uri"], ctx)
                if handoff.get("owner_id") != owner or handoff.get("covered_paths") != requests:
                    raise ValueError("Invalid Compile target lock scope")
        except (TypeError, ValueError, KeyError):
            return ProcessResult.failed("Invalid Compile target tag message")
        agfs = self._fs._ensure_initialized()._async_agfs if handoff else None
        guard = PathLockGuard(agfs)
        try:
            with bind_task_context_from_message(data):
                try:
                    async with timeout(ATTEMPT_TIMEOUT_SECONDS):
                        if handoff:
                            try:
                                await guard.adopt(handoff)
                            except HandoffRecoveryRequired:
                                try:
                                    await self._fs._ensure_initialized().stat(
                                        payload["uri"], ctx=ctx, skip_count=True
                                    )
                                except Exception as exc:
                                    if is_not_found_error(exc):
                                        return ProcessResult.success()
                                    raise
                                # shortcut: leases leave a restart gap; use durable admission
                                # if continuous exclusion across outages is required.
                                await guard.acquire(
                                    lambda: agfs.pathlock_reacquire_batch(
                                        requests, owner, timeout_secs=1.0
                                    )
                                )
                        if await self._fs.finalize_compile_directories(
                            payload["uri"], ctx, progress=payload
                        ):
                            return ProcessResult.success()
                except Exception as exc:
                    self._warn(payload, retry, exc)
                payload["retry_count"] = retry + 1
                return await self._requeue(payload, min(60.0, 5.0 * 2 ** min(retry, 4)), guard)
        except CancelledError:
            if guard.lease is None and not guard.transferred:
                await run_to_completion(lambda: self.on_cancelled(payload))
            raise
        finally:
            await guard.close()

    async def _requeue(self, payload: dict, delay: float, guard: PathLockGuard) -> ProcessResult:
        """Retain the original delivery through network failures and cancellation.

        Existing Work is reused so enqueue failures cannot create a failed child
        that later changes a successful repair Task into a failed Task.
        """
        failures = 0
        while True:
            await sleep(delay)
            try:
                async with timeout(ATTEMPT_TIMEOUT_SECONDS):

                    async def enqueue():
                        return await get_queue_manager().enqueue(
                            QueueManager.COMPILE_TARGET_TAG, payload
                        )

                    if guard.lease is not None:
                        payload["target_lock"] = await guard.to_handoff()
                        await guard.handoff_and_dispatch(payload["target_lock"], enqueue)
                    else:
                        await enqueue()
                return ProcessResult.requeued()
            except TaskWorkRejected:
                if guard.lease is None and not guard.transferred:
                    await self.on_cancelled(payload)
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
