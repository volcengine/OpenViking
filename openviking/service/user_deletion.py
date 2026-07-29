# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0
"""Persistent, idempotent user-data deletion."""

from __future__ import annotations

import asyncio
import json
import time
from typing import Any, Optional

from openviking.core.namespace import canonical_user_root
from openviking.server.identity import RequestContext, Role
from openviking.service.task_tracker import TaskStatus, get_task_tracker
from openviking.service.task_work_index import (
    TASK_ACCOUNT_ID_FIELD,
    TASK_USER_ID_FIELD,
    extract_task_metadata,
)
from openviking.storage.queuefs.named_queue import DequeueHandlerBase
from openviking.storage.viking_fs import LS_ALL_NODES
from openviking_cli.exceptions import NotFoundError
from openviking_cli.session.user_id import UserIdentifier
from openviking_cli.utils.logger import get_logger

logger = get_logger(__name__)

TASK_TYPE = "user_delete"
_CANCEL_WAIT_SECONDS = 30.0
_ACTIVE_TASK_STATUSES = (
    TaskStatus.PENDING,
    TaskStatus.RUNNING,
    TaskStatus.CANCELLING,
)


def deletion_message(
    *,
    task_id: str,
    owner_account_id: str,
    owner_user_id: str,
    account_id: str,
    user_id: str,
) -> dict[str, str]:
    return {
        "task_id": task_id,
        TASK_ACCOUNT_ID_FIELD: owner_account_id,
        TASK_USER_ID_FIELD: owner_user_id,
        "owner_account_id": owner_account_id,
        "owner_user_id": owner_user_id,
        "account_id": account_id,
        "user_id": user_id,
    }


class UserDeletionProcessor(DequeueHandlerBase):
    def __init__(
        self,
        service: Any,
        manager: Any,
        service_loop: asyncio.AbstractEventLoop,
        shared_upload_prefix: str,
    ):
        self._service = service
        self._manager = manager
        self._service_loop = service_loop
        self._shared_upload_prefix = shared_upload_prefix.rstrip("/")

    async def _cancel_user_tasks(self, account_id: str, user_id: str) -> None:
        tracker = get_task_tracker()
        active = [
            task
            for task in await tracker.list_tasks(
                account_id=account_id, user_id=user_id, limit=10_000
            )
            if task.status in _ACTIVE_TASK_STATUSES
        ]
        blockers = []
        for task in active:
            try:
                await tracker.cancel(task.task_id, account_id=account_id, user_id=user_id)
            except ValueError:
                blockers.append(f"{task.task_id}({task.task_type})")
        if blockers:
            raise RuntimeError("Active tasks do not support cancellation: " + ", ".join(blockers))

        deadline = time.monotonic() + _CANCEL_WAIT_SECONDS
        while time.monotonic() < deadline:
            remaining = [
                task
                for task in await tracker.list_tasks(
                    account_id=account_id,
                    user_id=user_id,
                    limit=10_000,
                )
                if task.status in _ACTIVE_TASK_STATUSES
            ]
            if not remaining:
                return
            await asyncio.sleep(0.1)
        raise RuntimeError(
            "Timed out cancelling active tasks: "
            + ", ".join(f"{task.task_id}({task.task_type})" for task in remaining)
        )

    async def _delete_watches(self, account_id: str, user_id: str) -> None:
        scheduler = self._service.watch_scheduler
        manager = scheduler.watch_manager if scheduler is not None else None
        if manager is None:
            return
        tasks = await manager.get_all_tasks(account_id, user_id, Role.USER)
        for task in tasks:
            await manager.delete_task(task.task_id, account_id, user_id, Role.USER)

    async def _delete_uploads(self, ctx: RequestContext) -> None:
        viking_fs = self._service.viking_fs
        try:
            uploads = await viking_fs.ls(
                self._shared_upload_prefix,
                show_all_hidden=True,
                node_limit=LS_ALL_NODES,
                ctx=ctx,
            )
        except NotFoundError:
            return
        for upload in uploads:
            uri = upload.get("uri") or f"{self._shared_upload_prefix}/{upload.get('name', '')}"
            if not uri.rstrip("/") or uri.rstrip("/") == self._shared_upload_prefix:
                continue
            try:
                meta = json.loads(
                    await viking_fs.read_file(f"{uri.rstrip('/')}/meta.json", ctx=ctx)
                )
            except Exception:
                continue
            if meta.get("account") == ctx.account_id and meta.get("user") == ctx.user.user_id:
                await viking_fs.rm(uri, recursive=True, ctx=ctx)

    async def _process(self, message: dict[str, str], raw: dict[str, Any]) -> None:
        tracker = get_task_tracker()
        task_id = message["task_id"]
        owner = {
            "account_id": message["owner_account_id"],
            "user_id": message["owner_user_id"],
        }
        target_account_id = message["account_id"]
        target_user_id = message["user_id"]
        task = await tracker.create(
            TASK_TYPE,
            resource_id=f"{target_account_id}/{target_user_id}",
            task_id=task_id,
            **owner,
        )
        deletion = self._manager.get_user_deletion(target_account_id, target_user_id)
        if not deletion or deletion.get("task_id") != task_id:
            await tracker.complete(task_id, **owner)
            self.report_success()
            return
        if task.status in (TaskStatus.COMPLETED, TaskStatus.FAILED, TaskStatus.CANCELLED):
            self.report_success()
            return

        try:
            await tracker.start(task_id, stage="stopping_watches", **owner)
            await self._delete_watches(target_account_id, target_user_id)
            await tracker.update_stage(task_id, "cancelling_tasks", **owner)
            await self._cancel_user_tasks(target_account_id, target_user_id)
            await tracker.update_stage(task_id, "cleaning", **owner)

            cleanup_ctx = RequestContext(
                user=UserIdentifier(target_account_id, target_user_id),
                role=Role.ROOT,
            )
            try:
                await self._service.viking_fs.rm(
                    canonical_user_root(cleanup_ctx),
                    recursive=True,
                    ctx=cleanup_ctx,
                )
            except NotFoundError:
                pass
            await self._delete_uploads(cleanup_ctx)
            removed = await self._manager.finish_user_deletion(
                target_account_id,
                target_user_id,
                task_id,
            )
        except Exception as exc:
            await tracker.fail(task_id, str(exc), **owner)
            self.report_error(str(exc), raw)
            logger.exception(
                "User deletion failed for %s/%s",
                target_account_id,
                target_user_id,
            )
            return

        if removed:
            # Leave the message unacked if persisting completion fails. On retry,
            # the missing fence takes the idempotent completion path above.
            await tracker.complete(task_id, **owner)
        self.report_success()

    async def on_dequeue(self, data: Optional[dict[str, Any]]) -> Optional[dict[str, Any]]:
        if not data:
            return None
        try:
            payload = data.get("data", data)
            if isinstance(payload, str):
                payload = json.loads(payload)
            required = {
                "task_id",
                "owner_account_id",
                "owner_user_id",
                "account_id",
                "user_id",
            }
            if not isinstance(payload, dict) or not required.issubset(payload):
                raise ValueError("Invalid user deletion message")
            message = {key: str(payload[key]) for key in required}
        except (json.JSONDecodeError, TypeError, ValueError) as exc:
            self.report_error(str(exc), data)
            return None

        future = asyncio.run_coroutine_threadsafe(
            self._process(message, data),
            self._service_loop,
        )
        try:
            await asyncio.wrap_future(future)
        except asyncio.CancelledError:
            future.cancel()
            raise
        return None


async def setup_user_deletion(
    service: Any,
    manager: Any,
    shared_upload_prefix: str = "viking://upload",
) -> None:
    """Bind the deletion fence, queue consumer, and startup reconciliation."""
    if manager is None or service.viking_fs is None or service._queue_manager is None:
        return
    service.viking_fs.set_user_deletion_guard(manager.is_user_deleting)
    queue = service._queue_manager.get_queue(
        service._queue_manager.USER_DELETION,
        dequeue_handler=UserDeletionProcessor(
            service,
            manager,
            asyncio.get_running_loop(),
            shared_upload_prefix,
        ),
        allow_create=True,
    )
    tracker = get_task_tracker()
    queued_task_ids = {
        metadata.task_id
        for message in await queue.snapshot()
        if (metadata := extract_task_metadata(message)) is not None
    }
    for account_id, user_id, deletion in manager.iter_user_deletions():
        task_id = deletion.get("task_id")
        owner_account_id = deletion.get("owner_account_id")
        owner_user_id = deletion.get("owner_user_id")
        if not task_id or not owner_account_id or not owner_user_id:
            continue
        task = await tracker.get(
            task_id,
            account_id=owner_account_id,
            user_id=owner_user_id,
        )
        if task is None:
            task = await tracker.create(
                TASK_TYPE,
                resource_id=f"{account_id}/{user_id}",
                task_id=task_id,
                account_id=owner_account_id,
                user_id=owner_user_id,
            )
        if task.status == TaskStatus.PENDING and task_id not in queued_task_ids:
            await queue.enqueue(
                deletion_message(
                    task_id=task_id,
                    owner_account_id=owner_account_id,
                    owner_user_id=owner_user_id,
                    account_id=account_id,
                    user_id=user_id,
                )
            )
            queued_task_ids.add(task_id)
