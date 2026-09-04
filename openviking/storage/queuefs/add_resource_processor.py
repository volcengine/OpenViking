# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0
"""Durable add-resource queue consumer."""

import asyncio
import concurrent.futures
import json
from contextlib import suppress
from copy import deepcopy
from typing import Any, Dict, Optional

from openviking.observability.context import bind_execution_context
from openviking.server.identity import RequestContext, Role
from openviking.service.task_tracker import TaskStatus, get_task_tracker
from openviking.service.task_work_index import bind_task_context, extract_task_metadata
from openviking.storage.queuefs.add_resource_msg import AddResourceMsg
from openviking.storage.queuefs.named_queue import DequeueHandlerBase
from openviking.telemetry import (
    OperationTelemetry,
    bind_telemetry,
    register_telemetry,
    resolve_telemetry,
    unregister_telemetry,
)
from openviking.telemetry.request_wait_tracker import get_request_wait_tracker
from openviking.telemetry.resource_summary import record_resource_queue_metrics
from openviking_cli.exceptions import OpenVikingError
from openviking_cli.session.user_id import UserIdentifier
from openviking_cli.utils.logger import get_logger

logger = get_logger(__name__)


class AddResourceProcessor(DequeueHandlerBase):
    """Own an add-resource task until it reaches a terminal state and can be ACKed."""

    def __init__(
        self,
        resource_service: Any,
        service_loop: asyncio.AbstractEventLoop,
        queue_name: str,
        viking_fs: Any,
    ):
        self._resource_service = resource_service
        self._service_loop = service_loop
        self._queue_name = queue_name
        self._viking_fs = viking_fs

    async def _load_lock(self, msg: AddResourceMsg, ctx: RequestContext) -> Any:
        """Adopt a pathlock handoff ref, returning an owned lease dict."""
        if msg.lock_handoff is None:
            return None
        try:
            return await self._viking_fs._async_agfs.pathlock_adopt(msg.lock_handoff)
        except Exception as handoff_error:
            try:
                return await self._resource_service.reacquire_add_resource_job_lock(
                    msg.root_uri,
                    ctx,
                )
            except Exception:
                raise handoff_error

    async def _cleanup_staged_source(self, msg: AddResourceMsg, ctx: RequestContext) -> None:
        if msg.staged_source is None:
            return
        from openviking.resource.staged_source import StagedSource

        staged = StagedSource.from_dict(msg.staged_source)
        await self._viking_fs.delete_temp(staged.temp_uri, ctx=ctx)

    async def _release_cancelled_resources(
        self,
        msg: AddResourceMsg,
        ctx: RequestContext,
    ) -> None:
        if msg.lock_handoff is not None:
            try:
                lock = await self._viking_fs._async_agfs.pathlock_adopt(msg.lock_handoff)
                if msg.cleanup_empty_target_on_failure:
                    await self._resource_service._cleanup_reserved_target_if_empty(
                        root_uri=msg.root_uri,
                        ctx=ctx,
                        resource_lock=lock,
                    )
                await self._viking_fs._async_agfs.pathlock_release(lock)
            except Exception as exc:
                logger.warning("[AddResource] Failed to release cancelled lock handoff: %s", exc)
        with suppress(Exception):
            await self._cleanup_staged_source(msg, ctx)

    async def _requeue_lock_handoff(self, msg: AddResourceMsg, exc: Exception) -> bool:
        if msg.lock_handoff_retry >= 2:
            return False

        from openviking.storage.queuefs import get_queue_manager

        payload = msg.to_dict()
        payload["lock_handoff_retry"] = msg.lock_handoff_retry + 1
        await get_queue_manager().enqueue(self._queue_name, payload)
        logger.warning(
            "[AddResource] Requeued task %s after lock handoff failure: %s",
            msg.task_id,
            exc,
        )
        self.report_requeue()
        self.report_success()
        return True

    async def _process(self, msg: AddResourceMsg, data: Dict[str, Any]) -> None:
        telemetry_id = msg.telemetry_id or ""
        ctx = RequestContext(
            user=UserIdentifier(msg.account_id, msg.user_id),
            role=Role(msg.role),
            group_ids=tuple(msg.group_ids),
            actor_peer_id=msg.actor_peer_id,
            bypass_acl=msg.bypass_acl,
        )
        tracker = get_task_tracker()
        task = await tracker.create(
            "add_resource",
            resource_id=None if msg.defer_target_resolution else msg.root_uri,
            account_id=ctx.account_id,
            user_id=ctx.user.user_id,
            task_id=msg.task_id,
            meta={
                "source_path": msg.source_path,
                **({"internal": True} if msg.internal_task else {}),
            },
        )
        if task.status in (
            TaskStatus.CANCELLING,
            TaskStatus.COMPLETED,
            TaskStatus.FAILED,
            TaskStatus.CANCELLED,
        ):
            if task.status in (TaskStatus.CANCELLING, TaskStatus.CANCELLED):
                await self._release_cancelled_resources(msg, ctx)
            else:
                with suppress(Exception):
                    await self._cleanup_staged_source(msg, ctx)
            unregister_telemetry(telemetry_id)
            self.report_success()
            return None

        metadata = extract_task_metadata(data)
        replay_result = getattr(task, "result", None)
        resource_lock = None
        if replay_result is None:
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
                unregister_telemetry(telemetry_id)
                with suppress(Exception):
                    await self._cleanup_staged_source(msg, ctx)
                return None

        telemetry = resolve_telemetry(telemetry_id) if telemetry_id else None
        if telemetry is None:
            telemetry = OperationTelemetry(operation="add_resource_job", enabled=True)
            if telemetry_id:
                telemetry.telemetry_id = telemetry_id
            else:
                telemetry_id = telemetry.telemetry_id
            register_telemetry(telemetry)
        request_wait_tracker = get_request_wait_tracker()
        request_wait_tracker.register_request(telemetry_id)

        async def _set_stage(stage: str) -> None:
            await tracker.update_stage(
                msg.task_id,
                stage,
                account_id=ctx.account_id,
                user_id=ctx.user.user_id,
            )

        with (
            bind_execution_context(),
            bind_telemetry(telemetry),
            bind_task_context(msg.task_id, ctx.account_id, ctx.user.user_id),
        ):
            terminal = False
            try:
                if replay_result is None:
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
                        task_auth=await tracker.get_task_auth(
                            msg.task_id,
                            account_id=ctx.account_id,
                            user_id=ctx.user.user_id,
                        ),
                    )
                    if result.get("status") == "error":
                        errors = result.get("errors") or ["resource processing failed"]
                        code = result.get("code")
                        failure_result = {"code": code} if isinstance(code, str) and code else None
                        await tracker.fail(
                            msg.task_id,
                            "; ".join(str(error) for error in errors),
                            account_id=ctx.account_id,
                            user_id=ctx.user.user_id,
                            result=failure_result,
                        )
                        terminal = True
                        self.report_error("resource processing failed", data)
                        return None
                    await tracker.complete(
                        msg.task_id,
                        deepcopy(result),
                        account_id=ctx.account_id,
                        user_id=ctx.user.user_id,
                        resource_id=result.get("root_uri"),
                    )
                else:
                    result = deepcopy(replay_result)
                await tracker.wait_for_descendants(msg.task_id, metadata.work_id)
                result.setdefault(
                    "queue_status", request_wait_tracker.build_queue_status(telemetry_id)
                )
                if replay_result is None:
                    result["context_count"] = request_wait_tracker.get_embedding_context_count(
                        telemetry_id
                    )
                record_resource_queue_metrics(
                    telemetry=telemetry,
                    telemetry_id=telemetry_id,
                    root_uri=result.get("root_uri"),
                )

                # Extract token usage summary from telemetry and inject into result
                _snapshot = telemetry.finish()
                if _snapshot is not None:
                    _tokens = _snapshot.summary.get("tokens", {})
                    if _tokens:
                        result.setdefault("usage", {})
                        result["usage"]["tokens"] = _tokens

                await self._resource_service._link_resource_reason_memory(
                    result=result,
                    ctx=ctx,
                    reason=msg.reason,
                    source_name=msg.source_name,
                    timeout=msg.timeout,
                )
                await tracker.complete(
                    msg.task_id,
                    result,
                    account_id=ctx.account_id,
                    user_id=ctx.user.user_id,
                    resource_id=result.get("root_uri"),
                )
                terminal = True
                self.report_success()
                return None
            except Exception as exc:
                failure_result = (
                    {"code": exc.code} if isinstance(exc, OpenVikingError) and exc.code else None
                )
                await tracker.fail(
                    msg.task_id,
                    str(exc),
                    account_id=ctx.account_id,
                    user_id=ctx.user.user_id,
                    result=failure_result,
                )
                terminal = True
                self.report_error(str(exc), data)
                return None
            finally:
                request_wait_tracker.cleanup(telemetry_id)
                unregister_telemetry(telemetry_id)
                with suppress(Exception):
                    if resource_lock is not None:
                        await self._viking_fs._async_agfs.pathlock_release(resource_lock)
                if terminal:
                    with suppress(Exception):
                        await self._cleanup_staged_source(msg, ctx)

    async def on_cancelled(self, data: Optional[Dict[str, Any]]) -> Optional[Dict[str, Any]]:
        """Release an enqueue-time lock before ACKing cancelled work."""
        try:
            payload = data.get("data", data) if isinstance(data, dict) else data
            if isinstance(payload, str):
                payload = json.loads(payload)
            msg = AddResourceMsg.from_dict(payload)
        except (json.JSONDecodeError, TypeError, ValueError) as exc:
            self.report_error(str(exc), data)
            return None
        future = asyncio.run_coroutine_threadsafe(
            self._release_cancelled_resources(
                msg,
                RequestContext(
                    user=UserIdentifier(msg.account_id, msg.user_id),
                    role=Role(msg.role),
                    group_ids=tuple(msg.group_ids),
                    actor_peer_id=msg.actor_peer_id,
                    bypass_acl=msg.bypass_acl,
                ),
            ),
            self._service_loop,
        )
        await asyncio.wrap_future(future)
        unregister_telemetry(msg.telemetry_id or "")
        self.report_success()
        return None

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
