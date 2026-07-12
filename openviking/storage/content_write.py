# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: Apache-2.0
"""Coordinator for content write operations."""

from __future__ import annotations

import os
from typing import TYPE_CHECKING, Any, Dict, Optional

from openviking.core.namespace import NamespaceShapeError, canonicalize_uri, context_type_for_uri
from openviking.resource.watch_storage import is_watch_task_control_uri
from openviking.server.identity import RequestContext
from openviking.session.memory.memory_updater import MemoryUpdater
from openviking.session.memory.utils.memory_file_utils import MemoryFileUtils
from openviking.session.memory.utils.resource_refs import (
    RESOURCE_REF_SOURCE_CONTENT_WRITE,
    sync_memory_resource_refs,
)
from openviking.storage.errors import ResourceBusyError
from openviking.storage.queuefs import SemanticMsg, get_queue_manager
from openviking.storage.queuefs.semantic_msg import build_semantic_coalesce_key
from openviking.storage.transaction import get_lock_manager
from openviking.storage.viking_fs import VikingFS
from openviking.telemetry import get_current_telemetry
from openviking.telemetry.request_wait_tracker import get_request_wait_tracker
from openviking.telemetry.resource_summary import build_queue_status_payload
from openviking.utils.tags import normalize_search_tags
from openviking_cli.exceptions import (
    AlreadyExistsError,
    DeadlineExceededError,
    InvalidArgumentError,
    NotFoundError,
)
from openviking_cli.utils import VikingURI
from openviking_cli.utils.logger import get_logger

logger = get_logger(__name__)

if TYPE_CHECKING:
    from openviking.storage.transaction.lock_handle import LockHandle

_DERIVED_FILENAMES = frozenset({".abstract.md", ".overview.md", ".relations.json"})
_CREATE_ALLOWED_EXTENSIONS = frozenset(
    {".md", ".txt", ".json", ".yaml", ".yml", ".toml", ".py", ".js", ".ts"}
)


class ContentWriteCoordinator:
    """Write a file (create or modify) and trigger downstream maintenance."""

    def __init__(self, viking_fs: VikingFS, vikingdb: Any = None):
        self._viking_fs = viking_fs
        self._vikingdb = vikingdb

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
        try:
            normalized_uri = canonicalize_uri(uri, ctx)
        except NamespaceShapeError as exc:
            raise InvalidArgumentError(str(exc)) from exc
        self._validate_mode(mode)
        self._validate_target_uri(normalized_uri)
        self._viking_fs._ensure_mutable_access(normalized_uri, ctx)

        if mode == "create":
            return await self._create_and_write(
                uri=normalized_uri,
                content=content,
                ctx=ctx,
                wait=wait,
                timeout=timeout,
            )

        stat = await self._safe_stat(normalized_uri, ctx=ctx)
        if stat.get("isDir"):
            raise InvalidArgumentError(f"write only supports existing files, got directory: {uri}")

        context_type = context_type_for_uri(normalized_uri)
        root_uri = await self._resolve_root_uri(normalized_uri, ctx=ctx, anchor_to_parent=True)
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

        return await self._write_direct_with_refresh(
            uri=normalized_uri,
            root_uri=root_uri,
            content=content,
            mode=mode,
            context_type=context_type,
            wait=wait,
            timeout=timeout,
            ctx=ctx,
            written_bytes=written_bytes,
            telemetry_id=telemetry_id,
        )

    async def set_tags(
        self,
        *,
        uri: str,
        tags: list[str],
        mode: str = "replace",
        recursive: bool = False,
        ctx: RequestContext,
    ) -> Dict[str, Any]:
        try:
            normalized_uri = canonicalize_uri(uri, ctx)
        except NamespaceShapeError as exc:
            raise InvalidArgumentError(str(exc)) from exc

        self._validate_tag_mode(mode)
        normalized_tags = normalize_search_tags(tags)
        stat = await self._safe_stat(normalized_uri, ctx=ctx)
        if stat.get("isDir"):
            return await self._set_directory_tags(
                uri=normalized_uri,
                tags=normalized_tags,
                mode=mode,
                recursive=recursive,
                ctx=ctx,
            )
        return await self._set_single_uri_tags(
            uri=normalized_uri,
            tags=normalized_tags,
            mode=mode,
            recursive=recursive,
            ctx=ctx,
        )

    def _build_write_result(
        self,
        *,
        uri: str,
        root_uri: str,
        context_type: str,
        mode: str,
        written_bytes: int,
        wait: bool,
        queue_status: Optional[Dict[str, Any]],
        semantic_status: Optional[str] = None,
        vector_status: Optional[str] = None,
        overview_status: Optional[str] = None,
    ) -> Dict[str, Any]:
        if semantic_status is None or vector_status is None:
            semantic_status, vector_status = self._refresh_statuses(
                wait=wait,
                queue_status=queue_status,
            )
        result = {
            "uri": uri,
            "root_uri": root_uri,
            "context_type": context_type,
            "mode": mode,
            "written_bytes": written_bytes,
            "content_updated": True,
            "semantic_status": semantic_status,
            "vector_status": vector_status,
            "queue_status": queue_status,
        }
        if overview_status is not None:
            result["overview_status"] = overview_status
        return result

    def _build_tags_result(
        self,
        *,
        uri: str,
        updated_uris: list[str],
        skipped_count: int,
        failed_count: int,
        root_uri: str,
        context_type: str,
        tags: list[str],
        mode: str,
    ) -> Dict[str, Any]:
        return {
            "uri": uri,
            "updated_uris": updated_uris,
            "root_uri": root_uri,
            "context_type": context_type,
            "tags": tags,
            "mode": mode,
            "success_count": len(updated_uris),
            "skipped_count": skipped_count,
            "failed_count": failed_count,
            "tags_updated": len(updated_uris) > 0,
        }

    def _refresh_statuses(
        self,
        *,
        wait: bool,
        queue_status: Optional[Dict[str, Any]],
    ) -> tuple[str, str]:
        if not wait:
            return "queued", "queued"
        if not queue_status:
            return "complete", "complete"

        def _has_errors(name: str) -> bool:
            status = queue_status.get(name, {})
            if not isinstance(status, dict):
                return False
            try:
                return int(status.get("error_count", 0) or 0) > 0
            except (TypeError, ValueError):
                return bool(status.get("errors"))

        semantic_status = "failed" if _has_errors("Semantic") else "complete"
        vector_status = "failed" if _has_errors("Embedding") else "complete"
        return semantic_status, vector_status

    async def _write_direct_with_refresh(
        self,
        *,
        uri: str,
        root_uri: str,
        content: str,
        mode: str,
        context_type: str,
        wait: bool,
        timeout: Optional[float],
        ctx: RequestContext,
        written_bytes: int,
        telemetry_id: str,
    ) -> Dict[str, Any]:
        lock_manager = get_lock_manager()
        handle = lock_manager.create_handle()
        lock_path = self._viking_fs._uri_to_path(uri, ctx=ctx)
        acquired = await lock_manager.acquire_exact_path(handle, lock_path)
        if not acquired:
            await lock_manager.release(handle)
            raise ResourceBusyError(
                f"resource is busy and cannot be written now: {uri}",
                uri=uri,
            )

        previous_content: Optional[str] = None
        content_written = False
        semantic_enqueued = False
        lock_released = False
        try:
            if mode != "create":
                previous_content = await self._viking_fs.read_file(uri, ctx=ctx)
            if wait and telemetry_id:
                get_request_wait_tracker().register_request(telemetry_id)
            await self._write_in_place(
                uri,
                content,
                mode=mode,
                ctx=ctx,
                lock_handle=handle,
            )
            content_written = True
            await self._enqueue_semantic_refresh(
                root_uri=root_uri,
                changed_uri=uri,
                context_type=context_type,
                ctx=ctx,
                change_type="added" if mode == "create" else "modified",
            )
            semantic_enqueued = True
            await lock_manager.release(handle)
            lock_released = True
            queue_status = (
                await self._wait_for_request(telemetry_id=telemetry_id, timeout=timeout)
                if wait
                else None
            )
            return self._build_write_result(
                uri=uri,
                root_uri=root_uri,
                context_type=context_type,
                mode=mode,
                written_bytes=written_bytes,
                wait=wait,
                queue_status=queue_status,
            )
        except Exception:
            if not semantic_enqueued and content_written:
                await self._rollback_direct_write(
                    uri=uri,
                    previous_content=previous_content,
                    mode=mode,
                    ctx=ctx,
                    lock_handle=handle,
                )
            if not lock_released:
                await lock_manager.release(handle)
            raise
        finally:
            if wait and telemetry_id:
                get_request_wait_tracker().cleanup(telemetry_id)

    async def _rollback_direct_write(
        self,
        *,
        uri: str,
        previous_content: Optional[str],
        mode: str,
        ctx: RequestContext,
        lock_handle: Any,
    ) -> None:
        try:
            if mode == "create":
                await self._viking_fs.rm(uri, ctx=ctx, lock_handle=lock_handle)
                return
            if previous_content is not None:
                await self._viking_fs.write_file(
                    uri,
                    previous_content,
                    ctx=ctx,
                    lock_handle=lock_handle,
                )
        except Exception:
            logger.error("Failed to rollback direct content write for %s", uri, exc_info=True)

    def _validate_mode(self, mode: str) -> None:
        if mode not in {"replace", "append", "create"}:
            raise InvalidArgumentError(f"unsupported write mode: {mode}")

    def _validate_tag_mode(self, mode: str) -> None:
        if mode not in {"replace", "append"}:
            raise InvalidArgumentError(f"unsupported tag mode: {mode}")

    def _validate_target_uri(self, uri: str) -> None:
        name = uri.rstrip("/").split("/")[-1]
        if name in _DERIVED_FILENAMES:
            raise InvalidArgumentError(f"cannot write derived semantic file directly: {uri}")
        if is_watch_task_control_uri(uri):
            raise InvalidArgumentError(f"cannot write watch task control file directly: {uri}")

        parsed = VikingURI(uri)
        if parsed.scope not in {"resources", "user", "agent"}:
            raise InvalidArgumentError(f"write is not supported for scope: {parsed.scope}")

    def _is_not_found(self, exc: Exception) -> bool:
        """Check if an exception indicates a not-found error (OpenViking or AGFS)."""
        if isinstance(exc, NotFoundError):
            return True
        # AGFS raises its own AGFSNotFoundError which is unrelated to our NotFoundError
        try:
            from openviking.pyagfs import AGFSNotFoundError

            return isinstance(exc, AGFSNotFoundError)
        except ImportError:
            return False

    async def _safe_stat(
        self, uri: str, *, ctx: RequestContext, allow_not_found: bool = False
    ) -> Dict[str, Any]:
        try:
            return await self._viking_fs.stat(uri, ctx=ctx)
        except Exception as exc:
            if self._is_not_found(exc):
                if allow_not_found:
                    return {"not_found": True}
                if isinstance(exc, NotFoundError):
                    raise
                raise NotFoundError(uri, "file") from exc
            raise NotFoundError(uri, "file") from exc

    def _validate_create_extension(self, uri: str) -> None:
        _, ext = os.path.splitext(uri)
        if ext.lower() not in _CREATE_ALLOWED_EXTENSIONS:
            raise InvalidArgumentError(f"create mode does not allow extension '{ext}': {uri}")

    async def _create_and_write(
        self,
        *,
        uri: str,
        content: str,
        ctx: RequestContext,
        wait: bool,
        timeout: Optional[float],
    ) -> Dict[str, Any]:
        self._validate_create_extension(uri)

        stat = await self._safe_stat(uri, ctx=ctx, allow_not_found=True)
        if not stat.get("not_found"):
            raise AlreadyExistsError(uri, "file")

        context_type = context_type_for_uri(uri)
        root_uri = await self._resolve_root_uri(
            uri, ctx=ctx, _allow_not_found=True, anchor_to_parent=True
        )
        written_bytes = len(content.encode("utf-8"))
        telemetry_id = get_current_telemetry().telemetry_id

        if context_type == "memory":
            return await self._write_memory_with_refresh(
                uri=uri,
                root_uri=root_uri,
                content=content,
                mode="create",
                wait=wait,
                timeout=timeout,
                ctx=ctx,
                written_bytes=written_bytes,
                telemetry_id=telemetry_id,
            )

        return await self._write_direct_with_refresh(
            uri=uri,
            root_uri=root_uri,
            content=content,
            mode="create",
            context_type=context_type,
            wait=wait,
            timeout=timeout,
            ctx=ctx,
            written_bytes=written_bytes,
            telemetry_id=telemetry_id,
        )

    async def _write_in_place(
        self,
        uri: str,
        content: str,
        *,
        mode: str,
        ctx: RequestContext,
        lock_handle: Optional["LockHandle"] = None,
    ) -> None:
        if context_type_for_uri(uri) == "memory":
            if mode == "replace":
                existing_raw = await self._viking_fs.read_file(uri, ctx=ctx)
                mf = MemoryFileUtils.read(existing_raw, uri=uri)
                mf.content = content
            elif mode == "append":
                existing_raw = await self._viking_fs.read_file(uri, ctx=ctx)
                mf = MemoryFileUtils.read(existing_raw, uri=uri)
                mf.content = mf.content + content
            else:
                mf = MemoryFileUtils.read(content, uri=uri)
            sync_memory_resource_refs(mf, source=RESOURCE_REF_SOURCE_CONTENT_WRITE)
            await self._viking_fs.write_file(
                uri,
                MemoryFileUtils.write(mf),
                ctx=ctx,
                lock_handle=lock_handle,
            )
            return

        if mode == "append":
            existing_raw = await self._viking_fs.read_file(uri, ctx=ctx)
            mf = MemoryFileUtils.read(existing_raw, uri=uri)
            mf.content = mf.content + content
            updated_raw = MemoryFileUtils.write(mf)
            await self._viking_fs.write_file(
                uri,
                updated_raw,
                ctx=ctx,
                lock_handle=lock_handle,
            )
            return
        await self._viking_fs.write_file(uri, content, ctx=ctx, lock_handle=lock_handle)

    async def _enqueue_semantic_refresh(
        self,
        *,
        root_uri: str,
        changed_uri: str,
        context_type: str,
        ctx: RequestContext,
        change_type: str = "modified",
        target_uri: str = "",
        recursive: bool = False,
    ) -> None:
        queue_manager = get_queue_manager()
        semantic_queue = queue_manager.get_queue(queue_manager.SEMANTIC, allow_create=True)
        telemetry = get_current_telemetry()
        msg = SemanticMsg(
            uri=root_uri,
            target_uri=target_uri,
            context_type=context_type,
            recursive=recursive,
            account_id=ctx.account_id,
            user_id=ctx.user.user_id,
            role=str(ctx.role),
            skip_vectorization=False,
            telemetry_id=telemetry.telemetry_id,
            coalesce_key=(
                build_semantic_coalesce_key(
                    context_type=context_type,
                    uri=root_uri,
                    account_id=ctx.account_id,
                    user_id=ctx.user.user_id,
                )
                if context_type in {"resource", "skill"}
                else ""
            ),
            changes={change_type: [changed_uri]},
        )
        if msg.telemetry_id:
            get_request_wait_tracker().register_semantic_root(msg.telemetry_id, msg.id)
        try:
            await semantic_queue.enqueue(msg)
        except Exception as e:
            if msg.telemetry_id:
                get_request_wait_tracker().mark_semantic_failed(msg.telemetry_id, msg.id, str(e))
            raise

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
        lock_path = self._viking_fs._uri_to_path(uri, ctx=ctx)
        acquired = await lock_manager.acquire_exact_path(handle, lock_path)
        if not acquired:
            await lock_manager.release(handle)
            raise ResourceBusyError(
                f"resource is busy and cannot be written now: {uri}",
                uri=uri,
            )

        released = False
        request_registered = False
        try:
            await self._write_in_place(
                uri,
                content,
                mode=mode,
                ctx=ctx,
                lock_handle=handle,
            )
            await lock_manager.release(handle)
            released = True
            if wait and telemetry_id and self._vikingdb_has_queue():
                get_request_wait_tracker().register_request(telemetry_id)
                request_registered = True
            await MemoryUpdater.refresh_schema_overview(
                viking_fs=self._viking_fs,
                directory_uri=root_uri,
                ctx=ctx,
            )
            embedding_requested = await MemoryUpdater.refresh_file_embedding(
                viking_fs=self._viking_fs,
                vikingdb=self._vikingdb,
                uri=uri,
                memory_type=MemoryUpdater.memory_type_from_uri(root_uri),
                ctx=ctx,
            )
            queue_status = None
            if embedding_requested and wait:
                queue_status = (
                    await self._wait_for_request(telemetry_id=telemetry_id, timeout=timeout)
                    if telemetry_id
                    else await self._wait_for_queues(timeout=timeout)
                )
            vector_status = self._memory_vector_status(
                embedding_requested=embedding_requested,
                wait=wait,
                queue_status=queue_status,
            )
            return self._build_write_result(
                uri=uri,
                root_uri=root_uri,
                context_type="memory",
                mode=mode,
                written_bytes=written_bytes,
                wait=wait,
                queue_status=queue_status,
                semantic_status="skipped",
                vector_status=vector_status,
                overview_status="complete",
            )
        except Exception:
            if not released:
                await lock_manager.release(handle)
            raise
        finally:
            if request_registered:
                get_request_wait_tracker().cleanup(telemetry_id)

    def _vikingdb_has_queue(self) -> bool:
        if not self._vikingdb:
            return False
        return bool(getattr(self._vikingdb, "has_queue_manager", False))

    def _memory_vector_status(
        self,
        *,
        embedding_requested: bool,
        wait: bool,
        queue_status: Optional[Dict[str, Any]],
    ) -> str:
        if not embedding_requested:
            return "skipped"
        if not wait:
            return "queued"
        _, vector_status = self._refresh_statuses(wait=True, queue_status=queue_status)
        return vector_status

    async def _set_single_uri_tags(
        self,
        *,
        uri: str,
        tags: list[str],
        mode: str,
        recursive: bool,
        ctx: RequestContext,
    ) -> Dict[str, Any]:
        del recursive
        context_type = context_type_for_uri(uri)
        root_uri = await self._resolve_root_uri(uri, ctx=ctx)
        target_uri = uri
        levels: list[int] | None = None
        if uri.endswith("/.abstract.md"):
            parent = VikingURI(uri).parent
            if parent is not None:
                target_uri = parent.uri.rstrip("/")
                levels = [0]
        elif uri.endswith("/.overview.md"):
            parent = VikingURI(uri).parent
            if parent is not None:
                target_uri = parent.uri.rstrip("/")
                levels = [1]
        updated_uris = await self._upsert_uri_tags(
            uri=target_uri,
            tags=tags,
            mode=mode,
            ctx=ctx,
            levels=levels,
        )
        if not updated_uris:
            return self._build_tags_result(
                uri=uri,
                updated_uris=[],
                skipped_count=1,
                failed_count=0,
                root_uri=root_uri,
                context_type=context_type,
                tags=tags,
                mode=mode,
            )
        return self._build_tags_result(
            uri=uri,
            updated_uris=updated_uris,
            skipped_count=0,
            failed_count=0,
            root_uri=root_uri,
            context_type=context_type,
            tags=tags,
            mode=mode,
        )

    async def _set_directory_tags(
        self,
        *,
        uri: str,
        tags: list[str],
        mode: str,
        recursive: bool,
        ctx: RequestContext,
    ) -> Dict[str, Any]:
        updated_targets = await self._collect_directory_tag_targets(
            uri=uri, recursive=recursive, ctx=ctx
        )

        if not updated_targets:
            raise NotFoundError(uri, "semantic file")

        applied_uris: list[str] = []
        skipped_count = 0
        for target in updated_targets:
            updated_uris = await self._upsert_uri_tags(
                uri=target["uri"],
                tags=tags,
                mode=mode,
                ctx=ctx,
                levels=target.get("levels"),
            )
            if updated_uris:
                applied_uris.extend(updated_uris)
            else:
                skipped_count += 1

        context_type = context_type_for_uri(uri)
        return self._build_tags_result(
            uri=uri,
            updated_uris=applied_uris,
            skipped_count=skipped_count,
            failed_count=0,
            root_uri=uri,
            context_type=context_type,
            tags=tags,
            mode=mode,
        )

    async def _collect_directory_tag_targets(
        self,
        *,
        uri: str,
        recursive: bool,
        ctx: RequestContext,
    ) -> list[dict[str, object]]:
        if not recursive:
            return [{"uri": uri.rstrip("/"), "levels": [0, 1]}]

        entries = await self._viking_fs.tree(
            uri,
            ctx=ctx,
            output="original",
            show_all_hidden=True,
        )

        deduped: list[dict[str, object]] = []
        seen: set[str] = set()
        directory_levels: dict[str, set[int]] = {}
        for entry in entries:
            entry_uri = entry.get("uri", "")
            if not entry_uri or is_watch_task_control_uri(entry_uri):
                continue
            normalized_uri = entry_uri.rstrip("/")
            dedupe_key = f"dir:{normalized_uri}" if entry.get("isDir") else f"file:{normalized_uri}"
            if dedupe_key in seen:
                continue
            seen.add(dedupe_key)
            if entry.get("isDir"):
                directory_levels.setdefault(normalized_uri, set()).update({0, 1})
            elif normalized_uri.endswith("/.abstract.md"):
                parent = VikingURI(normalized_uri).parent
                if parent is not None:
                    directory_levels.setdefault(parent.uri.rstrip("/"), set()).add(0)
            elif normalized_uri.endswith("/.overview.md"):
                parent = VikingURI(normalized_uri).parent
                if parent is not None:
                    directory_levels.setdefault(parent.uri.rstrip("/"), set()).add(1)
            else:
                deduped.append({"uri": normalized_uri})
        for directory_uri, levels in directory_levels.items():
            deduped.append({"uri": directory_uri, "levels": sorted(levels)})
        return deduped

    async def _upsert_uri_tags(
        self,
        *,
        uri: str,
        tags: list[str],
        mode: str,
        ctx: RequestContext,
        levels: list[int] | None = None,
    ) -> list[str]:
        store = self._viking_fs._get_vector_store()
        if not store:
            raise RuntimeError("Vector store not initialized. Call OpenViking.initialize() first.")
        if levels:
            updated_records = await store.update_search_tags(
                uri,
                tags,
                mode=mode,
                levels=levels,
                ctx=ctx,
            )
            return [str(record.get("uri")) for record in updated_records if record.get("uri")]
        updated_records = await store.update_search_tags(uri, tags, mode=mode, ctx=ctx)
        if not updated_records:
            return []
        return [str(record.get("uri")) for record in updated_records if record.get("uri")]

    async def _resolve_root_uri(
        self,
        uri: str,
        *,
        ctx: RequestContext,
        _allow_not_found: bool = False,
        anchor_to_parent: bool = False,
    ) -> str:
        parsed = VikingURI(uri)
        parts = [part for part in parsed.full_path.split("/") if part]
        if not parts:
            raise InvalidArgumentError(f"invalid write uri: {uri}")

        root_uri = uri
        if parts[0] == "resources":
            if len(parts) >= 2:
                if anchor_to_parent:
                    # Content writes anchor the semantic refresh at the written file's
                    # direct parent directory, so the changed file is a direct child of
                    # the DAG run root: its own L2 vector and the parent's L0/L1 are
                    # (re)generated from a single-directory run, while ancestor summaries
                    # refresh via the existing parent bubble. Collapsing to the project
                    # root instead would force the run to traverse the whole project
                    # subtree before the changed file's vector is even dispatched.
                    parent = parsed.parent
                    if parent is not None:
                        root_uri = parent.uri
                else:
                    root_uri = VikingURI.build("resources", parts[1])
        elif parts[0] == "user":
            if "resources" in parts:
                resources_idx = parts.index("resources")
                if len(parts) <= resources_idx + 1:
                    raise InvalidArgumentError(
                        f"resource write target must be inside a resource directory: {uri}"
                    )
                root_uri = VikingURI.build(*parts[: resources_idx + 2])
            else:
                try:
                    memories_idx = parts.index("memories")
                except ValueError as exc:
                    raise InvalidArgumentError(
                        f"write only supports memory or resource files under user scope: {uri}"
                    ) from exc
                if len(parts) <= memories_idx + 1:
                    raise InvalidArgumentError(
                        f"memory write target must be inside a memory type directory: {uri}"
                    )
                root_uri = VikingURI.build(*parts[: memories_idx + 2])

        stat = await self._safe_stat(root_uri, ctx=ctx, allow_not_found=_allow_not_found)
        if stat.get("not_found") or not stat.get("isDir"):
            parent = VikingURI(uri).parent
            if parent is None:
                raise InvalidArgumentError(f"could not resolve write root for {uri}")
            root_uri = parent.uri
        return root_uri
