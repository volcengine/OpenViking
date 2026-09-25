# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0
"""Queue consumer that owns one durable asynchronous reindex request."""

from __future__ import annotations

from typing import Any, Dict, Optional

from openviking.server.identity import RequestContext, Role
from openviking.service.task_tracker import get_task_tracker
from openviking.service.task_work_index import bind_task_context, extract_task_metadata
from openviking.storage.errors import ResourceBusyError
from openviking.storage.queuefs.named_queue import DequeueHandlerBase
from openviking.storage.queuefs.process_result import ProcessResult
from openviking.storage.queuefs.reindex_msg import ReindexMsg
from openviking_cli.session.user_id import UserIdentifier


class ReindexProcessor(DequeueHandlerBase):
    def __init__(self, viking_fs: Any) -> None:
        self._viking_fs = viking_fs

    @staticmethod
    def _ctx(msg: ReindexMsg) -> RequestContext:
        return RequestContext(
            user=UserIdentifier(account_id=msg.account_id, user_id=msg.user_id),
            role=Role(msg.role),
            group_ids=tuple(msg.group_ids),
        )

    async def _adopt_or_reacquire(self, msg: ReindexMsg, ctx: RequestContext) -> Dict[str, Any]:
        agfs = self._viking_fs._async_agfs
        if msg.lock_handoff is not None:
            try:
                return await agfs.pathlock_adopt(msg.lock_handoff)
            except Exception:
                pass
        stat = await self._viking_fs.stat(msg.uri, ctx=ctx, skip_count=True)
        acquire = agfs.pathlock_acquire_tree if stat.get("isDir", stat.get("is_dir")) else agfs.pathlock_acquire_exact
        return await acquire(self._viking_fs._uri_to_path(msg.uri, ctx=ctx))

    async def on_dequeue(self, data: Optional[Dict[str, Any]]) -> ProcessResult:
        if not data:
            return ProcessResult.success()
        payload = data.get("data", data)
        if isinstance(payload, str):
            import json

            payload = json.loads(payload)
        msg = ReindexMsg.from_dict(payload)
        ctx = self._ctx(msg)
        tracker = get_task_tracker()
        metadata = extract_task_metadata(data)
        lease = None
        try:
            lease = await self._adopt_or_reacquire(msg, ctx)
        except ResourceBusyError:
            return ProcessResult.requeued()
        try:
            await tracker.start(msg.task_id, account_id=ctx.account_id, user_id=ctx.user.user_id, stage="queued")
            from openviking.server.dependencies import get_service
            from openviking.service.reindex_executor import ReindexExecutor

            service = get_service()
            executor = ReindexExecutor(
                vlm_resolver=service._vlm_resolver,
                vector_config_resolver=service._vector_config_resolver,
            )
            with bind_task_context(msg.task_id, ctx.account_id, ctx.user.user_id):
                result = await executor._run(
                    uri=msg.uri,
                    object_type=msg.object_type,
                    mode=msg.mode,
                    force=msg.force,
                    recursive=msg.recursive,
                    ingest_options=executor._resolve_ingest_options(mode=msg.mode, tags=msg.tags, tag_mode=msg.tag_mode),
                    ctx=ctx,
                    existing_lease=lease,
                )
            lease = None
            if metadata is not None:
                await tracker.wait_for_descendants(msg.task_id, metadata.work_id)
            await tracker.complete(msg.task_id, result, account_id=ctx.account_id, user_id=ctx.user.user_id)
            return ProcessResult.success()
        except Exception as exc:
            await tracker.fail(msg.task_id, str(exc), account_id=ctx.account_id, user_id=ctx.user.user_id)
            return ProcessResult.failed(str(exc))
        finally:
            if lease is not None:
                await self._viking_fs._async_agfs.pathlock_release(lease)
