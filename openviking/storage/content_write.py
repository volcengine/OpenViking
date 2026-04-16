# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: Apache-2.0
"""Coordinator for content write operations."""

from __future__ import annotations

import os
from typing import Any, Dict, Optional

from openviking.resource.watch_storage import is_watch_task_control_uri
from openviking.server.identity import RequestContext
from openviking.session.memory.utils.content import deserialize_full, serialize_with_metadata
from openviking.storage.queuefs import SemanticMsg, get_queue_manager
from openviking.storage.queuefs.semantic_processor import SemanticProcessor
from openviking.storage.transaction import get_lock_manager
from openviking.storage.viking_fs import VikingFS
from openviking.telemetry import get_current_telemetry
from openviking.telemetry.request_wait_tracker import get_request_wait_tracker
from openviking.telemetry.resource_summary import build_queue_status_payload
from openviking.utils.embedding_utils import vectorize_file
from openviking_cli.exceptions import DeadlineExceededError, InvalidArgumentError, NotFoundError
from openviking_cli.utils import VikingURI
from openviking_cli.utils.logger import get_logger

logger = get_logger(__name__)

_DERIVED_FILENAMES = frozenset({".abstract.md", ".overview.md", ".relations.json"})


class ContentWriteCoordinator:
    """Write an existing file and trigger downstream maintenance."""

    def __init__(self, viking_fs: VikingFS):
        self._viking_fs = viking_fs

    async def write(
        self,
        *,
        uri: str,
        content: str,
        ctx: RequestContext,
        mode: str = "replace",
        wait: bool = False,
        timeout: Optional[float] = None,
    ) -> Dict[str, Any]:
        normalized_uri = VikingURI.normalize(uri)
        self._validate_mode(mode)
        self._validate_target_uri(normalized_uri)

        stat = await self._safe_stat(normalized_uri, ctx=ctx)
        if stat.get("isDir"):
            raise InvalidArgumentError(f"write only supports existing files, got directory: {uri}")

        context_type = self._context_type_for_uri(normalized_uri)
        root_uri = await self._resolve_root_uri(normalized_uri, ctx=ctx)
        written_bytes = len(content.encode("utf-8"))
        telemetry_id = get_current_telemetry().telemetry_id

        if context_type == "memory":
            return await self._write_memory_with_refresh(
                uri=normalized_uri,
                root_uri=root_uri,
                content=content,
                mode=mode,
                wait=wait,
                timeout=timeout,
                ctx=ctx,
                written_bytes=written_bytes,
                telemetry_id=telemetry_id,
            )

        lock_manager = get_lock_manager()
        handle = lock_manager.create_handle()
        lock_path = self._viking_fs._uri_to_path(root_uri, ctx=ctx)
        acquired = await lock_manager.acquire_subtree(handle, lock_path)
        if not acquired:
            await lock_manager.release(handle)
            raise InvalidArgumentError(
                f"resource is busy and cannot be written now: {normalized_uri}"
            )

        temp_root_uri = ""
        lock_transferred = False
        try:
            if wait and telemetry_id:
                get_request_wait_tracker().register_request(telemetry_id)
            temp_root_uri, temp_target_uri = await self._prepare_temp_write(
                uri=normalized_uri,
                root_uri=root_uri,
                content=content,
                mode=mode,
                ctx=ctx,
            )
            await self._enqueue_semantic_refresh(
                temp_root_uri=temp_root_uri,
                target_root_uri=root_uri,
                temp_target_uri=temp_target_uri,
                context_type=context_type,
                ctx=ctx,
                lifecycle_lock_handle_id=handle.id,
            )
            lock_transferred = True
            queue_status = (
                await self._wait_for_request(telemetry_id=telemetry_id, timeout=timeout)
                if wait
                else None
            )
            return {
                "uri": normalized_uri,
                "root_uri": root_uri,
                "context_type": context_type,
                "mode": mode,
                "written_bytes": written_bytes,
                "semantic_updated": True,
                "vector_updated": True,
                "queue_status": queue_status,
            }
        except Exception:
            if not lock_transferred and temp_root_uri:
                try:
                    await self._viking_fs.delete_temp(temp_root_uri, ctx=ctx)
                except Exception:
                    logger.debug("Failed to clean temp tree after write failure", exc_info=True)
            if not lock_transferred:
                await lock_manager.release(handle)
            raise
        finally:
            if wait and telemetry_id:
                get_request_wait_tracker().cleanup(telemetry_id)

    def _validate_mode(self, mode: str) -> None:
        if mode not in {"replace", "append"}:
            raise InvalidArgumentError(f"unsupported write mode: {mode}")

    def _validate_target_uri(self, uri: str) -> None:
        name = uri.rstrip("/").split("/")[-1]
        if name in _DERIVED_FILENAMES:
            raise InvalidArgumentError(f"cannot write derived semantic file directly: {uri}")
        if is_watch_task_control_uri(uri):
            raise InvalidArgumentError(f"cannot write watch task control file directly: {uri}")

        parsed = VikingURI(uri)
        if parsed.scope not in {"resources", "user", "agent"}:
            raise InvalidArgumentError(f"write is not supported for scope: {parsed.scope}")

    async def _safe_stat(self, uri: str, *, ctx: RequestContext) -> Dict[str, Any]:
        try:
            return await self._viking_fs.stat(uri, ctx=ctx)
        except Exception as exc:
            if isinstance(exc, NotFoundError):
                raise
            raise NotFoundError(uri, "file") from exc

    async def _write_in_place(
        self,
        uri: str,
        content: str,
        *,
        mode: str,
        ctx: RequestContext,
    ) -> None:
        if mode == "replace" and self._context_type_for_uri(uri) == "memory":
            existing_raw = await self._viking_fs.read_file(uri, ctx=ctx)
            _, metadata = deserialize_full(existing_raw)
            if metadata:
                content = serialize_with_metadata(content, metadata)
            await self._viking_fs.write_file(uri, content, ctx=ctx)
            return

        if mode == "append":
            existing_raw = await self._viking_fs.read_file(uri, ctx=ctx)
            existing_content, metadata = deserialize_full(existing_raw)
            updated_content = existing_content + content
            if metadata:
                updated_raw = serialize_with_metadata(updated_content, metadata)
            else:
                updated_raw = updated_content
            await self._viking_fs.write_file(uri, updated_raw, ctx=ctx)
            return
        await self._viking_fs.write_file(uri, content, ctx=ctx)

    async def _prepare_temp_write(
        self,
        *,
        uri: str,
        root_uri: str,
        content: str,
        mode: str,
        ctx: RequestContext,
    ) -> tuple[str, str]:
        temp_base = self._viking_fs.create_temp_uri(ctx=ctx)
        await self._viking_fs.mkdir(temp_base, exist_ok=True, ctx=ctx)
        root_name = root_uri.rstrip("/").split("/")[-1]
        temp_root_uri = f"{temp_base.rstrip('/')}/{root_name}"
        await self._copy_tree(root_uri, temp_root_uri, ctx=ctx)

        temp_target_uri = self._translate_to_temp_uri(
            uri=uri, root_uri=root_uri, temp_root_uri=temp_root_uri
        )
        await self._write_in_place(temp_target_uri, content, mode=mode, ctx=ctx)
        return temp_root_uri, temp_target_uri

    async def _copy_tree(self, src_uri: str, dst_uri: str, *, ctx: RequestContext) -> None:
        stat = await self._safe_stat(src_uri, ctx=ctx)
        if not stat.get("isDir"):
            raise InvalidArgumentError(f"incremental write root must be a directory: {src_uri}")

        await self._viking_fs.mkdir(dst_uri, exist_ok=True, ctx=ctx)
        entries = await self._viking_fs.ls(
            src_uri, output="original", show_all_hidden=True, ctx=ctx
        )
        for entry in entries:
            name = entry.get("name", "")
            if not name or name in {".", ".."}:
                continue
            src_child = VikingURI(src_uri).join(name).uri
            dst_child = VikingURI(dst_uri).join(name).uri
            if entry.get("isDir", False):
                await self._copy_tree(src_child, dst_child, ctx=ctx)
                continue
            content = await self._viking_fs.read_file_bytes(src_child, ctx=ctx)
            await self._viking_fs.write_file_bytes(dst_child, content, ctx=ctx)

    def _translate_to_temp_uri(self, *, uri: str, root_uri: str, temp_root_uri: str) -> str:
        if uri == root_uri:
            return temp_root_uri
        prefix = root_uri.rstrip("/") + "/"
        if not uri.startswith(prefix):
            raise InvalidArgumentError(f"uri {uri} is not inside write root {root_uri}")
        relative = uri[len(prefix) :]
        return f"{temp_root_uri.rstrip('/')}/{relative}"

    async def _enqueue_semantic_refresh(
        self,
        *,
        temp_root_uri: str,
        target_root_uri: str,
        temp_target_uri: str,
        context_type: str,
        ctx: RequestContext,
        lifecycle_lock_handle_id: str,
    ) -> None:
        queue_manager = get_queue_manager()
        semantic_queue = queue_manager.get_queue(queue_manager.SEMANTIC, allow_create=True)
        telemetry = get_current_telemetry()
        msg = SemanticMsg(
            uri=temp_root_uri,
            target_uri=target_root_uri,
            context_type=context_type,
            account_id=ctx.account_id,
            user_id=ctx.user.user_id,
            agent_id=ctx.user.agent_id,
            role=ctx.role.value,
            skip_vectorization=False,
            telemetry_id=telemetry.telemetry_id,
            lifecycle_lock_handle_id=lifecycle_lock_handle_id,
            changes={"modified": [temp_target_uri]},
        )
        await semantic_queue.enqueue(msg)
        if msg.telemetry_id:
            get_request_wait_tracker().register_semantic_root(msg.telemetry_id, msg.id)

    async def _enqueue_memory_refresh(
        self,
        *,
        root_uri: str,
        modified_uri: str,
        ctx: RequestContext,
        lifecycle_lock_handle_id: str,
    ) -> None:
        queue_manager = get_queue_manager()
        semantic_queue = queue_manager.get_queue(queue_manager.SEMANTIC, allow_create=True)
        telemetry = get_current_telemetry()
        msg = SemanticMsg(
            uri=root_uri,
            context_type="memory",
            account_id=ctx.account_id,
            user_id=ctx.user.user_id,
            agent_id=ctx.user.agent_id,
            role=ctx.role.value,
            skip_vectorization=False,
            telemetry_id=telemetry.telemetry_id,
            lifecycle_lock_handle_id=lifecycle_lock_handle_id,
            changes={"modified": [modified_uri]},
        )
        await semantic_queue.enqueue(msg)
        if msg.telemetry_id:
            get_request_wait_tracker().register_semantic_root(msg.telemetry_id, msg.id)

    async def _wait_for_queues(self, *, timeout: Optional[float]) -> Dict[str, Any]:
        queue_manager = get_queue_manager()
        try:
            status = await queue_manager.wait_complete(timeout=timeout)
        except TimeoutError as exc:
            raise DeadlineExceededError("queue processing", timeout) from exc
        return build_queue_status_payload(status)

    async def _wait_for_request(
        self,
        *,
        telemetry_id: str,
        timeout: Optional[float],
    ) -> Dict[str, Any]:
        if not telemetry_id:
            return await self._wait_for_queues(timeout=timeout)
        tracker = get_request_wait_tracker()
        try:
            await tracker.wait_for_request(telemetry_id, timeout=timeout)
        except TimeoutError as exc:
            raise DeadlineExceededError("queue processing", timeout) from exc
        return tracker.build_queue_status(telemetry_id)

    async def _vectorize_single_file(
        self,
        uri: str,
        *,
        context_type: str,
        ctx: RequestContext,
    ) -> None:
        parent = VikingURI(uri).parent
        if parent is None:
            raise InvalidArgumentError(f"file has no parent directory: {uri}")
        summary_dict = await self._summary_dict_for_vectorize(
            uri, context_type=context_type, ctx=ctx
        )
        await vectorize_file(
            file_path=uri,
            summary_dict=summary_dict,
            parent_uri=parent.uri,
            context_type=context_type,
            ctx=ctx,
            preserve_existing_created_at=True,
        )

    async def _summary_dict_for_vectorize(
        self,
        uri: str,
        *,
        context_type: str,
        ctx: RequestContext,
    ) -> Dict[str, str]:
        file_name = os.path.basename(uri)
        if context_type != "memory":
            return {"name": file_name}

        try:
            processor = SemanticProcessor(max_concurrent_llm=1)
            return await processor._generate_single_file_summary(uri, ctx=ctx)
        except Exception:
            logger.warning(
                "Failed to generate summary for memory write vector refresh: %s",
                uri,
                exc_info=True,
            )
            return {"name": file_name}

    async def _write_memory_with_refresh(
        self,
        *,
        uri: str,
        root_uri: str,
        content: str,
        mode: str,
        wait: bool,
        timeout: Optional[float],
        ctx: RequestContext,
        written_bytes: int,
        telemetry_id: str,
    ) -> Dict[str, Any]:
        lock_manager = get_lock_manager()
        handle = lock_manager.create_handle()
        lock_path = self._viking_fs._uri_to_path(root_uri, ctx=ctx)
        acquired = await lock_manager.acquire_subtree(handle, lock_path)
        if not acquired:
            await lock_manager.release(handle)
            raise InvalidArgumentError(f"resource is busy and cannot be written now: {uri}")

        lock_transferred = False
        try:
            if wait and telemetry_id:
                get_request_wait_tracker().register_request(telemetry_id)
            await self._write_in_place(uri, content, mode=mode, ctx=ctx)
            await self._vectorize_single_file(uri, context_type="memory", ctx=ctx)
            await self._enqueue_memory_refresh(
                root_uri=root_uri,
                modified_uri=uri,
                ctx=ctx,
                lifecycle_lock_handle_id=handle.id,
            )
            lock_transferred = True
            queue_status = (
                await self._wait_for_request(telemetry_id=telemetry_id, timeout=timeout)
                if wait
                else None
            )
            return {
                "uri": uri,
                "root_uri": root_uri,
                "context_type": "memory",
                "mode": mode,
                "written_bytes": written_bytes,
                "semantic_updated": True,
                "vector_updated": True,
                "queue_status": queue_status,
            }
        except Exception:
            if not lock_transferred:
                await lock_manager.release(handle)
            raise
        finally:
            if wait and telemetry_id:
                get_request_wait_tracker().cleanup(telemetry_id)

    async def _resolve_root_uri(self, uri: str, *, ctx: RequestContext) -> str:
        parsed = VikingURI(uri)
        parts = [part for part in parsed.full_path.split("/") if part]
        if not parts:
            raise InvalidArgumentError(f"invalid write uri: {uri}")

        root_uri = uri
        if parts[0] == "resources":
            if len(parts) >= 2:
                root_uri = VikingURI.build("resources", parts[1])
        elif parts[0] == "user":
            try:
                memories_idx = parts.index("memories")
            except ValueError as exc:
                raise InvalidArgumentError(
                    f"write only supports memory files under user scope: {uri}"
                ) from exc
            if len(parts) <= memories_idx + 1:
                raise InvalidArgumentError(
                    f"memory write target must be inside a memory type directory: {uri}"
                )
            root_uri = VikingURI.build(*parts[: memories_idx + 2])
        elif parts[0] == "agent":
            if len(parts) >= 3 and parts[1] == "skills":
                root_uri = VikingURI.build(*parts[:3])
            else:
                try:
                    memories_idx = parts.index("memories")
                except ValueError as exc:
                    raise InvalidArgumentError(
                        f"write only supports memory or skill files under agent scope: {uri}"
                    ) from exc
                if len(parts) <= memories_idx + 1:
                    raise InvalidArgumentError(
                        f"memory write target must be inside a memory type directory: {uri}"
                    )
                root_uri = VikingURI.build(*parts[: memories_idx + 2])

        stat = await self._safe_stat(root_uri, ctx=ctx)
        if not stat.get("isDir"):
            parent = VikingURI(uri).parent
            if parent is None:
                raise InvalidArgumentError(f"could not resolve write root for {uri}")
            root_uri = parent.uri
        return root_uri

    def _context_type_for_uri(self, uri: str) -> str:
        if "/memories/" in uri:
            return "memory"
        if "/skills/" in uri or uri.startswith("viking://agent/skills/"):
            return "skill"
        return "resource"
