# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0
"""Durable add-resource consumer on the legacy ExternalParse queue."""

import asyncio
import concurrent.futures
import json
from contextlib import suppress
from typing import Any, Dict, Optional

from openviking.observability.context import bind_execution_context
from openviking.server.identity import RequestContext, Role
from openviking.service.task_tracker import TaskStatus, get_task_tracker
from openviking.storage.queuefs.add_resource_msg import AddResourceMsg
from openviking.storage.queuefs.named_queue import DequeueHandlerBase
from openviking.telemetry import bind_telemetry, resolve_telemetry
from openviking.telemetry.resource_summary import summarize_queue_errors
from openviking_cli.session.user_id import UserIdentifier
from openviking_cli.utils.logger import get_logger

logger = get_logger(__name__)


class AddResourceProcessor(DequeueHandlerBase):
    """Own an add-resource task until it reaches a terminal state and can be ACKed."""

    def __init__(
        self,
        resource_service: Any,
        service_loop: asyncio.AbstractEventLoop,
    ):
        self._resource_service = resource_service
        self._service_loop = service_loop

    async def _load_lock(self, msg: AddResourceMsg, ctx: RequestContext) -> Any:
        if msg.lock_handoff is None:
            return None
        from openviking.storage.transaction.lock_lease import LockHandoffRef, OwnedLockLease

        ref = LockHandoffRef.from_value(msg.lock_handoff)
        if ref is None:
            raise ValueError("Invalid lock_handoff")
        try:
            return await OwnedLockLease.from_handoff(ref)
        except Exception as handoff_error:
            try:
                return await self._resource_service.reacquire_add_resource_job_lock(
                    msg.root_uri,
                    ctx,
                )
            except Exception:
                raise handoff_error

    async def _requeue_lock_handoff(self, msg: AddResourceMsg, exc: Exception) -> bool:
        if msg.lock_handoff_retry >= 2:
            return False

        from openviking.storage.queuefs import QueueManager, get_queue_manager

        payload = msg.to_dict()
        payload["lock_handoff_retry"] = msg.lock_handoff_retry + 1
        await get_queue_manager().enqueue(QueueManager.ADD_RESOURCE, payload)
        logger.warning(
            "[AddResource] Requeued task %s after lock handoff failure: %s",
            msg.task_id,
            exc,
        )
        self.report_requeue()
        self.report_success()
        return True

    async def _process(self, msg: AddResourceMsg, data: Dict[str, Any]) -> None:
        ctx = RequestContext(
            user=UserIdentifier(msg.account_id, msg.user_id),
            role=Role(msg.role),
            actor_peer_id=msg.actor_peer_id,
        )
        tracker = get_task_tracker()
        task = await tracker.create(
            "add_resource",
            resource_id=msg.root_uri,
            account_id=ctx.account_id,
            user_id=ctx.user.user_id,
            task_id=msg.task_id,
        )
        if task.status in (TaskStatus.COMPLETED, TaskStatus.FAILED):
            self.report_success()
            return None

        resource_lock = None
        try:
            resource_lock = await self._load_lock(msg, ctx)
        except Exception as exc:
            if await self._requeue_lock_handoff(msg, exc):
                return None
            await tracker.fail(
                msg.task_id,
                f"Invalid lock_handoff: {exc}",
                account_id=ctx.account_id,
                user_id=ctx.user.user_id,
            )
            self.report_error(f"Invalid lock_handoff: {exc}", data)
            return None

        telemetry_id = msg.telemetry_id or ""
        telemetry = resolve_telemetry(telemetry_id) if telemetry_id else None
        if telemetry is None:
            from openviking.telemetry.operation import OperationTelemetry

            telemetry = OperationTelemetry(operation="add_resource_job", enabled=False)
            if telemetry_id:
                telemetry.telemetry_id = telemetry_id

        async def _set_stage(stage: str) -> None:
            await tracker.update_stage(
                msg.task_id,
                stage,
                account_id=ctx.account_id,
                user_id=ctx.user.user_id,
            )

        with bind_execution_context(), bind_telemetry(telemetry):
            try:
                await tracker.start(
                    msg.task_id,
                    account_id=ctx.account_id,
                    user_id=ctx.user.user_id,
                    stage="queued",
                )
                result = await self._resource_service.execute_add_resource_job(
                    msg,
                    ctx=ctx,
                    resource_lock=resource_lock,
                    stage_callback=_set_stage,
                )
                if result.get("status") == "error":
                    errors = result.get("errors") or ["resource processing failed"]
                    await tracker.fail(
                        msg.task_id,
                        "; ".join(str(error) for error in errors),
                        account_id=ctx.account_id,
                        user_id=ctx.user.user_id,
                    )
                    self.report_error("resource processing failed", data)
                    return None
                queue_errors = summarize_queue_errors(result.get("queue_status"))
                if queue_errors:
                    await tracker.fail(
                        msg.task_id,
                        "queue processing failed: " + "; ".join(queue_errors),
                        account_id=ctx.account_id,
                        user_id=ctx.user.user_id,
                    )
                    self.report_error("queue processing failed", data)
                    return None
                await tracker.complete(
                    msg.task_id,
                    result,
                    account_id=ctx.account_id,
                    user_id=ctx.user.user_id,
                )
                self.report_success()
                return None
            except asyncio.CancelledError:
                # Leave both task and QueueFS message active; RecoverStale owns restart recovery.
                raise
            except Exception as exc:
                await tracker.fail(
                    msg.task_id,
                    str(exc),
                    account_id=ctx.account_id,
                    user_id=ctx.user.user_id,
                )
                self.report_error(str(exc), data)
                return None
            finally:
                with suppress(Exception):
                    if resource_lock is not None:
                        await resource_lock.close()

    async def on_dequeue(self, data: Optional[Dict[str, Any]]) -> Optional[Dict[str, Any]]:
        if not data:
            return None
        try:
            if not isinstance(data, dict):
                raise ValueError("Queue message must be an object")
            payload = data.get("data", data)
            if isinstance(payload, str):
                payload = json.loads(payload)
            msg = AddResourceMsg.from_dict(payload)
        except (json.JSONDecodeError, TypeError, ValueError) as exc:
            self.report_error(str(exc), data)
            return None

        future: concurrent.futures.Future[None] = asyncio.run_coroutine_threadsafe(
            self._process(msg, data),
            self._service_loop,
        )
        try:
            await asyncio.wrap_future(future)
        except asyncio.CancelledError:
            future.cancel()
            raise
        return None
