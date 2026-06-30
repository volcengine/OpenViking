# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0
"""UnderstandingParseProcessor: Processes UnderstandingParseMsg messages."""

import asyncio
import json

from contextlib import suppress
from pathlib import Path
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
        cleanup_local_path = msg.cleanup_local_path
        lock_lease = None
        try:
            from openviking.storage.transaction.lock_lease import LockHandoffRef, OwnedLockLease

            ref = LockHandoffRef.from_value(msg.lock_handoff)
            if ref is not None:
                lock_lease = await OwnedLockLease.from_handoff(ref)
        except Exception:
            lock_lease = None
        ctx = RequestContext(
            user=UserIdentifier(msg.account_id, msg.user_id),
            role=Role(msg.role),
            actor_peer_id=msg.actor_peer_id,
        )

        logger.info(
            "[UnderstandingParse] Dequeued task_id=%s root_uri=%s",
            msg.task_id,
            msg.root_uri,
        )

        task_tracker = get_task_tracker()
        resource_processor = self._resource_processor
        if resource_processor is None:
            raise RuntimeError("ResourceProcessor is None")

        telemetry = resolve_telemetry(msg.task_id)
        bind_telemetry(telemetry)

        with bind_execution_context():
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

                if not cleanup_local_path:
                    return
                try:
                    from openviking_cli.utils.config.open_viking_config import (
                        get_openviking_config,
                    )

                    cfg = get_openviking_config()
                    staging_root = (
                        Path(cfg.storage.workspace).expanduser().resolve()
                        / "temp"
                        / "external_parse"
                    )
                    cleanup_path = Path(cleanup_local_path).expanduser().resolve()
                    if cleanup_path.is_relative_to(staging_root):
                        with suppress(FileNotFoundError):
                            cleanup_path.unlink()
                except Exception:
                    return
