# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: Apache-2.0
"""Coordinator for content write operations."""

from __future__ import annotations

import base64
import binascii
import os
from collections import defaultdict
from dataclasses import dataclass
from typing import Any, Dict, Optional

from openviking.core.namespace import (
    classify_uri,
    context_type_for_uri,
    relative_uri_path,
    uri_parts,
)
from openviking.resource.processing_mode import (
    DEFAULT_PROCESSING_MODE,
    VECTORS_ONLY,
    ProcessingMode,
    normalize_processing_mode,
)
from openviking.resource.watch_storage import is_watch_task_control_uri
from openviking.server.identity import RequestContext
from openviking.session.memory.memory_updater import MemoryUpdater
from openviking.session.memory.utils.memory_file_utils import MemoryFileUtils
from openviking.session.memory.utils.resource_refs import (
    RESOURCE_REF_SOURCE_CONTENT_WRITE,
    sync_memory_resource_refs,
)
from openviking.storage.abstract_overview import (
    AbstractOverviewFormatError,
    is_abstract_overview_uri,
    plan_abstract_overview_refresh,
    prepare_abstract_overview_write,
)
from openviking.storage.errors import LockAcquisitionError, ResourceBusyError
from openviking.storage.queuefs import SemanticMsg, get_queue_manager
from openviking.storage.queuefs.semantic_msg import build_semantic_coalesce_key
from openviking.storage.queuefs.semantic_ops.freshness_policy import FreshnessAction
from openviking.storage.viking_fs import VikingFS
from openviking.telemetry import get_current_telemetry
from openviking.telemetry.request_wait_tracker import get_request_wait_tracker
from openviking.telemetry.resource_summary import build_queue_status_payload
from openviking.utils.embedding_utils import vectorize_directory_meta, vectorize_file
from openviking.utils.path_safety import validate_safe_viking_uri_path
from openviking.utils.tags import normalize_search_tags
from openviking_cli.exceptions import (
    AlreadyExistsError,
    DeadlineExceededError,
    InvalidArgumentError,
    NotFoundError,
    OpenVikingError,
    ResourceExhaustedError,
)
from openviking_cli.utils import VikingURI
from openviking_cli.utils.config import get_openviking_config
from openviking_cli.utils.logger import get_logger

logger = get_logger(__name__)

_DERIVED_FILENAMES = frozenset({".relations.json"})
_CREATE_ALLOWED_EXTENSIONS = frozenset(
    {
        ".md",
        ".txt",
        ".json",
        ".yaml",
        ".yml",
        ".toml",
        ".py",
        ".js",
        ".ts",
    }
)
_BATCH_MAX_OPERATIONS = 256
_BATCH_MAX_FILE_BYTES = 8 * 1024 * 1024
_BATCH_MAX_TOTAL_BYTES = 16 * 1024 * 1024

# Subtrees directly under a user root that OpenViking manages itself; only
# memories/, resources/, and plain files may be written under a user root.
_USER_MANAGED_SUBTREES = frozenset({"skills", "peers", "privacy", "sessions"})


@dataclass(frozen=True)
class _BatchRefreshOutcome:
    queue_status: Optional[Dict[str, Any]]
    semantic_actions: tuple[FreshnessAction, ...] = ()
    memory_refreshed: bool = False
    embedding_requested: bool = False

    def statuses(self, *, wait: bool) -> tuple[str, str]:
        if self.semantic_actions:
            semantic_status = (
                "deferred"
                if all(action is FreshnessAction.MARK_PENDING for action in self.semantic_actions)
                else ("complete" if wait else "queued")
            )
        else:
            semantic_status = "complete" if self.memory_refreshed else "skipped"

        vector_work = bool(self.semantic_actions) or self.embedding_requested
        vector_status = ("complete" if wait else "queued") if vector_work else "skipped"
        return semantic_status, vector_status


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
        processing_mode: ProcessingMode = DEFAULT_PROCESSING_MODE,
    ) -> Dict[str, Any]:
        self._validate_mode(mode)
        processing_mode = normalize_processing_mode(processing_mode)
        normalized_uri = self._validate_uri_path(uri, field_name="uri")
        self._ensure_content_write_policy(normalized_uri)
        self._viking_fs._ensure_mutable_access(normalized_uri, ctx)

        if mode == "create":
            return await self._create_and_write(
                uri=normalized_uri,
                content=content,
                ctx=ctx,
                wait=wait,
                timeout=timeout,
                processing_mode=processing_mode,
            )

        stat = await self._safe_stat(normalized_uri, ctx=ctx)
        if stat.get("isDir"):
            raise InvalidArgumentError(
                f"write only supports existing files, got directory: {normalized_uri}"
            )

        context_type = context_type_for_uri(normalized_uri)
        root_uri = await self._resolve_root_uri(
            normalized_uri, ctx=ctx, anchor_to_parent=True
        )
        written_bytes = len(content.encode("utf-8"))
        telemetry_id = get_current_telemetry().telemetry_id

        if context_type == "memory" and not is_abstract_overview_uri(normalized_uri):
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
                processing_mode=processing_mode,
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
            processing_mode=processing_mode,
        )

    async def batch_write(
        self,
        *,
        root_uri: str,
        operations: list[dict[str, Any]],
        ctx: RequestContext,
        wait: bool = True,
        timeout: Optional[float] = None,
    ) -> Dict[str, Any]:
        """Write a bundle under one directory, then refresh it as a batch.

        Each operation follows the same create/replace/append semantics as ``write``;
        ``upsert`` is available for callers that already hold the desired final tree.
        Refresh runs only after every write and after releasing the tree lock, so derived
        summaries are generated once per batch.
        """
        normalized_root = self._validate_uri_path(root_uri, field_name="root_uri")
        await self._validate_batch_root(normalized_root, ctx=ctx)
        normalized_operations = self._normalize_batch_operations(
            normalized_root, operations, ctx=ctx
        )

        root_path = self._viking_fs._uri_to_path(normalized_root, ctx=ctx)
        try:
            lease = await self._viking_fs._async_agfs.pathlock_acquire_tree(root_path)
        except LockAcquisitionError as exc:
            raise ResourceBusyError(
                f"resource is busy and cannot be written now: {normalized_root}",
                uri=normalized_root,
            ) from exc

        created: list[str] = []
        updated: list[str] = []
        unchanged: list[str] = []
        refresh_kinds: dict[str, str] = {}
        sidecar_directories: set[str] = set()
        pending: list[tuple[dict[str, Any], bool, str]] = []
        write_error: Exception | None = None
        lock_released = False
        try:
            for operation in normalized_operations:
                uri = operation["uri"]
                stat = await self._safe_stat(uri, ctx=ctx, allow_not_found=True)
                exists = not stat.get("not_found")
                if exists and stat.get("isDir"):
                    raise InvalidArgumentError(f"batch-write target must be a file: {uri}")

                requested_mode = operation["mode"]
                write_mode = requested_mode
                if write_mode == "upsert":
                    write_mode = "replace" if exists else "create"

                if is_abstract_overview_uri(uri):
                    if not exists:
                        raise InvalidArgumentError(
                            f"cannot create generated abstract overview directly: {uri}"
                        )
                if write_mode == "create" and exists:
                    raise AlreadyExistsError(uri, "file")
                if write_mode in {"replace", "append"} and not exists:
                    raise NotFoundError(uri, "file")
                pending.append((operation, exists, write_mode))

            for operation, existed, write_mode in pending:
                uri = operation["uri"]
                try:
                    if context_type_for_uri(uri) == "memory":
                        await self._viking_fs.write_file(
                            uri,
                            operation["content"],
                            ctx=ctx,
                            lease_ref=lease,
                        )
                    else:
                        await self._write_in_place(
                            uri,
                            operation["content"],
                            mode=write_mode,
                            ctx=ctx,
                            lease_ref=lease,
                        )
                except Exception as exc:
                    write_error = exc
                    break
                if existed:
                    updated.append(uri)
                    if is_abstract_overview_uri(uri):
                        parent = VikingURI(uri).parent
                        if parent is not None:
                            sidecar_directories.add(parent.uri)
                    else:
                        refresh_kinds[uri] = "modified"
                else:
                    created.append(uri)
                    refresh_kinds[uri] = "added"
        finally:
            await self._viking_fs._async_agfs.pathlock_release(lease)
            lock_released = True

        assert lock_released
        telemetry_id = get_current_telemetry().telemetry_id
        request_registered = False
        refresh_outcome: Optional[_BatchRefreshOutcome] = None
        try:
            if refresh_kinds or sidecar_directories:
                if wait and telemetry_id:
                    get_request_wait_tracker().register_request(telemetry_id)
                    request_registered = True
                try:
                    for directory_uri in sorted(sidecar_directories):
                        await self._vectorize_semantic_directory(
                            directory_uri=directory_uri, ctx=ctx
                        )
                    refresh_result = (
                        await self._refresh_batch(
                            refresh_kinds=refresh_kinds,
                            ctx=ctx,
                            wait=wait,
                            timeout=timeout,
                            telemetry_id=telemetry_id,
                        )
                        if refresh_kinds
                        else (
                            await self._wait_for_request(telemetry_id=telemetry_id, timeout=timeout)
                            if wait
                            else None
                        )
                    )
                    if isinstance(refresh_result, _BatchRefreshOutcome):
                        refresh_outcome = refresh_result
                        queue_status = refresh_result.queue_status
                    else:
                        # Preserve compatibility for tests/extensions replacing
                        # the private refresh hook with its historical result.
                        queue_status = refresh_result
                except Exception as exc:
                    if write_error is not None:
                        logger.error(
                            "Batch refresh failed while preserving an earlier write error",
                            exc_info=True,
                        )
                        queue_status = None
                    else:
                        if isinstance(exc, DeadlineExceededError):
                            raise
                        cause = str(exc).strip() or type(exc).__name__
                        raise OpenVikingError(
                            "Content is already at the requested state, but semantic/index "
                            f"refresh failed: {cause}. Re-run the same batch-write or ov compile "
                            "command to rewrite the final state and retry the refresh.",
                            code="REFRESH_FAILED",
                            details={
                                "root_uri": normalized_root,
                                "created": created,
                                "updated": updated,
                                "unchanged": unchanged,
                                "cause": cause,
                            },
                        ) from exc
            else:
                queue_status = None
        finally:
            if request_registered:
                get_request_wait_tracker().cleanup(telemetry_id)

        if write_error is not None:
            raise write_error
        result = {
            "root_uri": normalized_root,
            "created": created,
            "updated": updated,
            "unchanged": unchanged,
            "queue_status": queue_status,
        }
        if refresh_outcome is not None:
            semantic_status, vector_status = refresh_outcome.statuses(wait=wait)
            result["semantic_status"] = semantic_status
            result["vector_status"] = vector_status
        return result

    def _validate_uri_path(self, uri: str, *, field_name: str) -> str:
        try:
            return validate_safe_viking_uri_path(uri)
        except ValueError as exc:
            raise InvalidArgumentError(f"invalid {field_name}: {exc}") from exc

    async def _validate_batch_root(self, root_uri: str, *, ctx: RequestContext) -> None:
        classification = classify_uri(root_uri)
        parts = uri_parts(root_uri)
        if classification.context_type not in {"resource", "memory"}:
            raise InvalidArgumentError("batch-write root must be a resource or memory directory")
        if classification.context_type == "memory":
            if (
                classification.content_index is None
                or len(parts) <= classification.content_index + 1
            ):
                raise InvalidArgumentError(
                    "batch-write root must be inside a memory type directory"
                )
        elif parts == ["resources"] or (
            classification.content_index is not None
            and len(parts) <= classification.content_index + 1
        ):
            raise InvalidArgumentError("batch-write root must be inside a resource directory")
        self._viking_fs._ensure_mutable_access(root_uri, ctx)
        stat = await self._safe_stat(root_uri, ctx=ctx)
        if not stat.get("isDir"):
            raise InvalidArgumentError(
                f"batch-write root must be an existing directory: {root_uri}"
            )

    def _normalize_batch_operations(
        self,
        root_uri: str,
        operations: list[dict[str, Any]],
        *,
        ctx: RequestContext,
    ) -> list[dict[str, Any]]:
        if not operations:
            raise InvalidArgumentError("batch-write operations must not be empty")
        if len(operations) > _BATCH_MAX_OPERATIONS:
            raise ResourceExhaustedError(
                f"batch-write supports at most {_BATCH_MAX_OPERATIONS} operations"
            )

        context_type = context_type_for_uri(root_uri)
        normalized: list[dict[str, Any]] = []
        seen: set[str] = set()
        total_bytes = 0
        for raw in operations:
            if not isinstance(raw, dict):
                raise InvalidArgumentError("batch-write operation must be an object")
            uri = self._validate_uri_path(raw.get("uri", ""), field_name="operation uri")
            if uri in seen:
                raise InvalidArgumentError(f"duplicate batch-write target: {uri}")
            seen.add(uri)
            if not relative_uri_path(root_uri, uri):
                raise InvalidArgumentError(f"batch-write target is outside root_uri: {uri}")
            if context_type_for_uri(uri) != context_type:
                raise InvalidArgumentError(
                    f"batch-write target has a different context type: {uri}"
                )
            self._ensure_content_write_policy(uri)
            self._viking_fs._ensure_mutable_access(uri, ctx)

            has_content = "content" in raw
            has_content_base64 = "content_base64" in raw
            if has_content == has_content_base64:
                raise InvalidArgumentError(
                    f"batch-write requires exactly one of content or content_base64: {uri}"
                )
            if has_content:
                content = raw.get("content")
                if not isinstance(content, str):
                    raise InvalidArgumentError(f"batch-write content must be a string: {uri}")
                encoded_content = content.encode("utf-8")
            else:
                if context_type == "memory":
                    raise InvalidArgumentError(
                        f"batch-write binary content is not supported for memories: {uri}"
                    )
                content_base64 = raw.get("content_base64")
                if not isinstance(content_base64, str):
                    raise InvalidArgumentError(
                        f"batch-write content_base64 must be a string: {uri}"
                    )
                try:
                    encoded_content = base64.b64decode(content_base64, validate=True)
                except (binascii.Error, ValueError) as exc:
                    raise InvalidArgumentError(
                        f"batch-write content_base64 is invalid: {uri}"
                    ) from exc
                content = encoded_content
            content_size = len(encoded_content)
            if content_size > _BATCH_MAX_FILE_BYTES:
                raise ResourceExhaustedError(f"batch-write file exceeds size limit: {uri}")
            total_bytes += content_size
            if total_bytes > _BATCH_MAX_TOTAL_BYTES:
                raise ResourceExhaustedError("batch-write total content exceeds size limit")

            mode = raw.get("mode", "replace")
            self._validate_batch_mode(mode)
            if has_content_base64 and mode == "append":
                raise InvalidArgumentError(
                    f"batch-write append does not support binary content: {uri}"
                )
            if context_type == "memory" and mode in {"create", "upsert"}:
                self._validate_create_extension(uri)
            if context_type == "memory" and mode == "append":
                raise InvalidArgumentError("batch-write append is not supported for memories")
            normalized.append(
                {
                    "uri": uri,
                    "content": content,
                    "mode": mode,
                }
            )
        return sorted(normalized, key=lambda operation: operation["uri"])

    async def _refresh_batch(
        self,
        *,
        refresh_kinds: dict[str, str],
        ctx: RequestContext,
        wait: bool,
        timeout: Optional[float],
        telemetry_id: str,
    ) -> _BatchRefreshOutcome:
        resource_groups: dict[tuple[str, str], dict[str, list[str]]] = defaultdict(
            lambda: {"added": [], "modified": []}
        )
        memory_groups: dict[str, list[str]] = defaultdict(list)
        for uri, change_type in sorted(refresh_kinds.items()):
            context_type = context_type_for_uri(uri)
            if context_type == "memory":
                parent = VikingURI(uri).parent
                memory_groups[parent.uri if parent is not None else uri].append(uri)
                continue
            refresh_root = await self._resolve_root_uri(uri, ctx=ctx, anchor_to_parent=True)
            resource_groups[(refresh_root, context_type)][change_type].append(uri)

        semantic_actions = []
        for (refresh_root, context_type), changes in sorted(resource_groups.items()):
            action = await self._enqueue_semantic_refresh_changes(
                root_uri=refresh_root,
                context_type=context_type,
                changes=changes,
                ctx=ctx,
                force_refresh=wait,
            )
            semantic_actions.append(action)

        embedding_requested = False
        for directory_uri, uris in sorted(memory_groups.items()):
            await MemoryUpdater.refresh_schema_overview(
                viking_fs=self._viking_fs,
                directory_uri=directory_uri,
                ctx=ctx,
                strict=True,
            )
            for uri in uris:
                requested = await MemoryUpdater.refresh_file_embedding(
                    viking_fs=self._viking_fs,
                    vikingdb=self._vikingdb,
                    uri=uri,
                    memory_type=MemoryUpdater.memory_type_from_uri(uri),
                    ctx=ctx,
                    strict=True,
                )
                embedding_requested = embedding_requested or requested

        if not wait or (not resource_groups and not embedding_requested):
            return _BatchRefreshOutcome(
                queue_status=None,
                semantic_actions=tuple(semantic_actions),
                memory_refreshed=bool(memory_groups),
                embedding_requested=embedding_requested,
            )
        queue_status = await self._wait_for_request(
            telemetry_id=telemetry_id,
            timeout=timeout,
        )
        self._raise_refresh_errors(queue_status)
        return _BatchRefreshOutcome(
            queue_status=queue_status,
            semantic_actions=tuple(semantic_actions),
            memory_refreshed=bool(memory_groups),
            embedding_requested=embedding_requested,
        )

    async def _enqueue_semantic_refresh_changes(
        self,
        *,
        root_uri: str,
        context_type: str,
        changes: dict[str, list[str]],
        ctx: RequestContext,
        target_uri: str = "",
        recursive: bool = False,
        force_refresh: bool = False,
    ) -> FreshnessAction:
        changed_entries = len({uri for values in changes.values() for uri in values})
        semantic_config = get_openviking_config().semantic
        decision = await plan_abstract_overview_refresh(
            viking_fs=self._viking_fs,
            dir_uri=root_uri,
            changed_entries=changed_entries,
            ctx=ctx,
            overview_sample_limit=getattr(semantic_config, "overview_sample_limit", 32),
            refresh_ratio=getattr(semantic_config, "freshness_refresh_ratio", 0.10),
            force_refresh=force_refresh,
        )
        aggregate_directory = decision.action is FreshnessAction.REFRESH_NOW
        has_live_files = any(changes.get(kind) for kind in ("added", "modified"))
        if not aggregate_directory and not has_live_files:
            return decision.action
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
                if aggregate_directory and context_type in {"resource", "skill"}
                else ""
            ),
            changes={
                change_type: sorted(changes.get(change_type, []))
                for change_type in ("added", "modified")
                if changes.get(change_type)
            },
            generation_trigger="content_write",
            aggregate_directory=aggregate_directory,
        )
        if msg.telemetry_id:
            get_request_wait_tracker().register_semantic_root(msg.telemetry_id, msg.id)
        try:
            await semantic_queue.enqueue(msg)
        except Exception as exc:
            if msg.telemetry_id:
                get_request_wait_tracker().mark_semantic_failed(msg.telemetry_id, msg.id, str(exc))
            raise
        return decision.action

    @staticmethod
    def _raise_refresh_errors(queue_status: Dict[str, Any]) -> None:
        for name in ("Semantic", "Embedding"):
            status = queue_status.get(name, {}) if isinstance(queue_status, dict) else {}
            if isinstance(status, dict) and (
                int(status.get("error_count", 0) or 0) > 0 or bool(status.get("errors"))
            ):
                raise OpenVikingError(
                    f"Batch write {name.lower()} refresh failed",
                    code="INTERNAL",
                    details={"queue_status": queue_status},
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
        self._validate_tag_mode(mode)
        normalized_tags = normalize_search_tags(tags, discard_invalid=True)
        stat = await self._safe_stat(uri, ctx=ctx)
        if stat.get("isDir"):
            return await self._set_directory_tags(
                uri=uri,
                tags=normalized_tags,
                mode=mode,
                recursive=recursive,
                ctx=ctx,
            )
        return await self._set_single_uri_tags(
            uri=uri,
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
        processing_mode: ProcessingMode = DEFAULT_PROCESSING_MODE,
    ) -> Dict[str, Any]:
        lock_path = self._viking_fs._uri_to_path(uri, ctx=ctx)
        try:
            lease = await self._viking_fs._async_agfs.pathlock_acquire_exact(lock_path)
        except LockAcquisitionError as exc:
            raise ResourceBusyError(
                f"resource is busy and cannot be written now: {uri}",
                uri=uri,
            ) from exc

        previous_content: Optional[str] = None
        content_written = False
        post_process_started = False
        lock_released = False
        vector_enqueued = False
        refresh_action: Optional[FreshnessAction] = None
        try:
            if mode != "create":
                previous_content = await self._viking_fs.read_file(uri, ctx=ctx)
            elif is_abstract_overview_uri(uri):
                raise InvalidArgumentError(
                    f"cannot create generated abstract overview directly: {uri}"
                )
            if wait and telemetry_id:
                get_request_wait_tracker().register_request(telemetry_id)
            await self._write_in_place(
                uri,
                content,
                mode=mode,
                ctx=ctx,
                lease_ref=lease,
                existing_raw=previous_content,
            )
            content_written = True
            if is_abstract_overview_uri(uri):
                vector_enqueued = await self._vectorize_abstract_overview(uri=uri, ctx=ctx)
                post_process_started = True
            elif processing_mode == VECTORS_ONLY:
                vector_enqueued = await self._vectorize_written_file(
                    uri=uri,
                    context_type=context_type,
                    ctx=ctx,
                )
                post_process_started = True
            else:
                refresh_action = await self._enqueue_semantic_refresh(
                    root_uri=root_uri,
                    changed_uri=uri,
                    context_type=context_type,
                    ctx=ctx,
                    change_type="added" if mode == "create" else "modified",
                    force_refresh=wait,
                )
                post_process_started = True
            await self._viking_fs._async_agfs.pathlock_release(lease)
            lock_released = True
            queue_status = (
                await self._wait_for_request(telemetry_id=telemetry_id, timeout=timeout)
                if wait
                else None
            )
            result_kwargs = {}
            if is_abstract_overview_uri(uri):
                _, vector_status = (
                    self._refresh_statuses(wait=wait, queue_status=queue_status)
                    if vector_enqueued
                    else ("skipped", "skipped")
                )
                result_kwargs = {
                    "semantic_status": "skipped",
                    "vector_status": vector_status,
                }
            elif processing_mode == VECTORS_ONLY:
                if vector_enqueued:
                    _, vector_status = self._refresh_statuses(
                        wait=wait,
                        queue_status=queue_status,
                    )
                else:
                    vector_status = "skipped"
                result_kwargs = {
                    "semantic_status": "skipped",
                    "vector_status": vector_status,
                }
            elif refresh_action is FreshnessAction.MARK_PENDING:
                # Changed-file semantic/vector work may still be queued, while
                # the directory aggregation itself is intentionally deferred.
                _, vector_status = self._refresh_statuses(
                    wait=wait, queue_status=queue_status
                )
                result_kwargs = {
                    "semantic_status": "deferred",
                    "vector_status": vector_status,
                }
            return self._build_write_result(
                uri=uri,
                root_uri=root_uri,
                context_type=context_type,
                mode=mode,
                written_bytes=written_bytes,
                wait=wait,
                queue_status=queue_status,
                **result_kwargs,
            )
        except Exception:
            if not post_process_started and content_written:
                await self._rollback_direct_write(
                    uri=uri,
                    previous_content=previous_content,
                    mode=mode,
                    ctx=ctx,
                    lease_ref=lease,
                )
            if not lock_released:
                await self._viking_fs._async_agfs.pathlock_release(lease)
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
        lease_ref: Optional[Dict[str, Any]] = None,
    ) -> None:
        try:
            if mode == "create":
                await self._viking_fs.rm(uri, ctx=ctx, lease_ref=lease_ref)
                return
            if previous_content is not None:
                await self._viking_fs.write_file(
                    uri,
                    previous_content,
                    ctx=ctx,
                    lease_ref=lease_ref,
                )
        except Exception:
            logger.error("Failed to rollback direct content write for %s", uri, exc_info=True)

    async def _vectorize_written_file(
        self,
        *,
        uri: str,
        context_type: str,
        ctx: RequestContext,
    ) -> bool:
        parent = VikingURI(uri).parent
        if parent is None:
            return False
        name = uri.rstrip("/").rsplit("/", 1)[-1]
        return await vectorize_file(
            file_path=uri,
            summary_dict={"name": name, "summary": ""},
            parent_uri=parent.uri,
            context_type=context_type,
            ctx=ctx,
        )

    async def _vectorize_abstract_overview(self, *, uri: str, ctx: RequestContext) -> bool:
        """Re-index a manually edited L0/L1 body without regenerating it."""

        parent = VikingURI(uri).parent
        if parent is None:
            return False
        await self._vectorize_semantic_directory(directory_uri=parent.uri, ctx=ctx)
        return True

    async def _vectorize_semantic_directory(
        self, *, directory_uri: str, ctx: RequestContext
    ) -> None:
        """Re-index the semantic levels that exist for one directory."""

        async def read_if_exists(uri: str) -> Optional[str]:
            try:
                return await self._viking_fs.read_file(uri, ctx=ctx)
            except Exception as exc:
                if self._is_not_found(exc):
                    return None
                raise

        abstract = await read_if_exists(f"{directory_uri}/.abstract.md")
        overview = await read_if_exists(f"{directory_uri}/.overview.md")
        if abstract is None and overview is None:
            return
        await vectorize_directory_meta(
            uri=directory_uri,
            abstract=abstract or "",
            overview=overview or "",
            context_type=context_type_for_uri(directory_uri),
            ctx=ctx,
            include_abstract=abstract is not None,
            include_overview=overview is not None,
        )

    def _validate_mode(self, mode: str) -> None:
        if mode not in {"replace", "append", "create"}:
            raise InvalidArgumentError(f"unsupported write mode: {mode}")

    def _validate_batch_mode(self, mode: str) -> None:
        if not isinstance(mode, str) or mode not in {"replace", "append", "create", "upsert"}:
            raise InvalidArgumentError(f"unsupported batch-write mode: {mode}")

    def _validate_tag_mode(self, mode: str) -> None:
        if mode not in {"replace", "append"}:
            raise InvalidArgumentError(f"unsupported tag mode: {mode}")

    def _ensure_content_write_policy(self, uri: str) -> None:
        name = uri.rstrip("/").split("/")[-1]
        if name in _DERIVED_FILENAMES:
            raise InvalidArgumentError(f"cannot write derived semantic file directly: {uri}")
        if is_watch_task_control_uri(uri):
            raise InvalidArgumentError(f"cannot write watch task control file directly: {uri}")

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
        processing_mode: ProcessingMode,
    ) -> Dict[str, Any]:
        if is_abstract_overview_uri(uri):
            raise InvalidArgumentError(f"cannot create generated abstract overview directly: {uri}")
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
                processing_mode=processing_mode,
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
            processing_mode=processing_mode,
        )

    async def _write_in_place(
        self,
        uri: str,
        content: str,
        *,
        mode: str,
        ctx: RequestContext,
        lease_ref: Optional[Dict[str, Any]] = None,
        existing_raw: Optional[str] = None,
    ) -> None:
        if is_abstract_overview_uri(uri):
            current_raw = (
                existing_raw
                if existing_raw is not None
                else await self._viking_fs.read_file(uri, ctx=ctx)
            )
            rendered = self._prepare_abstract_overview_content(
                uri=uri,
                current_raw=current_raw,
                requested_raw=content,
                mode=mode,
            )
            await self._viking_fs.write_file(uri, rendered, ctx=ctx, lease_ref=lease_ref)
            return

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
                lease_ref=lease_ref,
            )
            return

        if mode == "append":
            # Plain concatenation for resource/skill files: MEMORY_FIELDS is a
            # reserved trailer of memory namespaces only (see content_visibility),
            # so non-memory appends must not round-trip through MemoryFileUtils
            # (which strips trailing newlines and injects a metadata trailer).
            existing_raw = await self._viking_fs.read_file(uri, ctx=ctx)
            await self._viking_fs.write_file(
                uri, existing_raw + content, ctx=ctx, lease_ref=lease_ref
            )
            return
        await self._viking_fs.write_file(uri, content, ctx=ctx, lease_ref=lease_ref)

    @staticmethod
    def _prepare_abstract_overview_content(
        *, uri: str, current_raw: str | bytes, requested_raw: str | bytes, mode: str
    ) -> str:
        try:
            return prepare_abstract_overview_write(uri, current_raw, requested_raw, mode=mode)
        except AbstractOverviewFormatError as exc:
            raise InvalidArgumentError(str(exc)) from exc

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
        force_refresh: bool = False,
    ) -> FreshnessAction:
        return await self._enqueue_semantic_refresh_changes(
            root_uri=root_uri,
            context_type=context_type,
            ctx=ctx,
            changes={change_type: [changed_uri]},
            target_uri=target_uri,
            recursive=recursive,
            force_refresh=force_refresh,
        )

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
        processing_mode: ProcessingMode = DEFAULT_PROCESSING_MODE,
    ) -> Dict[str, Any]:
        del processing_mode

        lock_path = self._viking_fs._uri_to_path(uri, ctx=ctx)
        try:
            lease = await self._viking_fs._async_agfs.pathlock_acquire_exact(lock_path)
        except LockAcquisitionError as exc:
            raise ResourceBusyError(
                f"resource is busy and cannot be written now: {uri}",
                uri=uri,
            ) from exc

        released = False
        request_registered = False
        try:
            await self._write_in_place(uri, content, mode=mode, ctx=ctx, lease_ref=lease)
            await self._viking_fs._async_agfs.pathlock_release(lease)
            released = True
            if wait and telemetry_id and self._vikingdb_has_queue():
                get_request_wait_tracker().register_request(telemetry_id)
                request_registered = True
            overview_refreshed = await MemoryUpdater.refresh_schema_overview(
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
                overview_status="complete" if overview_refreshed else "skipped",
            )
        except Exception:
            if not released:
                await self._viking_fs._async_agfs.pathlock_release(lease)
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
                if anchor_to_parent:
                    parent = parsed.parent
                    if parent is not None:
                        root_uri = parent.uri
                else:
                    root_uri = VikingURI.build(*parts[: resources_idx + 2])
            elif "memories" in parts:
                memories_idx = parts.index("memories")
                if len(parts) <= memories_idx + 1:
                    raise InvalidArgumentError(
                        f"memory write target must be inside a memory type directory: {uri}"
                    )
                if anchor_to_parent:
                    parent = parsed.parent
                    if parent is not None:
                        root_uri = parent.uri
                else:
                    root_uri = VikingURI.build(*parts[: memories_idx + 2])
            else:
                # Plain files directly under the user root are allowed (e.g. a
                # persona file at viking://user/<user>/persona.md); the managed
                # subtrees (skills/, peers/, privacy/, sessions/) are not.
                if len(parts) <= 2 or parts[2] in _USER_MANAGED_SUBTREES:
                    raise InvalidArgumentError(
                        "user-scope writes need a file under memories/, resources/, "
                        f"or directly at the user root: {uri}"
                    )
                parent = parsed.parent
                if parent is not None:
                    root_uri = parent.uri

        stat = await self._safe_stat(root_uri, ctx=ctx, allow_not_found=_allow_not_found)
        if stat.get("not_found") or not stat.get("isDir"):
            parent = VikingURI(uri).parent
            if parent is None:
                raise InvalidArgumentError(f"could not resolve write root for {uri}")
            root_uri = parent.uri
        return root_uri
