# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0
"""Durable, idempotent cleanup of accounts and users."""

from __future__ import annotations

import asyncio
import json
import time
from typing import Any, Optional
from uuid import uuid4

from openviking.core.namespace import canonical_user_root
from openviking.server.error_mapping import is_not_found_error
from openviking.server.identity import RequestContext, Role
from openviking.service.task_store import SYSTEM_TASK_ACCOUNT_ID, SYSTEM_TASK_USER_ID
from openviking.service.task_tracker import TaskStatus, get_task_tracker
from openviking.service.task_tracker_concurrency import OwnerLoopDispatcher, run_to_completion
from openviking.service.task_work_index import extract_task_metadata
from openviking.storage.queuefs.named_queue import DequeueHandlerBase
from openviking.storage.queuefs.process_result import ProcessResult
from openviking.storage.viking_fs import LS_ALL_NODES
from openviking_cli.exceptions import NotFoundError
from openviking_cli.session.user_id import UserIdentifier
from openviking_cli.utils.logger import get_logger

logger = get_logger(__name__)

_CANCEL_WAIT_SECONDS = 10 * 60
_SHARED_UPLOAD_ROOT = "viking://upload"
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
    target_user_id: str | None,
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


class DeletionService:
    """Own the deletion fence, tracked task, durable work, and cleanup pipeline."""

    def __init__(
        self,
        *,
        service: Any,
        manager: Any,
        service_loop: asyncio.AbstractEventLoop,
        oauth_store: Any = None,
        usage_audit_runtime: Any = None,
    ) -> None:
        self._service = service
        self._manager = manager
        self._service_loop = service_loop
        self._oauth_store = oauth_store
        self._usage_audit_runtime = usage_audit_runtime
        self._request_lock = asyncio.Lock()

    async def initialize(self) -> None:
        """Bind the queue consumer and reconcile persisted deletion fences."""
        self._service.viking_fs.set_deletion_guard(self._manager.is_deleting)
        queue_manager = self._service._queue_manager
        queue = queue_manager.get_queue(queue_manager.DATA_CLEANUP)
        queued_task_ids = {
            metadata.task_id
            for message in await queue.snapshot()
            if (metadata := extract_task_metadata(message)) is not None
        }
        queue.set_dequeue_handler(_DeletionProcessor(self, self._service_loop))

        tracker = get_task_tracker()
        for account_id, user_id, deletion in self._manager.iter_deletions():
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
                    "account_delete" if user_id is None else "user_delete",
                    resource_id=account_id if user_id is None else f"{account_id}/{user_id}",
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

    async def delete(
        self,
        account_id: str,
        user_id: str | None = None,
        *,
        actor: RequestContext,
    ) -> dict[str, str]:
        """Revoke the identity immediately and ensure one durable cleanup task exists."""
        # Request cancellation must not interrupt the fence/task/queue handoff.
        return await run_to_completion(lambda: self._submit(account_id, user_id, actor=actor))

    async def delete_now(
        self,
        account_id: str,
        *,
        actor: RequestContext,
    ) -> dict[str, str]:
        """Run the normal account deletion pipeline synchronously.

        Creation rollback needs the same vector, config, filesystem and identity
        cleanup as DELETE, but must settle it before returning the original
        creation error. If immediate cleanup fails, enqueue the same deletion
        message so the existing durable cleanup path can continue later.
        """
        result = await run_to_completion(
            lambda: self._submit(account_id, None, actor=actor, enqueue=False)
        )
        message = _deletion_message(
            task_id=result["task_id"],
            owner_account_id=SYSTEM_TASK_ACCOUNT_ID,
            owner_user_id=SYSTEM_TASK_USER_ID,
            target_account_id=account_id,
            target_user_id=None,
        )
        error = await run_to_completion(lambda: self._process(message))
        if error is not None:
            # _process has already marked this task failed. Submit again so
            # _submit replaces the terminal fence with a fresh queued task.
            await self.delete(account_id, actor=actor)
            raise RuntimeError(error)
        return result

    async def _submit(
        self,
        account_id: str,
        user_id: str | None,
        *,
        actor: RequestContext,
        enqueue: bool = True,
    ) -> dict[str, str]:
        if (
            user_id is None
            or actor.role == Role.ROOT
            or (actor.account_id == account_id and actor.user.user_id == user_id)
        ):
            request_owner_account_id = SYSTEM_TASK_ACCOUNT_ID
            request_owner_user_id = SYSTEM_TASK_USER_ID
        else:
            request_owner_account_id = actor.account_id
            request_owner_user_id = actor.user.user_id

        async with self._request_lock:
            task_id = str(uuid4())
            deletion, created = await self._manager.begin_deletion(
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
                    deletion = await self._manager.replace_deletion_task(
                        account_id,
                        user_id,
                        expected_task_id=task_id,
                        task_id=str(uuid4()),
                        owner_account_id=request_owner_account_id,
                        owner_user_id=request_owner_user_id,
                    )
                    if not deletion:
                        raise NotFoundError(user_id or account_id, "user" if user_id else "account")

            task_id = deletion["task_id"]
            owner_account_id = deletion["owner_account_id"]
            owner_user_id = deletion["owner_user_id"]
            await get_task_tracker().create(
                "account_delete" if user_id is None else "user_delete",
                resource_id=account_id if user_id is None else f"{account_id}/{user_id}",
                task_id=task_id,
                account_id=owner_account_id,
                user_id=owner_user_id,
            )
            if enqueue:
                await self._enqueue_if_missing(
                    task_id=task_id,
                    owner_account_id=owner_account_id,
                    owner_user_id=owner_user_id,
                    target_account_id=account_id,
                    target_user_id=user_id,
                )

        return {
            "account_id": account_id,
            **({"user_id": user_id} if user_id is not None else {}),
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
        target_user_id: str | None,
    ) -> None:
        queue_manager = self._service._queue_manager
        queue = queue_manager.get_queue(queue_manager.DATA_CLEANUP)
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
        owner = {"account_id": message["account_id"], "user_id": message["user_id"]}
        account_id = message["target"]["account_id"]
        user_id = message["target"]["user_id"]
        scope = "account" if user_id is None else "user"
        tracker = get_task_tracker()
        deletion = self._manager.get_deletion(account_id, user_id)
        if deletion is None or deletion["task_id"] != task_id:
            task = await tracker.get(task_id, **owner)
            # Account-owned records are removed with their account. A late
            # message must not recreate them, even if the ID has been reused.
            if task is None and owner["account_id"] != SYSTEM_TASK_ACCOUNT_ID:
                return None
        task = await tracker.create(
            f"{scope}_delete",
            resource_id=account_id if user_id is None else f"{account_id}/{user_id}",
            task_id=task_id,
            **owner,
        )
        if task.status in _TERMINAL_TASK_STATUSES:
            return None
        if deletion is None or deletion["task_id"] != task_id:
            exists = (
                self._manager.has_user(account_id, user_id)
                if user_id is not None
                else any(item["account_id"] == account_id for item in self._manager.get_accounts())
            )
            await tracker.complete(task_id, {"deleted": not exists, "stale": True}, **owner)
            return None

        ctx = RequestContext(
            user=UserIdentifier(account_id, user_id or SYSTEM_TASK_USER_ID), role=Role.ROOT
        )
        await tracker.start(task_id, **owner)
        try:
            # Settle each started operation before propagating cancellation;
            # the durable message can then resume cleanup after restart.
            scheduler = self._service.watch_scheduler
            if scheduler is not None:
                await run_to_completion(lambda: scheduler.delete_tasks(account_id, user_id))
            await run_to_completion(lambda: self._cancel_tasks(account_id, user_id))
            if user_id is not None:
                await run_to_completion(lambda: tracker.delete_user_tasks(account_id, user_id))

            vectors = self._service.viking_fs.vector_store
            if vectors is not None:
                if user_id is None:
                    await run_to_completion(
                        lambda: vectors.delete_account_data(account_id, ctx=ctx)
                    )
                else:
                    await run_to_completion(
                        lambda: vectors.delete_user_data(account_id, user_id, ctx=ctx)
                    )
            if self._oauth_store is not None:
                await run_to_completion(
                    lambda: self._oauth_store.revoke_tokens(account_id=account_id, user_id=user_id)
                )
            if self._usage_audit_runtime is not None:
                await run_to_completion(
                    lambda: self._usage_audit_runtime.delete_data(
                        account_id=account_id, user_id=user_id
                    )
                )
            if user_id is None:
                runtime_config = self._service.runtime_config_manager
                if runtime_config is not None:
                    await run_to_completion(lambda: runtime_config.delete_account(account_id))
                await run_to_completion(lambda: self._delete_account_files(account_id))
            else:
                await run_to_completion(lambda: self._delete_uploads(ctx))
                await run_to_completion(
                    lambda: self._service.viking_fs.rm(
                        canonical_user_root(ctx), recursive=True, ctx=ctx
                    )
                )

            deleted = await run_to_completion(
                lambda: self._manager.finish_deletion(account_id, user_id, task_id)
            )
            if deleted is not True:
                raise RuntimeError("Cleanup task no longer owns the identity deletion")
        except Exception as exc:
            logger.exception("%s cleanup failed for %s/%s", scope, account_id, user_id)
            error = f"{scope.capitalize()} cleanup failed: {exc}"
            await tracker.fail(task_id, error, **owner)
            return error
        await tracker.complete(task_id, {"deleted": True}, **owner)
        return None

    async def _delete_account_files(self, account_id: str) -> None:
        agfs = self._service.viking_fs._async_agfs
        path = f"/local/{account_id}"
        try:
            await agfs.rm(path, recursive=True)
        except Exception as exc:
            if not is_not_found_error(exc):
                raise
        try:
            await agfs.stat(path)
        except Exception as exc:
            if is_not_found_error(exc):
                get_task_tracker().forget_account_tasks(account_id)
                return
            raise
        raise RuntimeError("Account directory still exists after deletion")

    async def _cancel_tasks(self, account_id: str, user_id: str | None) -> None:
        tracker = get_task_tracker()
        tasks = await tracker.list_tasks(account_id=account_id, user_id=user_id, limit=None)
        active = [
            task
            for task in tasks
            if task.status in _ACTIVE_TASK_STATUSES
            # The single cleanup consumer cannot be executing a user cleanup
            # concurrently. Later deliveries will skip the deleted account.
            and not (user_id is None and task.task_type == "user_delete")
        ]
        deadline = time.monotonic() + _CANCEL_WAIT_SECONDS
        for task in active:
            owner = {"account_id": task.account_id, "user_id": task.user_id}
            try:
                await tracker.cancel(task.task_id, **owner)
            except ValueError:
                # Some system work cannot be cancelled. Wait for its owner to
                # finish instead of deleting the data underneath it.
                pass
        for task in active:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise TimeoutError("Timed out waiting for tasks to stop")
            await tracker.wait(
                task.task_id, account_id=task.account_id, user_id=task.user_id, timeout=remaining
            )

    async def _delete_uploads(self, ctx: RequestContext) -> None:
        viking_fs = self._service.viking_fs
        try:
            uploads = await viking_fs.ls(
                _SHARED_UPLOAD_ROOT,
                show_all_hidden=True,
                node_limit=LS_ALL_NODES,
                ctx=ctx,
            )
        except NotFoundError:
            return

        for upload in uploads:
            uri = str(upload.get("uri") or "").rstrip("/")
            if not uri or uri == _SHARED_UPLOAD_ROOT:
                continue
            upload_id = uri.removeprefix(f"{_SHARED_UPLOAD_ROOT}/")
            if (
                not upload.get("isDir")
                or not uri.startswith(f"{_SHARED_UPLOAD_ROOT}/")
                or not upload_id
                or "/" in upload_id
            ):
                continue
            meta_uri = f"{uri}/meta"
            try:
                meta = json.loads(await viking_fs.read_file(meta_uri, ctx=ctx))
            except Exception:
                logger.warning("Skipping shared upload with unreadable metadata: %s", meta_uri)
                continue
            if meta.get("account") == ctx.account_id and meta.get("user") == ctx.user.user_id:
                await viking_fs.rm(uri, recursive=True, ctx=ctx)


class _DeletionProcessor(DequeueHandlerBase):
    def __init__(
        self,
        deletion_service: DeletionService,
        service_loop: asyncio.AbstractEventLoop,
    ) -> None:
        self._deletion_service = deletion_service
        self._dispatcher = OwnerLoopDispatcher(service_loop)

    @staticmethod
    def _parse_message(data: dict[str, Any]) -> dict[str, Any]:
        payload = data.get("data", data)
        if isinstance(payload, str):
            payload = json.loads(payload)
        if not isinstance(payload, dict):
            raise ValueError("Invalid deletion message")
        target = payload.get("target")
        if not isinstance(target, dict):
            raise ValueError("Invalid deletion target")
        required = ("task_id", "account_id", "user_id")
        if any(not payload.get(field) for field in required):
            raise ValueError("Invalid deletion owner")
        if not target.get("account_id") or "user_id" not in target:
            raise ValueError("Invalid deletion target")
        if target["user_id"] is not None and not target["user_id"]:
            raise ValueError("Invalid deletion user")
        return {
            "task_id": str(payload["task_id"]),
            "account_id": str(payload["account_id"]),
            "user_id": str(payload["user_id"]),
            "target": {
                "account_id": str(target["account_id"]),
                "user_id": str(target["user_id"]) if target["user_id"] is not None else None,
            },
        }

    async def on_dequeue(self, data: Optional[dict[str, Any]]) -> ProcessResult:
        if not data:
            return ProcessResult.success()
        try:
            message = self._parse_message(data)
        except (json.JSONDecodeError, TypeError, ValueError) as exc:
            return ProcessResult.failed(str(exc))

        error = await self._dispatcher.run(lambda: self._deletion_service._process(message))
        return ProcessResult.success() if error is None else ProcessResult.failed(error)


async def setup_deletion(
    *,
    service: Any,
    manager: Any,
    oauth_store: Any = None,
    usage_audit_runtime: Any = None,
) -> Optional[DeletionService]:
    """Create the deletion service after storage and authentication are ready."""
    if manager is None or service.viking_fs is None or service._queue_manager is None:
        return None
    deletion_service = DeletionService(
        service=service,
        manager=manager,
        service_loop=asyncio.get_running_loop(),
        oauth_store=oauth_store,
        usage_audit_runtime=usage_audit_runtime,
    )
    await deletion_service.initialize()
    return deletion_service
