# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0
"""Queue consumer that owns durable content-write task finalization."""

from __future__ import annotations

import asyncio
import json
from copy import deepcopy
from typing import Any, Dict, Optional

from openviking.server.identity import RequestContext, Role
from openviking.service.task_domain import TaskPatch, TaskStatus
from openviking.service.task_tracker import get_task_tracker
from openviking.service.task_work_index import (
    bind_task_context_from_message,
    extract_task_metadata,
)
from openviking.storage.context_update_execution import commit_and_enqueue_plan
from openviking.storage.context_update_plan import ContextUpdatePlan
from openviking.storage.pathlock_transfer import PathLockGuard
from openviking.storage.queuefs.content_write_msg import ContentWriteMsg
from openviking.storage.queuefs.named_queue import DequeueHandlerBase
from openviking.storage.queuefs.process_result import ProcessResult
from openviking.telemetry import bind_telemetry, resolve_telemetry, unregister_telemetry
from openviking.utils.ingest_options import IngestOptions
from openviking_cli.exceptions import OpenVikingError
from openviking_cli.session.user_id import UserIdentifier
from openviking_cli.utils import get_logger

logger = get_logger(__name__)


class ContentWriteProcessor(DequeueHandlerBase):
    def __init__(self, viking_fs: Any, vikingdb: Any = None) -> None:
        self._viking_fs = viking_fs
        self._vikingdb = vikingdb

    @staticmethod
    def _ctx(msg: ContentWriteMsg) -> RequestContext:
        return RequestContext(
            user=UserIdentifier(account_id=msg.account_id, user_id=msg.user_id),
            role=Role(msg.role),
            group_ids=tuple(msg.group_ids),
            actor_peer_id=msg.actor_peer_id,
            bypass_acl=msg.bypass_acl,
        )

    def _uris_for_reacquire(self, msg: ContentWriteMsg) -> list[str]:
        refresh = msg.refresh
        if str(refresh.get("kind") or "") != "batch":
            uri = str(refresh.get("uri") or msg.resource_id)
            return [uri] if uri else []

        uris: set[str] = set()
        result = msg.result if isinstance(msg.result, dict) else {}
        for field in ("created", "updated", "unchanged"):
            values = result.get(field)
            if isinstance(values, list):
                uris.update(str(value) for value in values if value)
        for field in ("refresh_kinds", "file_ingest_options"):
            values = refresh.get(field)
            if isinstance(values, dict):
                uris.update(str(uri) for uri in values if uri)
        for item in refresh.get("resource_plans") or ():
            if not isinstance(item, dict):
                continue
            plan = item.get("plan")
            if isinstance(plan, dict) and plan.get("root_uri"):
                uris.add(str(plan["root_uri"]))
        return sorted(uris)

    async def _reacquire_lock(self, msg: ContentWriteMsg, ctx: RequestContext) -> Dict[str, Any]:
        covered_paths = (
            msg.lock_handoff.get("covered_paths")
            if isinstance(msg.lock_handoff, dict)
            else None
        )
        if isinstance(covered_paths, list) and covered_paths:
            return await self._viking_fs._async_agfs.pathlock_acquire_batch(covered_paths)

        uris = self._uris_for_reacquire(msg)
        if not uris:
            raise ValueError("content-write lock reacquire has no resource URI")
        paths = [self._viking_fs._uri_to_path(uri, ctx=ctx) for uri in uris]
        if len(paths) == 1:
            return await self._viking_fs._async_agfs.pathlock_acquire_exact(paths[0])
        return await self._viking_fs._async_agfs.pathlock_acquire_exact_batch(paths)

    async def _load_lock(self, msg: ContentWriteMsg, ctx: RequestContext) -> Dict[str, Any] | None:
        if msg.lock_handoff is None:
            return None
        try:
            return await self._viking_fs._async_agfs.pathlock_adopt(msg.lock_handoff)
        except Exception as handoff_error:
            try:
                return await self._reacquire_lock(msg, ctx)
            except Exception:
                raise handoff_error

    async def _requeue_lock_handoff(self, msg: ContentWriteMsg, exc: Exception) -> bool:
        if msg.lock_handoff_retry >= 2:
            return False

        from openviking.storage.queuefs import get_queue_manager

        payload = msg.to_dict()
        payload["lock_handoff_retry"] = msg.lock_handoff_retry + 1
        queue_manager = get_queue_manager()
        await queue_manager.enqueue(queue_manager.CONTENT_WRITE, payload)
        logger.warning(
            "[ContentWrite] Requeued task %s after lock handoff failure: %s",
            msg.task_id,
            exc,
        )
        return True

    async def _release_orphaned_lock_handoff(self, msg: ContentWriteMsg) -> None:
        if msg.lock_handoff is None:
            return

        lock_guard = PathLockGuard(self._viking_fs._async_agfs)
        try:
            await lock_guard.adopt(msg.lock_handoff)
        except Exception as exc:
            logger.warning("[ContentWrite] Failed to release orphaned lock handoff: %s", exc)
        finally:
            await lock_guard.close()

    async def on_cancelled(self, data: Optional[Dict[str, Any]]) -> ProcessResult:
        """Release a queued content-write lock before cancelled work is ACKed."""
        try:
            payload = data.get("data", data) if isinstance(data, dict) else data
            if isinstance(payload, str):
                payload = json.loads(payload)
            msg = ContentWriteMsg.from_dict(payload)
        except (json.JSONDecodeError, TypeError, ValueError) as exc:
            return ProcessResult.failed(str(exc))

        await self._release_orphaned_lock_handoff(msg)
        unregister_telemetry(msg.telemetry_id or "")
        return ProcessResult.cancelled()

    async def on_dequeue(self, data: Optional[Dict[str, Any]]) -> ProcessResult:
        if not data:
            return ProcessResult.success()
        with bind_task_context_from_message(data):
            return await self._on_dequeue_bound(data)

    async def _on_dequeue_bound(self, data: Dict[str, Any]) -> ProcessResult:
        payload = data.get("data", data)
        if isinstance(payload, str):
            payload = json.loads(payload)
        msg = ContentWriteMsg.from_dict(payload)
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
            await self._release_orphaned_lock_handoff(msg)
            unregister_telemetry(msg.telemetry_id or "")
            return ProcessResult.success()
        await tracker.start(
            msg.task_id,
            account_id=ctx.account_id,
            user_id=ctx.user.user_id,
        )

        metadata = extract_task_metadata(data)
        dispatch_complete = (
            metadata is not None
            and await tracker.is_enqueue_complete(
                msg.task_id,
                metadata.work_id,
                account_id=ctx.account_id,
                user_id=ctx.user.user_id,
            )
        )
        lock_guard = PathLockGuard(self._viking_fs._async_agfs)
        if not dispatch_complete or (
            msg.lock_scope == "descendants" and msg.lock_handoff is not None
        ):
            try:
                await lock_guard.acquire(lambda: self._load_lock(msg, ctx))
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
                return ProcessResult.failed(f"Invalid lock_handoff: {exc}")

        telemetry_id = msg.telemetry_id or ""
        telemetry = resolve_telemetry(telemetry_id) if telemetry_id else None
        semantic_lock_handoff = None
        try:
            result_updates: Dict[str, Any] = {}
            checkpoint_result = (
                deepcopy(task.result) if dispatch_complete and isinstance(task.result, dict) else None
            )
            if not dispatch_complete:
                if lock_guard.lease is not None and msg.lock_scope == "semantic_handoff":
                    semantic_lock_handoff = await lock_guard.to_handoff()

                async def execute_refresh() -> tuple[Dict[str, Any], bool]:
                    if semantic_lock_handoff is None:
                        return await self._execute_refresh(msg, ctx)
                    return await self._execute_refresh(
                        msg,
                        ctx,
                        semantic_lock_handoff=semantic_lock_handoff,
                    )

                async def dispatch_refresh() -> tuple[Dict[str, Any], bool]:
                    if telemetry is None:
                        return await execute_refresh()
                    with bind_telemetry(telemetry):
                        return await execute_refresh()

                if semantic_lock_handoff is None:
                    refresh_result = await dispatch_refresh()
                else:
                    refresh_result = await lock_guard.handoff_and_dispatch(
                        semantic_lock_handoff,
                        dispatch_refresh,
                        accepted=lambda value: (
                            isinstance(value, tuple)
                            and len(value) == 2
                            and bool(value[1])
                        ),
                    )
                if (
                    isinstance(refresh_result, tuple)
                    and len(refresh_result) == 2
                    and isinstance(refresh_result[0], dict)
                ):
                    result_updates, _ = refresh_result
                else:
                    result_updates = refresh_result
                checkpoint_result = deepcopy(msg.result)
                checkpoint_result.update(result_updates)
                if lock_guard.lease is not None and msg.lock_scope == "dispatch":
                    await lock_guard.close()
                checkpoint = await tracker.patch_task(
                    msg.task_id,
                    TaskPatch(
                        result=checkpoint_result,
                        add_enqueue_complete_work_ids=(
                            frozenset({metadata.work_id}) if metadata is not None else frozenset()
                        ),
                    ),
                    account_id=ctx.account_id,
                    user_id=ctx.user.user_id,
                )
                if not checkpoint.accepted:
                    raise RuntimeError(
                        f"Content write dispatch checkpoint rejected: {checkpoint.message or checkpoint.code.value}"
                    )
            else:
                logger.info(
                    "[ContentWrite] Resuming after completed dispatch task_id=%s work_id=%s",
                    msg.task_id,
                    metadata.work_id if metadata is not None else "",
                )
            await tracker.wait_for_task_work(
                msg.task_id,
                exclude_work_id=metadata.work_id if metadata is not None else None,
                account_id=ctx.account_id,
                user_id=ctx.user.user_id,
            )
            queue_status = await tracker.build_queue_status(
                msg.task_id,
                account_id=ctx.account_id,
                user_id=ctx.user.user_id,
            )
            result = deepcopy(checkpoint_result or msg.result)
            result.update(result_updates)
            result["queue_status"] = queue_status
            result.setdefault("task_id", msg.task_id)
            if msg.failure is not None:
                result["failure"] = deepcopy(msg.failure)
                await tracker.fail(
                    msg.task_id,
                    str(msg.failure.get("message") or "Content write failed"),
                    account_id=ctx.account_id,
                    user_id=ctx.user.user_id,
                    result=result,
                )
                logger.info(
                    "[ContentWrite] Failed task after refresh task_id=%s operation=%s "
                    "resource_id=%s",
                    msg.task_id,
                    msg.operation,
                    msg.resource_id,
                )
                return ProcessResult.success()
            await tracker.complete(
                msg.task_id,
                result,
                account_id=ctx.account_id,
                user_id=ctx.user.user_id,
                resource_id=msg.resource_id,
            )
            logger.info(
                "[ContentWrite] Completed task result task_id=%s operation=%s resource_id=%s",
                msg.task_id,
                msg.operation,
                msg.resource_id,
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
            if msg.lock_scope == "descendants" and lock_guard.lease is not None:
                await tracker.wait_for_task_work(
                    msg.task_id,
                    exclude_work_id=metadata.work_id if metadata is not None else None,
                    account_id=ctx.account_id,
                    user_id=ctx.user.user_id,
                )
            return ProcessResult.failed(str(exc))
        finally:
            await lock_guard.close()
            unregister_telemetry(telemetry_id)

    async def _execute_refresh(
        self,
        msg: ContentWriteMsg,
        ctx: RequestContext,
        *,
        semantic_lock_handoff: Dict[str, Any] | None = None,
    ) -> tuple[Dict[str, Any], bool]:
        refresh = msg.refresh
        kind = str(refresh.get("kind") or "none")
        if kind == "none":
            return {}, False

        from openviking.storage.content_write import ContentWriteCoordinator

        coordinator = ContentWriteCoordinator(self._viking_fs, self._vikingdb)
        if kind == "plan":
            plan = ContextUpdatePlan.from_dict(refresh["plan"])
            work = await commit_and_enqueue_plan(
                plan,
                ctx=ctx,
                semantic_lock_handoff=semantic_lock_handoff,
                ingest_options=IngestOptions.from_value(refresh.get("ingest_options")),
                file_created=bool(refresh.get("file_created", False)),
                force_refresh=bool(refresh.get("force_refresh", False)),
                generation_trigger="content_write",
            )
            semantic_status, vector_status = ContentWriteCoordinator._plan_statuses(
                work,
                wait=False,
                queue_status=None,
            )
            return (
                {"semantic_status": semantic_status, "vector_status": vector_status},
                semantic_lock_handoff is not None and work.semantic_requested,
            )
        if kind == "abstract_overview":
            vector_enqueued = await coordinator._vectorize_abstract_overview(
                uri=str(refresh["uri"]),
                ctx=ctx,
                ingest_options=IngestOptions.from_value(refresh.get("ingest_options")),
            )
            return (
                {
                    "vector_status": coordinator._queue_work_status(
                        requested=vector_enqueued,
                        wait=False,
                        queue_status=None,
                        queue_name="Embedding",
                    )
                },
                False,
            )
        if kind == "memory":
            root_uri = str(refresh["root_uri"])
            uri = str(refresh["uri"])
            overview_refreshed, embedding_requested = await coordinator._refresh_memory_after_write(
                root_uri=root_uri,
                uri=uri,
                ctx=ctx,
                ingest_options=IngestOptions.from_value(refresh.get("ingest_options")),
            )
            return (
                {
                    "overview_status": "complete" if overview_refreshed else "skipped",
                    "vector_status": coordinator._queue_work_status(
                        requested=embedding_requested,
                        wait=False,
                        queue_status=None,
                        queue_name="Embedding",
                    ),
                },
                False,
            )
        if kind == "batch":
            outcome = await coordinator._execute_batch_refresh_payload(refresh, ctx=ctx)
            semantic_status, vector_status = outcome.statuses(wait=False)
            return {"semantic_status": semantic_status, "vector_status": vector_status}, False
        raise ValueError(f"Unsupported content-write refresh kind: {kind}")
