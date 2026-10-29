# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0
"""Queue consumer that owns one durable asynchronous reindex request."""

from __future__ import annotations

import asyncio
import json
from typing import Any, Dict, Optional

from openviking.server.identity import RequestContext, Role
from openviking.service.task_tracker import TaskStatus, get_task_tracker
from openviking.service.task_work_index import bind_task_context_from_message, extract_task_metadata
from openviking.storage.pathlock_transfer import PathLockGuard
from openviking.storage.queuefs.named_queue import DequeueHandlerBase
from openviking.storage.queuefs.process_result import ProcessResult
from openviking.storage.queuefs.reindex_msg import ReindexMsg
from openviking.telemetry import (
    OperationTelemetry,
    bind_telemetry,
    register_telemetry,
    resolve_telemetry,
    unregister_telemetry,
)
from openviking_cli.exceptions import OpenVikingError
from openviking_cli.session.user_id import UserIdentifier
from openviking_cli.utils import get_logger

logger = get_logger(__name__)


class ReindexProcessor(DequeueHandlerBase):
    def __init__(self, viking_fs: Any) -> None:
        self._viking_fs = viking_fs

    @staticmethod
    def _ctx(msg: ReindexMsg) -> RequestContext:
        return RequestContext(
            user=UserIdentifier(account_id=msg.account_id, user_id=msg.user_id),
            role=Role(msg.role),
            group_ids=tuple(msg.group_ids),
            actor_peer_id=msg.actor_peer_id,
            bypass_acl=msg.bypass_acl,
        )

    async def _adopt_or_reacquire(self, msg: ReindexMsg, ctx: RequestContext) -> Dict[str, Any]:
        agfs = self._viking_fs._async_agfs
        if msg.lock_handoff is not None:
            try:
                return await agfs.pathlock_adopt(msg.lock_handoff)
            except Exception:
                pass
        stat = await self._viking_fs.stat(msg.uri, ctx=ctx, skip_count=True)
        acquire = (
            agfs.pathlock_acquire_tree
            if stat.get("isDir", stat.get("is_dir"))
            else agfs.pathlock_acquire_exact
        )
        return await acquire(self._viking_fs._uri_to_path(msg.uri, ctx=ctx))

    async def _requeue_lock_handoff(self, msg: ReindexMsg, exc: Exception) -> bool:
        if msg.lock_handoff_retry >= 2:
            return False

        from openviking.storage.queuefs import get_queue_manager

        payload = msg.to_dict()
        payload["lock_handoff_retry"] = msg.lock_handoff_retry + 1
        queue_manager = get_queue_manager()
        await queue_manager.enqueue(queue_manager.REINDEX, payload)
        logger.warning(
            "[Reindex] Requeued task %s after lock handoff failure: %s",
            msg.task_id,
            exc,
        )
        return True

    async def on_dequeue(self, data: Optional[Dict[str, Any]]) -> ProcessResult:
        if not data:
            return ProcessResult.success()
        with bind_task_context_from_message(data):
            return await self._on_dequeue_bound(data)

    async def _on_dequeue_bound(self, data: Dict[str, Any]) -> ProcessResult:
        payload = data.get("data", data)
        if isinstance(payload, str):
            payload = json.loads(payload)
        msg = ReindexMsg.from_dict(payload)
        ctx = self._ctx(msg)
        tracker = get_task_tracker()
        task = await tracker.get(
            msg.task_id,
            account_id=ctx.account_id,
            user_id=ctx.user.user_id,
        )
        if task is not None and task.status in {
            TaskStatus.COMPLETED,
            TaskStatus.FAILED,
            TaskStatus.CANCELLED,
        }:
            unregister_telemetry(msg.telemetry_id or "")
            return ProcessResult.success()
        lock_guard = PathLockGuard(self._viking_fs._async_agfs)
        try:
            await lock_guard.acquire(lambda: self._adopt_or_reacquire(msg, ctx))
        except asyncio.CancelledError:
            await lock_guard.close()
            raise
        except Exception as exc:
            if await self._requeue_lock_handoff(msg, exc):
                return ProcessResult.requeued()
            await tracker.fail(
                msg.task_id,
                f"Invalid lock_handoff: {exc}",
                account_id=ctx.account_id,
                user_id=ctx.user.user_id,
            )
            unregister_telemetry(msg.telemetry_id or "")
            return ProcessResult.failed(f"Invalid lock_handoff: {exc}")

        telemetry_id = msg.telemetry_id or ""
        telemetry = resolve_telemetry(telemetry_id) if telemetry_id else None
        if telemetry is None:
            telemetry = OperationTelemetry(operation="reindex_job", enabled=True)
            if telemetry_id:
                telemetry.telemetry_id = telemetry_id
            else:
                telemetry_id = telemetry.telemetry_id
            register_telemetry(telemetry)
        try:
            await tracker.start(
                msg.task_id, account_id=ctx.account_id, user_id=ctx.user.user_id, stage="queued"
            )
            from openviking.service.reindex_executor import ReindexExecutor

            executor = ReindexExecutor()
            with bind_telemetry(telemetry):
                # Share ownership state so even argument/initialization failures
                # release the lease once, while handoff clears both references.
                result = await executor._run(
                    uri=msg.uri,
                    object_type=msg.object_type,
                    mode=msg.mode,
                    force=msg.force,
                    recursive=msg.recursive,
                    ingest_options=executor._resolve_ingest_options(
                        mode=msg.mode, tags=msg.tags, tag_mode=msg.tag_mode
                    ),
                    ctx=ctx,
                    lease_guard=lock_guard,
                )
            metadata = extract_task_metadata(data)
            await tracker.wait_for_task_work(
                msg.task_id,
                exclude_work_id=metadata.work_id if metadata is not None else None,
            )
            await tracker.complete(
                msg.task_id, result, account_id=ctx.account_id, user_id=ctx.user.user_id
            )
            return ProcessResult.success()
        except Exception as exc:
            await tracker.fail(
                msg.task_id,
                str(exc),
                account_id=ctx.account_id,
                user_id=ctx.user.user_id,
                result={"code": exc.code, "details": exc.details}
                if isinstance(exc, OpenVikingError)
                else None,
            )
            return ProcessResult.failed(str(exc))
        finally:
            await lock_guard.close()
            unregister_telemetry(telemetry_id)

    async def on_cancelled(self, data: Optional[Dict[str, Any]]) -> ProcessResult:
        """Release the root lock handoff before QueueFS ACKs cancelled work."""
        try:
            payload = data.get("data", data) if isinstance(data, dict) else data
            if isinstance(payload, str):
                payload = json.loads(payload)
            msg = ReindexMsg.from_dict(payload)
        except (json.JSONDecodeError, TypeError, ValueError) as exc:
            return ProcessResult.failed(str(exc))

        if msg.lock_handoff is not None:
            lock_guard = PathLockGuard(self._viking_fs._async_agfs)
            try:
                await lock_guard.adopt(msg.lock_handoff)
            except Exception as exc:
                logger.warning("[Reindex] Failed to release cancelled lock handoff: %s", exc)
            finally:
                await lock_guard.close()
        return ProcessResult.cancelled()
