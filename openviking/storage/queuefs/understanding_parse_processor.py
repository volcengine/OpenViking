# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0
"""UnderstandingParseProcessor: Processes UnderstandingParseMsg messages."""

import asyncio
import json

from contextlib import suppress
from typing import Any, Dict, Optional

from openviking.observability.context import (
    bind_execution_context,
)
from openviking.server.identity import RequestContext, Role
from openviking.service.task_tracker import get_task_tracker
from openviking.storage.queuefs.named_queue import DequeueHandlerBase
from openviking.storage.queuefs.understanding_parse_msg import UnderstandingParseMsg
from openviking.telemetry import bind_telemetry, bind_telemetry_stage, resolve_telemetry
from openviking_cli.session.user_id import UserIdentifier
from openviking_cli.utils.logger import get_logger

logger = get_logger(__name__)


class UnderstandingParseProcessor(DequeueHandlerBase):
    def __init__(self, resource_processor: Any, resource_memory_link_service: Any = None):
        self._resource_processor = resource_processor
        self._resource_memory_link_service = resource_memory_link_service
        self._background_tasks: set[asyncio.Task] = set()

    async def _monitor_queue_then_link_memory(
        self,
        *,
        task_id: str,
        telemetry_id: str,
        ctx: RequestContext,
        result: Dict[str, Any],
        reason: str,
        source_name: Optional[str],
    ) -> None:
        from openviking.telemetry.resource_summary import unregister_wait_telemetry
        from openviking.telemetry.request_wait_tracker import get_request_wait_tracker

        task_tracker = get_task_tracker()
        request_wait_tracker = get_request_wait_tracker()
        try:
            request_wait_tracker.register_request(telemetry_id)
            await request_wait_tracker.wait_for_request(telemetry_id)
            status = request_wait_tracker.build_queue_status(telemetry_id)
            errors = sum(int(group.get("error_count", 0) or 0) for group in status.values())
            if errors:
                await task_tracker.fail(
                    task_id,
                    f"queue processing failed: {status}",
                    account_id=ctx.account_id,
                    user_id=ctx.user.user_id,
                )
                self.report_error("queue processing failed", {"task_id": task_id})
                return

            result["queue_status"] = status
            if self._resource_memory_link_service and (reason or "").strip():
                root_uri = result.get("root_uri")
                if root_uri:
                    try:
                        link_result = await self._resource_memory_link_service.on_resource_added(
                            ctx=ctx,
                            resource_uri=root_uri,
                            reason=reason,
                            source_name=source_name,
                            timeout=None,
                        )
                        result["memory_linking"] = link_result
                    except Exception as exc:
                        logger.warning(
                            "[UnderstandingParse] Failed to link resource reason memory: %s",
                            exc,
                        )
                        result.setdefault("warnings", []).append(f"Memory linking failed: {exc}")

            await task_tracker.complete(
                task_id,
                result,
                account_id=ctx.account_id,
                user_id=ctx.user.user_id,
            )
            self.report_success()
        except Exception as exc:
            await task_tracker.fail(
                task_id,
                str(exc),
                account_id=ctx.account_id,
                user_id=ctx.user.user_id,
            )
            self.report_error(str(exc), {"task_id": task_id})
        finally:
            request_wait_tracker.cleanup(telemetry_id)
            unregister_wait_telemetry(telemetry_id)

    async def on_dequeue(self, data: Optional[Dict[str, Any]]) -> Optional[Dict[str, Any]]:
        if not data:
            return None

        payload: Dict[str, Any] = data
        if isinstance(data, dict) and "data" in data:
            inner = data.get("data")
            if isinstance(inner, str) and inner.strip():
                try:
                    decoded = json.loads(inner)
                    if isinstance(decoded, dict):
                        payload = decoded
                except Exception:
                    payload = data
            elif isinstance(inner, dict):
                payload = inner
            if isinstance(payload, dict) and data.get("id") and not payload.get("id"):
                payload["id"] = data["id"]

        msg = UnderstandingParseMsg.from_dict(payload)
        ctx = RequestContext(
            user=UserIdentifier(msg.account_id, msg.user_id),
            role=Role(msg.role),
            actor_peer_id=msg.actor_peer_id,
        )

        task_tracker = get_task_tracker()
        lock_lease = None
        if msg.lock_handoff is not None:
            try:
                from openviking.storage.transaction.lock_lease import (
                    LockHandoffRef,
                    OwnedLockLease,
                )

                ref = LockHandoffRef.from_value(msg.lock_handoff)
                if ref is None:
                    raise ValueError("Invalid lock_handoff")
                lock_lease = await OwnedLockLease.from_handoff(ref)
            except Exception as exc:
                retry_key = "_lock_handoff_retry"
                raw_retry = (msg.args or {}).get(retry_key, 0)
                try:
                    retry_count = int(raw_retry or 0)
                except (TypeError, ValueError):
                    retry_count = 0

                max_retries = 2
                if retry_count < max_retries:
                    from openviking.storage.queuefs import QueueManager, get_queue_manager

                    qm = get_queue_manager()
                    if qm is None:
                        raise
                    new_args = dict(msg.args or {})
                    new_args[retry_key] = retry_count + 1
                    retry_msg = UnderstandingParseMsg(
                        task_id=msg.task_id,
                        telemetry_id=msg.telemetry_id,
                        path=msg.path,
                        root_uri=msg.root_uri,
                        account_id=msg.account_id,
                        user_id=msg.user_id,
                        role=msg.role,
                        actor_peer_id=msg.actor_peer_id,
                        lock_handoff=msg.lock_handoff,
                        reason=msg.reason,
                        instruction=msg.instruction,
                        build_index=msg.build_index,
                        summarize=msg.summarize,
                        strict=msg.strict,
                        ignore_dirs=msg.ignore_dirs,
                        include=msg.include,
                        exclude=msg.exclude,
                        directly_upload_media=msg.directly_upload_media,
                        allow_local_path_resolution=msg.allow_local_path_resolution,
                        enforce_public_remote_targets=msg.enforce_public_remote_targets,
                        args=new_args,
                        source_name=msg.source_name,
                    )
                    await qm.enqueue(QueueManager.EXTERNAL_PARSE, retry_msg.to_dict())
                    self.report_requeue()
                    self.report_success()
                    return None

                await task_tracker.fail(
                    msg.task_id,
                    f"Invalid lock_handoff: {exc}",
                    account_id=ctx.account_id,
                    user_id=ctx.user.user_id,
                )
                self.report_error(f"Invalid lock_handoff: {exc}", data)
                return None

        logger.info(
            "[UnderstandingParse] Dequeued task_id=%s root_uri=%s",
            msg.task_id,
            msg.root_uri,
        )
        resource_processor = self._resource_processor
        if resource_processor is None:
            raise RuntimeError("ResourceProcessor is None")

        telemetry_id = msg.telemetry_id or ""
        if telemetry_id:
            from openviking.telemetry.request_wait_tracker import get_request_wait_tracker

            get_request_wait_tracker().register_request(telemetry_id)

        telemetry = resolve_telemetry(telemetry_id) if telemetry_id else None
        if telemetry_id and telemetry is None:
            from openviking.telemetry.operation import OperationTelemetry

            telemetry = OperationTelemetry(operation="noop", enabled=False)
            telemetry.telemetry_id = telemetry_id

        with bind_execution_context(), (bind_telemetry(telemetry) if telemetry else suppress()):
            try:
                await task_tracker.start(
                    msg.task_id, account_id=ctx.account_id, user_id=ctx.user.user_id
                )
                await task_tracker.update_stage(
                    msg.task_id,
                    "parsing",
                    account_id=ctx.account_id,
                    user_id=ctx.user.user_id,
                )

                bind_telemetry_stage("parse_understanding")
                kwargs: Dict[str, Any] = {}
                if lock_lease is not None and getattr(lock_lease, "active", False):
                    kwargs["resource_lock"] = lock_lease
                if msg.enforce_public_remote_targets:
                    from openviking.utils.network_guard import ensure_public_remote_target

                    kwargs.setdefault("request_validator", ensure_public_remote_target)
                result = await resource_processor.process_resource(
                    path=msg.path,
                    ctx=ctx,
                    reason=msg.reason,
                    instruction=msg.instruction,
                    scope="resources",
                    to=msg.root_uri,
                    parent=None,
                    build_index=msg.build_index,
                    summarize=msg.summarize,
                    allow_local_path_resolution=msg.allow_local_path_resolution,
                    enforce_public_remote_targets=msg.enforce_public_remote_targets,
                    strict=msg.strict,
                    source_name=msg.source_name,
                    ignore_dirs=msg.ignore_dirs,
                    include=msg.include,
                    exclude=msg.exclude,
                    directly_upload_media=msg.directly_upload_media,
                    args=msg.args,
                    skip_watch_management=True,
                    watch_interval=0,
                    **kwargs,
                )

                if result.get("status") == "error":
                    errors = result.get("errors") or ["resource processing failed"]
                    await task_tracker.fail(
                        msg.task_id,
                        "; ".join(str(error) for error in errors),
                        account_id=ctx.account_id,
                        user_id=ctx.user.user_id,
                    )
                    self.report_error("resource processing failed", data)
                    return None

                if telemetry_id:
                    await task_tracker.update_stage(
                        msg.task_id,
                        "processing_queue",
                        account_id=ctx.account_id,
                        user_id=ctx.user.user_id,
                    )
                    monitor = asyncio.create_task(
                        self._monitor_queue_then_link_memory(
                            task_id=msg.task_id,
                            telemetry_id=telemetry_id,
                            ctx=ctx,
                            result=result,
                            reason=msg.reason,
                            source_name=msg.source_name,
                        )
                    )
                    self._background_tasks.add(monitor)
                    monitor.add_done_callback(self._background_tasks.discard)
                    return None

                if self._resource_memory_link_service and (msg.reason or "").strip():
                    root_uri = result.get("root_uri")
                    if root_uri:
                        try:
                            link_result = await self._resource_memory_link_service.on_resource_added(
                                ctx=ctx,
                                resource_uri=root_uri,
                                reason=msg.reason,
                                source_name=msg.source_name,
                                timeout=None,
                            )
                            result["memory_linking"] = link_result
                        except Exception as exc:
                            logger.warning(
                                "[UnderstandingParse] Failed to link resource reason memory: %s",
                                exc,
                            )
                            result.setdefault("warnings", []).append(
                                f"Memory linking failed: {exc}"
                            )

                await task_tracker.complete(
                    msg.task_id,
                    result,
                    account_id=ctx.account_id,
                    user_id=ctx.user.user_id,
                )
                self.report_success()
                return None
            except asyncio.CancelledError:
                await task_tracker.fail(
                    msg.task_id,
                    "external parse cancelled",
                    account_id=ctx.account_id,
                    user_id=ctx.user.user_id,
                )
                self.report_error("external parse cancelled", data)
                raise
            except Exception as exc:
                await task_tracker.fail(
                    msg.task_id,
                    str(exc),
                    account_id=ctx.account_id,
                    user_id=ctx.user.user_id,
                )
                self.report_error(str(exc), data)
                return None
            finally:
                try:
                    if lock_lease is not None:
                        await lock_lease.close()
                except Exception:
                    pass
