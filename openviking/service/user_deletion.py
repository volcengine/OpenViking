# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0
"""Durable, idempotent deletion of one user and their owned data."""

from __future__ import annotations

import asyncio
import json
import time
from typing import Any, Awaitable, Callable, Optional
from uuid import uuid4

from openviking.core.namespace import canonical_user_root
from openviking.server.identity import RequestContext, Role
from openviking.service.task_store import SYSTEM_TASK_ACCOUNT_ID, SYSTEM_TASK_USER_ID
from openviking.service.task_tracker import TaskStatus, get_task_tracker
from openviking.service.task_work_index import extract_task_metadata
from openviking.storage.queuefs.named_queue import DequeueHandlerBase
from openviking.storage.viking_fs import LS_ALL_NODES
from openviking_cli.exceptions import NotFoundError
from openviking_cli.session.user_id import UserIdentifier
from openviking_cli.utils.logger import get_logger

logger = get_logger(__name__)

TASK_TYPE = "user_delete"
_CANCEL_WAIT_SECONDS = 10 * 60
_ACTIVE_TASK_STATUSES = (
    TaskStatus.PENDING,
    TaskStatus.RUNNING,
    TaskStatus.CANCELLING,
)
_TERMINAL_TASK_STATUSES = (
    TaskStatus.COMPLETED,
    TaskStatus.FAILED,
    TaskStatus.CANCELLED,
)


def _deletion_message(
    *,
    task_id: str,
    owner_account_id: str,
    owner_user_id: str,
    target_account_id: str,
    target_user_id: str,
) -> dict[str, Any]:
    return {
        "task_id": task_id,
        "account_id": owner_account_id,
        "user_id": owner_user_id,
        "target": {
            "account_id": target_account_id,
            "user_id": target_user_id,
        },
    }


class UserDeletionService:
    """Own the deletion fence, tracked task, durable work, and cleanup pipeline."""

    def __init__(
        self,
        *,
        service: Any,
        manager: Any,
        service_loop: asyncio.AbstractEventLoop,
        shared_upload_prefix: str,
        oauth_store: Any = None,
        usage_audit_runtime: Any = None,
    ) -> None:
        self._service = service
        self._manager = manager
        self._service_loop = service_loop
        self._shared_upload_prefix = shared_upload_prefix.rstrip("/")
        self._oauth_store = oauth_store
        self._usage_audit_runtime = usage_audit_runtime
        self._request_lock = asyncio.Lock()

    async def initialize(self) -> None:
        """Bind the queue consumer and reconcile persisted deletion fences."""
        self._service.viking_fs.set_user_deletion_guard(self._manager.is_user_deleting)
        queue_manager = self._service._queue_manager
        queue = queue_manager.get_queue(queue_manager.USER_DELETION)
        queued_task_ids = {
            metadata.task_id
            for message in await queue.snapshot()
            if (metadata := extract_task_metadata(message)) is not None
        }
        queue_manager.get_queue(
            queue_manager.USER_DELETION,
            dequeue_handler=_UserDeletionProcessor(self, self._service_loop),
        )

        tracker = get_task_tracker()
        for account_id, user_id, deletion in self._manager.iter_user_deletions():
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
            if task.status in (TaskStatus.PENDING, TaskStatus.RUNNING) and task_id not in (
                queued_task_ids
            ):
                await queue.enqueue(
                    _deletion_message(
                        task_id=task_id,
                        owner_account_id=owner_account_id,
                        owner_user_id=owner_user_id,
                        target_account_id=account_id,
                        target_user_id=user_id,
                    )
                )
                queued_task_ids.add(task_id)

    async def delete_user(
        self,
        account_id: str,
        user_id: str,
        *,
        actor: RequestContext,
    ) -> dict[str, str]:
        """Revoke the user immediately and ensure one durable cleanup task exists."""
        if actor.role == Role.ROOT or (
            actor.account_id == account_id and actor.user.user_id == user_id
        ):
            request_owner_account_id = SYSTEM_TASK_ACCOUNT_ID
            request_owner_user_id = SYSTEM_TASK_USER_ID
        else:
            request_owner_account_id = actor.account_id
            request_owner_user_id = actor.user.user_id

        async with self._request_lock:
            task_id = str(uuid4())
            deletion, created = await self._manager.begin_user_deletion(
                account_id,
                user_id,
                task_id=task_id,
                owner_account_id=request_owner_account_id,
                owner_user_id=request_owner_user_id,
            )
            if not created:
                task_id = deletion["task_id"]
                owner_account_id = deletion["owner_account_id"]
                owner_user_id = deletion["owner_user_id"]
                existing = await get_task_tracker().get(
                    task_id,
                    account_id=owner_account_id,
                    user_id=owner_user_id,
                )
                if existing is not None and existing.status in _TERMINAL_TASK_STATUSES:
                    deletion = await self._manager.replace_user_deletion_task(
                        account_id,
                        user_id,
                        expected_task_id=task_id,
                        task_id=str(uuid4()),
                        owner_account_id=request_owner_account_id,
                        owner_user_id=request_owner_user_id,
                    )
                    if not deletion:
                        raise NotFoundError(user_id, "user")

            task_id = deletion["task_id"]
            owner_account_id = deletion["owner_account_id"]
            owner_user_id = deletion["owner_user_id"]
            await get_task_tracker().create(
                TASK_TYPE,
                resource_id=f"{account_id}/{user_id}",
                task_id=task_id,
                account_id=owner_account_id,
                user_id=owner_user_id,
            )
            await self._enqueue_if_missing(
                task_id=task_id,
                owner_account_id=owner_account_id,
                owner_user_id=owner_user_id,
                target_account_id=account_id,
                target_user_id=user_id,
            )

        return {
            "account_id": account_id,
            "user_id": user_id,
            "status": "deleting",
            "task_id": task_id,
        }

    async def _enqueue_if_missing(
        self,
        *,
        task_id: str,
        owner_account_id: str,
        owner_user_id: str,
        target_account_id: str,
        target_user_id: str,
    ) -> None:
        queue_manager = self._service._queue_manager
        queue = queue_manager.get_queue(queue_manager.USER_DELETION)
        for message in await queue.snapshot():
            metadata = extract_task_metadata(message)
            if metadata is not None and metadata.task_id == task_id:
                return
        await queue.enqueue(
            _deletion_message(
                task_id=task_id,
                owner_account_id=owner_account_id,
                owner_user_id=owner_user_id,
                target_account_id=target_account_id,
                target_user_id=target_user_id,
            )
        )

    async def _process(self, message: dict[str, Any]) -> Optional[str]:
        task_id = message["task_id"]
        owner_account_id = message["account_id"]
        owner_user_id = message["user_id"]
        target_account_id = message["target"]["account_id"]
        target_user_id = message["target"]["user_id"]
        tracker = get_task_tracker()
        task = await tracker.create(
            TASK_TYPE,
            resource_id=f"{target_account_id}/{target_user_id}",
            task_id=task_id,
            account_id=owner_account_id,
            user_id=owner_user_id,
        )
        owner = {
            "account_id": owner_account_id,
            "user_id": owner_user_id,
        }
        if task.status in _TERMINAL_TASK_STATUSES:
            return None

        deletion = self._manager.get_user_deletion(target_account_id, target_user_id)
        if deletion is None or deletion.get("task_id") != task_id:
            deleted = not self._manager.has_user(target_account_id, target_user_id)
            await tracker.complete(
                task_id,
                {"deleted": deleted, "stale": True},
                **owner,
            )
            return None

        try:
            await tracker.start(task_id, stage="stopping_watches", **owner)
            await self._stop_watches(target_account_id, target_user_id)
        except Exception:
            logger.exception(
                "Failed to stop watches for user %s/%s",
                target_account_id,
                target_user_id,
            )
            error = "User cleanup failed at stages: watches"
            await tracker.fail(task_id, error, **owner)
            return error

        try:
            await tracker.update_stage(task_id, "cancelling_tasks", **owner)
            await self._cancel_user_tasks(target_account_id, target_user_id)
        except Exception:
            logger.exception(
                "Failed to settle tasks for user %s/%s",
                target_account_id,
                target_user_id,
            )
            error = "User cleanup failed at stages: tasks"
            await tracker.fail(task_id, error, **owner)
            return error

        cleanup_ctx = RequestContext(
            user=UserIdentifier(target_account_id, target_user_id),
            role=Role.ROOT,
        )
        steps: list[tuple[str, Callable[[], Awaitable[Any]]]] = [
            (
                "task_records",
                lambda: tracker.delete_user_tasks(target_account_id, target_user_id),
            ),
            (
                "agfs",
                lambda: self._service.viking_fs.rm(
                    canonical_user_root(cleanup_ctx),
                    recursive=True,
                    ctx=cleanup_ctx,
                ),
            ),
            (
                "uploads",
                lambda: self._delete_uploads(cleanup_ctx),
            ),
        ]
        vector_store = self._service.viking_fs.vector_store
        if vector_store is not None:
            steps.insert(
                1,
                (
                    "vectordb",
                    lambda: vector_store.delete_user_data(
                        target_account_id,
                        target_user_id,
                        ctx=cleanup_ctx,
                    ),
                ),
            )
        if self._oauth_store is not None:
            steps.append(
                (
                    "oauth",
                    lambda: self._oauth_store.revoke_user_tokens(
                        account_id=target_account_id,
                        user_id=target_user_id,
                    ),
                )
            )
        if self._usage_audit_runtime is not None:
            steps.append(
                (
                    "usage_audit",
                    lambda: self._usage_audit_runtime.delete_user_data(
                        account_id=target_account_id,
                        user_id=target_user_id,
                    ),
                )
            )

        failed_stages: list[str] = []
        for stage, cleanup in steps:
            await tracker.update_stage(task_id, f"cleaning_{stage}", **owner)
            try:
                await cleanup()
            except Exception:
                failed_stages.append(stage)
                logger.exception(
                    "User cleanup stage %s failed for %s/%s",
                    stage,
                    target_account_id,
                    target_user_id,
                )

        if failed_stages:
            error = "User cleanup failed at stages: " + ", ".join(failed_stages)
            await tracker.fail(task_id, error, **owner)
            return error

        await tracker.update_stage(task_id, "removing_user", **owner)
        try:
            removed = await self._manager.finish_user_deletion(
                target_account_id,
                target_user_id,
                task_id,
            )
        except Exception:
            logger.exception(
                "Failed to remove user registry entry for %s/%s",
                target_account_id,
                target_user_id,
            )
            error = "User cleanup failed at stages: registry"
            await tracker.fail(task_id, error, **owner)
            return error

        await tracker.complete(
            task_id,
            {"deleted": removed, "stale": not removed},
            **owner,
        )
        return None

    async def _stop_watches(self, account_id: str, user_id: str) -> None:
        scheduler = self._service.watch_scheduler
        manager = scheduler.watch_manager if scheduler is not None else None
        if manager is None:
            return
        for task in await manager.get_all_tasks(account_id, user_id, Role.USER):
            await manager.delete_task(task.task_id, account_id, user_id, Role.USER)

    async def _cancel_user_tasks(self, account_id: str, user_id: str) -> None:
        tracker = get_task_tracker()
        active = [
            task
            for task in await tracker.list_tasks(
                account_id=account_id,
                user_id=user_id,
                limit=10_000,
            )
            if task.status in _ACTIVE_TASK_STATUSES
        ]
        blockers: list[str] = []
        waiting: list[str] = []
        for task in active:
            try:
                await tracker.cancel(
                    task.task_id,
                    account_id=account_id,
                    user_id=user_id,
                )
                waiting.append(task.task_id)
            except ValueError:
                current = await tracker.get(
                    task.task_id,
                    account_id=account_id,
                    user_id=user_id,
                )
                if current is not None and current.status in _ACTIVE_TASK_STATUSES:
                    blockers.append(f"{current.task_id}({current.task_type})")
        if blockers:
            raise RuntimeError("Active tasks do not support cancellation: " + ", ".join(blockers))

        deadline = time.monotonic() + _CANCEL_WAIT_SECONDS
        for task_id in waiting:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise TimeoutError("Timed out waiting for user tasks to stop")
            await tracker.wait(
                task_id,
                account_id=account_id,
                user_id=user_id,
                timeout=remaining,
            )

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
            uri = uri.rstrip("/")
            if not uri or uri == self._shared_upload_prefix:
                continue
            try:
                meta = json.loads(await viking_fs.read_file(f"{uri}/meta.json", ctx=ctx))
            except Exception:
                logger.warning("Skipping shared upload with unreadable metadata: %s", uri)
                continue
            if meta.get("account") == ctx.account_id and meta.get("user") == ctx.user.user_id:
                await viking_fs.rm(uri, recursive=True, ctx=ctx)


class _UserDeletionProcessor(DequeueHandlerBase):
    def __init__(
        self,
        deletion_service: UserDeletionService,
        service_loop: asyncio.AbstractEventLoop,
    ) -> None:
        self._deletion_service = deletion_service
        self._service_loop = service_loop

    @staticmethod
    def _parse_message(data: dict[str, Any]) -> dict[str, Any]:
        payload = data.get("data", data)
        if isinstance(payload, str):
            payload = json.loads(payload)
        if not isinstance(payload, dict):
            raise ValueError("Invalid user deletion message")
        target = payload.get("target")
        if not isinstance(target, dict):
            raise ValueError("Invalid user deletion target")
        required = ("task_id", "account_id", "user_id")
        if any(not payload.get(field) for field in required):
            raise ValueError("Invalid user deletion owner")
        if not target.get("account_id") or not target.get("user_id"):
            raise ValueError("Invalid user deletion target")
        return {
            "task_id": str(payload["task_id"]),
            "account_id": str(payload["account_id"]),
            "user_id": str(payload["user_id"]),
            "target": {
                "account_id": str(target["account_id"]),
                "user_id": str(target["user_id"]),
            },
        }

    async def on_dequeue(self, data: Optional[dict[str, Any]]) -> Optional[dict[str, Any]]:
        if not data:
            return None
        try:
            message = self._parse_message(data)
        except (json.JSONDecodeError, TypeError, ValueError) as exc:
            self.report_error(str(exc), data)
            return None

        future = asyncio.run_coroutine_threadsafe(
            self._deletion_service._process(message),
            self._service_loop,
        )
        try:
            error = await asyncio.wrap_future(future)
        except asyncio.CancelledError:
            future.cancel()
            raise
        if error is None:
            self.report_success()
        else:
            self.report_error(error, data)
        return None


async def setup_user_deletion(
    *,
    service: Any,
    manager: Any,
    shared_upload_prefix: str,
    oauth_store: Any = None,
    usage_audit_runtime: Any = None,
) -> Optional[UserDeletionService]:
    """Create the deletion service after storage and authentication are ready."""
    if manager is None or service.viking_fs is None or service._queue_manager is None:
        return None
    deletion_service = UserDeletionService(
        service=service,
        manager=manager,
        service_loop=asyncio.get_running_loop(),
        shared_upload_prefix=shared_upload_prefix,
        oauth_store=oauth_store,
        usage_audit_runtime=usage_audit_runtime,
    )
    await deletion_service.initialize()
    return deletion_service
