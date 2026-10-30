# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0
"""Durable, idempotent cleanup of accounts, users, and groups."""

from __future__ import annotations

import asyncio
import json
import time
from typing import Any, Optional
from uuid import uuid4

from openviking.core.namespace import canonical_user_root
from openviking.server.error_mapping import is_not_found_error
from openviking.server.identity import RequestContext, Role
from openviking.service.task_domain import TaskRecord
from openviking.service.task_store import (
    SYSTEM_TASK_ACCOUNT_ID,
    SYSTEM_TASK_USER_ID,
    TaskAlreadyActive,
)
from openviking.service.task_tracker import TaskStatus, get_task_tracker
from openviking.service.task_tracker_concurrency import OwnerLoopDispatcher, run_to_completion
from openviking.service.task_work_index import bind_task_context, extract_task_metadata
from openviking.storage.queuefs.named_queue import DequeueHandlerBase
from openviking.storage.queuefs.process_result import ProcessResult
from openviking.storage.viking_fs import LS_ALL_NODES
from openviking_cli.exceptions import AlreadyExistsError, NotFoundError
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
    target_group_id: str | None = None,
) -> dict[str, Any]:
    return {
        "task_id": task_id,
        "account_id": owner_account_id,
        "user_id": owner_user_id,
        "target": {
            "account_id": target_account_id,
            **(
                {"group_id": target_group_id}
                if target_group_id is not None
                else {"user_id": target_user_id}
            ),
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
        gateway_app: Any = None,
        require_registered_identity: bool = True,
    ) -> None:
        self._service = service
        self._manager = manager
        self._service_loop = service_loop
        self._manager_dispatcher = OwnerLoopDispatcher(service_loop)
        self._oauth_store = oauth_store
        self._usage_audit_runtime = usage_audit_runtime
        self._gateway_app = gateway_app
        self._require_registered_identity = require_registered_identity
        self._request_lock = asyncio.Lock()

    async def initialize(self) -> None:
        """Bind the queue consumer and reconcile persisted deletion fences."""
        self._service.viking_fs.set_deletion_guard(self._is_deleting)
        creation_guard = (
            self._creation_blocked if self._require_registered_identity else self._is_deleting
        )
        get_task_tracker().set_deletion_guard(creation_guard)
        queue_manager = self._service._queue_manager
        queue = queue_manager.get_queue(queue_manager.DATA_CLEANUP)
        queued_task_ids = {
            metadata.task_id
            for message in await queue.snapshot()
            if (metadata := extract_task_metadata(message)) is not None
        }
        queue.set_dequeue_handler(_DeletionProcessor(self, self._service_loop))

        tracker = get_task_tracker()
        for account_id, user_id, deletion in await self._manager.iter_deletions():
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
                with bind_task_context(task_id, owner_account_id, owner_user_id):
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

        # Group task metadata lives in the task store. Recover both queued work
        # lost after task creation and a group marked deleting before task creation.
        for task in await tracker.list_tasks(
            task_type="group_delete",
            account_id=SYSTEM_TASK_ACCOUNT_ID,
            user_id=SYSTEM_TASK_USER_ID,
            limit=None,
        ):
            if task.status in _ACTIVE_TASK_STATUSES:
                target = task.meta["target"]
                await self._enqueue_if_missing(
                    task_id=task.task_id,
                    owner_account_id=task.account_id,
                    owner_user_id=task.user_id,
                    target_account_id=target["account_id"],
                    target_user_id=None,
                    target_group_id=target["group_id"],
                )
        for account_id, group_id in await self._manager.iter_deleting_groups():
            await self._ensure_group_task(account_id, group_id, retry_failed=False)

    async def _latest_group_task(self, account_id: str, group_id: str) -> TaskRecord | None:
        tasks = await get_task_tracker().list_tasks(
            task_type="group_delete",
            resource_id=f"{account_id}/{group_id}",
            limit=1,
            account_id=SYSTEM_TASK_ACCOUNT_ID,
            user_id=SYSTEM_TASK_USER_ID,
        )
        return tasks[0] if tasks else None

    async def create_group(self, account_id: str, group_id: str) -> dict:
        async with self._request_lock:
            # The group row may already be gone while its worker is still finishing.
            # Reserve its ID until the task has settled, including after a restart.
            task = await self._latest_group_task(account_id, group_id)
            if task is not None and task.status != TaskStatus.COMPLETED:
                raise AlreadyExistsError(group_id, "group")
            return await self._manager.create_group(account_id, group_id)

    async def list_groups(self, account_id: str) -> list[dict]:
        groups = await self._manager.get_groups(account_id)
        for group in groups:
            if group.get("status") == "deleting":
                task = await self._latest_group_task(account_id, group["group_id"])
                if task is not None and task.status != TaskStatus.COMPLETED:
                    group["task_id"] = task.task_id
        return groups

    async def delete_group(self, account_id: str, group_id: str) -> dict[str, str]:
        async def submit():
            async with self._request_lock:
                try:
                    await self._manager.begin_group_deletion(account_id, group_id)
                except NotFoundError:
                    # Cleanup may have removed the group before persisting its
                    # final task result. A retry must settle that task as well.
                    previous = await self._latest_group_task(account_id, group_id)
                    if previous is None or previous.status == TaskStatus.COMPLETED:
                        raise
                task = await self._ensure_group_task(account_id, group_id, retry_failed=True)
                return {
                    "account_id": account_id,
                    "group_id": group_id,
                    "status": "deleting",
                    "task_id": task.task_id,
                }

        return await run_to_completion(submit)

    async def _ensure_group_task(
        self, account_id: str, group_id: str, *, retry_failed: bool
    ) -> TaskRecord:
        tracker = get_task_tracker()
        task = await self._latest_group_task(account_id, group_id)
        if (
            task is None
            or task.status == TaskStatus.COMPLETED
            or (retry_failed and task.status in _TERMINAL_TASK_STATUSES)
        ):
            try:
                task = await tracker.create(
                    "group_delete",
                    resource_id=f"{account_id}/{group_id}",
                    account_id=SYSTEM_TASK_ACCOUNT_ID,
                    user_id=SYSTEM_TASK_USER_ID,
                    meta={"target": {"account_id": account_id, "group_id": group_id}},
                    reject_if_active=True,
                )
            except TaskAlreadyActive:
                # Another server accepted the same deletion. Resolve the persisted
                # task instead of creating a second identity-side task reference.
                task = await self._latest_group_task(account_id, group_id)
                if task is None:
                    raise
        if task.status in _ACTIVE_TASK_STATUSES:
            await self._enqueue_if_missing(
                task_id=task.task_id,
                owner_account_id=task.account_id,
                owner_user_id=task.user_id,
                target_account_id=account_id,
                target_user_id=None,
                target_group_id=group_id,
            )
        return task

    async def _is_deleting(self, account_id: str, user_id: str | None = None) -> bool:
        deletion = await self._manager_dispatcher.run(
            lambda: self._manager.get_deletion(account_id, user_id)
        )
        return deletion is not None

    async def _creation_blocked(self, account_id: str, user_id: str) -> bool:
        """Reject missing registered identities, also after their deletion fence is gone."""
        if account_id == SYSTEM_TASK_ACCOUNT_ID:
            return False
        if await self._is_deleting(account_id, user_id):
            return True
        user = await self._manager.get_user(account_id, user_id)
        return user is None

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
        target_group_id: str | None = None,
    ) -> None:
        queue_manager = self._service._queue_manager
        queue = queue_manager.get_queue(queue_manager.DATA_CLEANUP)
        for message in await queue.snapshot():
            metadata = extract_task_metadata(message)
            if metadata is not None and metadata.task_id == task_id:
                return
        with bind_task_context(task_id, owner_account_id, owner_user_id):
            await queue.enqueue(
                _deletion_message(
                    task_id=task_id,
                    owner_account_id=owner_account_id,
                    owner_user_id=owner_user_id,
                    target_account_id=target_account_id,
                    target_user_id=target_user_id,
                    target_group_id=target_group_id,
                )
            )

    async def _process(self, message: dict[str, Any]) -> Optional[str]:
        if message["target"].get("group_id") is not None:
            return await self._process_group(message)
        task_id = message["task_id"]
        owner = {"account_id": message["account_id"], "user_id": message["user_id"]}
        account_id = message["target"]["account_id"]
        user_id = message["target"]["user_id"]
        scope = "account" if user_id is None else "user"
        tracker = get_task_tracker()
        deletion = await self._manager.get_deletion(account_id, user_id)
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
                await self._manager.has_user(account_id, user_id)
                if user_id is not None
                else await self._manager.get_account(account_id) is not None
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
            if self._gateway_app is not None:
                from openviking_gateway.app import delete_data

                await run_to_completion(lambda: delete_data(self._gateway_app, account_id, user_id))
            scheduler = self._service.watch_scheduler
            if scheduler is not None:
                await run_to_completion(lambda: scheduler.delete_tasks(account_id, user_id))
            await run_to_completion(lambda: self._cancel_tasks(account_id, user_id))
            if user_id is None:
                from openviking.ttl.wiring import get_ttl_adapter

                ttl = get_ttl_adapter()
                if ttl is not None:
                    await run_to_completion(lambda: ttl.delete_account(account_id))
            await run_to_completion(
                lambda: tracker.delete_tasks(
                    account_id,
                    user_id,
                    allow_active_types={"user_delete", "group_delete"} if user_id is None else (),
                )
            )

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
                await run_to_completion(
                    lambda: self._service.release_account_vector_resources(account_id)
                )
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

            fs = self._service.viking_fs
            if user_id is not None and fs.acl_manager is not None:
                lease = await fs._async_agfs.pathlock_acquire_tree(
                    fs._uri_to_path("viking://resources", ctx=ctx)
                )
                try:
                    await run_to_completion(
                        lambda: fs.acl_manager.remove_principal(f"user:{user_id}", ctx)
                    )
                finally:
                    await fs._async_agfs.pathlock_release(lease)

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

    async def _process_group(self, message: dict[str, Any]) -> Optional[str]:
        tracker = get_task_tracker()
        task_id = message["task_id"]
        owner = {"account_id": SYSTEM_TASK_ACCOUNT_ID, "user_id": SYSTEM_TASK_USER_ID}
        target = message["target"]
        account_id, group_id = target["account_id"], target["group_id"]
        task = await tracker.get(task_id, **owner)
        # An expired or terminal delivery must never act on a recreated group.
        if task is None or task.status in _TERMINAL_TASK_STATUSES:
            return None
        if task.task_type != "group_delete" or task.meta["target"] != target:
            raise ValueError("Group cleanup message does not match its task")
        fs = self._service.viking_fs
        ctx = RequestContext(user=UserIdentifier(account_id, SYSTEM_TASK_USER_ID), role=Role.ROOT)
        try:
            account = await self._manager.get_account(account_id)
            if account is None or account["status"] == "deleting":
                await tracker.complete(
                    task_id, {"deleted": account is None, "stale": True}, **owner
                )
                return None
            lease = await fs._async_agfs.pathlock_acquire_tree(
                fs._uri_to_path("viking://resources", ctx=ctx)
            )
            try:
                # Re-read after acquiring the resource lock: another delivery may
                # already have completed while this delivery was acquiring it.
                task = await tracker.get(task_id, **owner)
                if task is None or task.status in _TERMINAL_TASK_STATUSES:
                    return None
                groups = await self._manager.get_groups(account_id)
                group = next((item for item in groups if item["group_id"] == group_id), None)
                if group is None or group.get("status") != "deleting":
                    await tracker.complete(
                        task_id, {"deleted": group is None, "stale": True}, **owner
                    )
                    return None
                await tracker.start(task_id, **owner)
                if fs.acl_manager is not None:
                    await run_to_completion(
                        lambda: fs.acl_manager.remove_principal(f"group:{group_id}", ctx)
                    )
                deleted = await run_to_completion(
                    lambda: self._manager.finish_group_deletion(account_id, group_id)
                )
                if not deleted:
                    raise RuntimeError("Group is no longer pending deletion")
                await tracker.complete(task_id, {"deleted": True}, **owner)
            finally:
                await fs._async_agfs.pathlock_release(lease)
        except Exception as exc:
            logger.exception("Group cleanup failed for %s/%s", account_id, group_id)
            error = f"Group cleanup failed: {exc}"
            await tracker.fail(task_id, error, **owner)
            return error
        return None

    async def _delete_account_files(self, account_id: str) -> None:
        agfs = self._service.viking_fs._async_agfs
        path = f"/local/{account_id}"
        try:
            await agfs.rm(path, recursive=True, auto_pathlock=False)
        except Exception as exc:
            if not is_not_found_error(exc):
                raise
        try:
            await agfs.stat(path)
        except Exception as exc:
            if is_not_found_error(exc):
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
            and not (user_id is None and task.task_type in {"user_delete", "group_delete"})
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
            try:
                await tracker.wait(
                    task.task_id,
                    account_id=task.account_id,
                    user_id=task.user_id,
                    timeout=remaining,
                )
            except KeyError:
                # A Task created concurrently with this deletion withdraws itself.
                continue

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
        if not target.get("account_id"):
            raise ValueError("Invalid deletion account")
        if "group_id" in target:
            if not target["group_id"] or target.get("user_id") is not None:
                raise ValueError("Invalid deletion group")
            parsed_target = {
                "account_id": str(target["account_id"]),
                "group_id": str(target["group_id"]),
            }
        else:
            if "user_id" not in target or target["user_id"] == "":
                raise ValueError("Invalid deletion user")
            parsed_target = {
                "account_id": str(target["account_id"]),
                "user_id": str(target["user_id"]) if target["user_id"] is not None else None,
            }
        return {
            "task_id": str(payload["task_id"]),
            "account_id": str(payload["account_id"]),
            "user_id": str(payload["user_id"]),
            "target": parsed_target,
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
    gateway_app: Any = None,
    require_registered_identity: bool = True,
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
        gateway_app=gateway_app,
        require_registered_identity=require_registered_identity,
    )
    await deletion_service.initialize()
    return deletion_service
