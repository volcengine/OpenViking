# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0
"""Task execution and temporary download storage for OVPack."""

from __future__ import annotations

import asyncio
import json
import tempfile
import time
from contextlib import suppress
from pathlib import Path
from typing import TYPE_CHECKING, Any, AsyncIterator, Awaitable, Callable, Iterable
from zipfile import BadZipFile

from openviking.pyagfs.exceptions import AGFSNotFoundError
from openviking.server.dependencies import get_server_config
from openviking.server.error_mapping import map_exception
from openviking.server.identity import RequestContext, Role
from openviking.server.temp_upload_store import TempUploadStore
from openviking.service.task_domain import DeletedTask, TaskPatch, TaskStatus
from openviking.service.task_tracker import get_task_tracker
from openviking.service.task_tracker_concurrency import run_to_completion
from openviking.service.task_work_index import bind_task_context, extract_task_metadata
from openviking.storage.ovpack.policy import validate_export_source_uri, validate_public_scope
from openviking.storage.pathlock_transfer import PathLockGuard
from openviking.storage.queuefs.named_queue import DequeueHandlerBase
from openviking.storage.queuefs.process_result import ProcessResult
from openviking.storage.queuefs.queue_manager import QueueManager, get_queue_manager
from openviking_cli.exceptions import (
    FailedPreconditionError,
    InvalidArgumentError,
    NotFoundError,
    PermissionDeniedError,
)
from openviking_cli.session.user_id import UserIdentifier
from openviking_cli.utils import get_logger

if TYPE_CHECKING:
    from openviking.service.pack_service import PackService

logger = get_logger(__name__)
_CHUNK_SIZE = 8 * 1024 * 1024
_ARCHIVE_TASK_TYPES = {"pack_export", "pack_backup"}
_UPLOAD_OPERATIONS = {"import", "restore"}


class PackTasks(DequeueHandlerBase):
    def __init__(self, pack: PackService) -> None:
        self._pack = pack
        self.resolve_role: Callable[[str, str], Awaitable[Role | None]] | None = None
        self.resolve_groups: Callable[[str, str], Awaitable[Iterable[str]]] | None = None

    async def submit(
        self, operation: str, args: dict[str, Any], ctx: RequestContext
    ) -> dict[str, str]:
        if operation in _UPLOAD_OPERATIONS:
            if operation == "import":
                validate_public_scope(args["parent"], operation="import", allow_root=True)
        elif operation == "export":
            validate_export_source_uri(args["uri"])
            await self._pack._ensure_initialized().stat(args["uri"], ctx=ctx, skip_count=True)

        tracker = get_task_tracker()
        queue = get_queue_manager()
        owner = {"account_id": ctx.account_id, "user_id": ctx.user.user_id}

        async def dispatch(resource_id: str | None) -> dict[str, str]:
            task = await tracker.create(
                f"pack_{operation}",
                resource_id=resource_id,
                **owner,
            )
            payload = {
                "operation": operation,
                "args": args,
                "role": str(ctx.role),
                "group_ids": list(ctx.group_ids),
                "actor_peer_id": ctx.actor_peer_id,
                "from_oauth": ctx.from_oauth,
                **owner,
            }
            try:
                with bind_task_context(task.task_id, task_type=task.task_type, **owner):
                    await queue.enqueue(QueueManager.PACK, payload)
            except Exception:
                if not await tracker.has_work(task.task_id, **owner):
                    await tracker.fail(task.task_id, "Could not enqueue OVPack task", **owner)
                raise
            return {"task_id": task.task_id, "status": "accepted"}

        if operation in _UPLOAD_OPERATIONS:
            store = TempUploadStore.build(get_server_config())
            async with store.hold_pack_upload(args["temp_file_id"], ctx) as upload_dir:
                return await run_to_completion(lambda: dispatch(upload_dir))
        return await run_to_completion(lambda: dispatch(args.get("uri")))

    async def on_dequeue(self, data: dict | None) -> ProcessResult:
        if not data:
            return ProcessResult.success()
        metadata = extract_task_metadata(data)
        if metadata is None:
            return ProcessResult.failed("OVPack delivery has no task owner")
        payload = data.get("data", data)
        if isinstance(payload, str):
            payload = json.loads(payload)
        ctx = RequestContext(
            user=UserIdentifier(metadata.account_id, metadata.user_id),
            role=Role(payload["role"]),
            group_ids=tuple(payload["group_ids"]),
            actor_peer_id=payload["actor_peer_id"],
            from_oauth=payload["from_oauth"],
        )
        tracker = get_task_tracker()
        task_id = metadata.task_id
        owner = {"account_id": ctx.account_id, "user_id": ctx.user.user_id}
        task = await tracker.get(task_id, **owner)
        if task is None:
            return ProcessResult.failed("OVPack task is missing")
        result = dict(task.result or {})

        async def record_write(uri: str, files_written: bool) -> None:
            result["uri"] = uri
            if files_written:
                result.pop("target_may_be_partial", None)
                result["files_written"] = True
            else:
                result["target_may_be_partial"] = True
            updated = await tracker.patch_task(
                task_id,
                TaskPatch(stage="files_written" if files_written else "writing", result=result),
                **owner,
            )
            if not updated.is_applied:
                raise FailedPreconditionError("Could not record OVPack write state.")

        try:
            if task.stage in {"writing", "files_written"}:
                raise FailedPreconditionError("OVPack write was interrupted; check the target URI.")
            if ctx.role != Role.ROOT and self.resolve_role is not None:
                current_role = await self.resolve_role(ctx.account_id, ctx.user.user_id)
                if current_role is None or not current_role.allows_token_role(ctx.role):
                    raise PermissionDeniedError("OVPack task owner's permissions have changed.")
            if ctx.role != Role.ROOT and self.resolve_groups is not None:
                ctx.group_ids = tuple(await self.resolve_groups(ctx.account_id, ctx.user.user_id))
            operation = payload["operation"]
            args = dict(payload["args"])
            if operation in _UPLOAD_OPERATIONS:
                vfs = self._pack._ensure_initialized()
                agfs = vfs._async_agfs
                lock_path = vfs._uri_to_path("viking://temp/.ovpack-write", ctx=ctx)
                guard = PathLockGuard(agfs)
                try:
                    await guard.acquire(lambda: agfs.pathlock_acquire_exact(lock_path))
                    source = await TempUploadStore.build(get_server_config()).resolve_for_consume(
                        args.pop("temp_file_id"), ctx
                    )
                    try:
                        apply_pack = (
                            self._pack.import_ovpack
                            if operation == "import"
                            else self._pack.restore_ovpack
                        )
                        uri = await apply_pack(
                            source.local_path, ctx=ctx, on_write=record_write, **args
                        )
                        result["uri"] = uri
                    finally:
                        await source.cleanup()
                finally:
                    await guard.close()
            else:
                result = await self._archive(task_id, operation, args, ctx)
            await tracker.complete(task_id, result, **owner)
            return ProcessResult.success()
        except Exception as exc:
            error = (
                InvalidArgumentError("Invalid OVPack ZIP archive.")
                if isinstance(exc, BadZipFile)
                else map_exception(exc)
            )
            if error is None:
                logger.exception("OVPack task failed task_id=%s", task_id)
            message = str(error) if error else "OVPack task failed"
            result.update({"code": error.code if error else "INTERNAL"})
            if error and error.details:
                result["details"] = error.details
            await tracker.fail(task_id, message, result=result, **owner)
            return ProcessResult.failed(message)

    @staticmethod
    def _archive_uri(task_id: str, ctx: RequestContext) -> str:
        return f"viking://temp/{ctx.user.user_space_name()}/ovpack/{task_id}"

    async def _archive(
        self, task_id: str, operation: str, args: dict[str, Any], ctx: RequestContext
    ) -> dict[str, Any]:
        vfs = self._pack._ensure_initialized()
        root = self._archive_uri(task_id, ctx)
        with suppress(AGFSNotFoundError):
            await vfs.remove_files(root, recursive=True, ctx=ctx)
        try:
            with tempfile.TemporaryDirectory(prefix="ovpack-") as directory:
                if operation == "export":
                    uri = args.pop("uri")
                    filename = f"{uri.rstrip('/').rsplit('/', 1)[-1]}.ovpack"
                    path = await self._pack.export_ovpack(
                        uri, f"{directory}/archive.ovpack", ctx=ctx, **args
                    )
                else:
                    filename = "openviking-backup.ovpack"
                    path = await self._pack.backup_ovpack(
                        f"{directory}/archive.ovpack", ctx=ctx, **args
                    )
                size = Path(path).stat().st_size
                with open(path, "rb") as archive:
                    index = 0
                    while chunk := await asyncio.to_thread(archive.read, _CHUNK_SIZE):
                        await vfs.write_file_bytes(f"{root}/{index}", chunk, ctx=ctx)
                        index += 1
        except Exception:
            await vfs.delete_temp(root, ctx=ctx)
            raise
        return {
            "filename": filename,
            "size": size,
            "download_path": f"/api/v1/pack/download/{task_id}",
        }

    async def download(
        self, task_id: str, ctx: RequestContext
    ) -> tuple[str, int, AsyncIterator[bytes]]:
        tracker = get_task_tracker()
        task = await tracker.get(task_id, account_id=ctx.account_id, user_id=ctx.user.user_id)
        if task is None or task.task_type not in _ARCHIVE_TASK_TYPES:
            raise NotFoundError(task_id, "OVPack task")
        if task.task_type == "pack_backup" and ctx.role not in {Role.ROOT, Role.ADMIN}:
            raise PermissionDeniedError("OVPack backups require ROOT or ADMIN role")
        if task.status != TaskStatus.COMPLETED or not task.result:
            raise FailedPreconditionError("OVPack archive is available after the task completes.")
        if time.time() >= task.updated_at + tracker.TTL_COMPLETED:
            raise NotFoundError(task_id, "OVPack archive")
        root = self._archive_uri(task_id, ctx)
        vfs = self._pack._ensure_initialized()
        size = task.result["size"]
        first = await vfs.read_file_bytes(f"{root}/0", ctx=ctx)

        async def stream() -> AsyncIterator[bytes]:
            yield first
            for index in range(1, (size + _CHUNK_SIZE - 1) // _CHUNK_SIZE):
                yield await vfs.read_file_bytes(f"{root}/{index}", ctx=ctx)

        return task.result["filename"], size, stream()

    async def cleanup_expired(self, task: DeletedTask) -> None:
        if task.task_type in _ARCHIVE_TASK_TYPES:
            ctx = RequestContext(user=UserIdentifier(task.account_id, task.user_id), role=Role.ROOT)
            with suppress(AGFSNotFoundError):
                await self._pack._ensure_initialized().remove_files(
                    self._archive_uri(task.task_id, ctx), recursive=True, ctx=ctx
                )
