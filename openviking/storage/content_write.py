# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: Apache-2.0
"""Coordinator for content write operations."""

from __future__ import annotations

import asyncio
import base64
import binascii
import hashlib
import os
import sys
from collections import defaultdict
from copy import deepcopy
from dataclasses import dataclass, field, replace
from functools import partial
from typing import Any, Awaitable, Callable, Dict, Optional
from uuid import uuid4

from openviking.core.context import ContextLevel
from openviking.core.namespace import (
    classify_uri,
    context_type_for_uri,
    relative_uri_path,
    uri_parts,
)
from openviking.core.namespace_policy import (
    NamespaceProcessingMode,
    namespace_policy_for_uri,
)
from openviking.privacy.sanitizer import PrivacySanitizer
from openviking.resource.processing_mode import (
    DEFAULT_PROCESSING_MODE,
    ProcessingMode,
    normalize_processing_mode,
)
from openviking.resource.watch_storage import is_watch_task_control_uri
from openviking.server.identity import RequestContext, Role
from openviking.service.task_tracker import (
    TaskStatus,
    get_task_tracker,
)
from openviking.service.task_tracker_concurrency import run_to_completion
from openviking.service.task_work_index import (
    bind_task_context,
    get_enqueued_work_ids,
    get_task_context,
)
from openviking.session.memory.account_templates import normalize_account_memory_template_write
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
from openviking.storage.acl import AclAction, AclSpec
from openviking.storage.context_update_execution import (
    enqueue_direct_index_actions,
)
from openviking.storage.context_update_plan import (
    ContentTreeAction,
    ContentTreeOperation,
    ContextUpdatePlan,
    DirectIndexAction,
    FileRefreshIntent,
    build_context_update_plan_from_snapshot,
    execute_content_tree_actions,
)
from openviking.storage.errors import LockAcquisitionError, ResourceBusyError
from openviking.storage.index_action import FieldPatch, IndexAction
from openviking.storage.internal_names import is_storage_internal_name
from openviking.storage.pathlock_transfer import PathLockGuard
from openviking.storage.queuefs import SemanticMsg, get_queue_manager
from openviking.storage.queuefs.semantic_msg import build_semantic_coalesce_key
from openviking.storage.queuefs.semantic_ops.freshness_policy import FreshnessAction
from openviking.storage.resource_diff import (
    ContentState,
    IndexState,
    InlineBytesStore,
    build_rnfv_snapshot,
    make_inline_file_inventory,
    resolve_resource_diff,
)
from openviking.storage.resource_rnfv import (
    FormalEntry,
    RequestIntent,
    canonical_vector_records_by_level,
)
from openviking.storage.resource_target import AgfsResourceTarget
from openviking.storage.vector_ids import vector_record_id
from openviking.storage.viking_fs import VikingFS
from openviking.telemetry import get_current_telemetry
from openviking.utils.content_hash import content_md5
from openviking.utils.embedding_utils import vectorize_directory_meta
from openviking.utils.ingest_options import IngestOptions
from openviking.utils.path_safety import normalize_storage_target_uri, validate_safe_viking_uri_path
from openviking.utils.summarizer import FileRefreshRequest, Summarizer
from openviking.utils.tags import normalize_search_tags
from openviking_cli.exceptions import (
    ConflictError,
    DeadlineExceededError,
    InvalidArgumentError,
    NotFoundError,
    OpenVikingError,
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

# Subtrees directly under a user root that OpenViking manages itself; only
# memories/, resources/, and plain files may be written under a user root.
_USER_MANAGED_SUBTREES = frozenset({"skills", "peers", "privacy", "sessions"})


@dataclass(frozen=True)
class _InlineArtifactRef:
    """Opaque artifact handle for the inline single-file RNFV path.

    The RNFV plan pipeline only needs an object to pass back to
    ``InlineBytesStore.read_bytes``; the write path holds the bytes directly, so
    no real parse-output backend is involved.
    """

    uri: str
    backend: str = "inline"


@dataclass(frozen=True)
class _LockedWriteTarget:
    """One locked target classification shared by all single-file write paths."""

    preexisting: bool
    formal_snapshot: tuple[dict[str, FormalEntry], bool]

    @property
    def is_new_file(self) -> bool:
        return not self.preexisting


@dataclass(frozen=True)
class _PreparedInlineResource:
    """One file-scoped RNFV plan compiled from final bytes and locked facts."""

    uri: str
    existed: bool
    final_bytes: bytes
    plan: ContextUpdatePlan
    store: InlineBytesStore
    artifact_ref: _InlineArtifactRef
    target: AgfsResourceTarget
    previous_abstract: str = ""
    ingest_options: IngestOptions = field(default_factory=IngestOptions)


def _queue_has_errors(queue_status: Optional[Dict[str, Any]], name: str) -> bool:
    if not isinstance(queue_status, dict):
        return False
    status = queue_status.get(name, {})
    if not isinstance(status, dict):
        return False
    try:
        return int(status.get("error_count", 0) or 0) > 0
    except (TypeError, ValueError):
        return bool(status.get("errors"))


def _result_has_queue_errors(result: Any) -> bool:
    if not isinstance(result, dict):
        return False
    queue_status = result.get("queue_status")
    return any(_queue_has_errors(queue_status, name) for name in ("Semantic", "Embedding"))


_ROOT_OWNS_FAILURE_ATTR = "_openviking_content_write_root_owns_failure"


def _mark_root_owns_failure(exc: BaseException) -> None:
    try:
        setattr(exc, _ROOT_OWNS_FAILURE_ATTR, True)
    except Exception:
        pass


def _root_owns_failure(exc: BaseException) -> bool:
    return bool(getattr(exc, _ROOT_OWNS_FAILURE_ATTR, False))


def _failure_payload(exc: BaseException) -> Dict[str, Any]:
    payload: Dict[str, Any] = {
        "message": str(exc) or type(exc).__name__,
        "type": type(exc).__name__,
    }
    if isinstance(exc, OpenVikingError):
        payload["code"] = exc.code
        payload["details"] = exc.details
    return payload


@dataclass(frozen=True)
class _BatchRefreshOutcome:
    queue_status: Optional[Dict[str, Any]]
    semantic_actions: tuple[FreshnessAction, ...] = ()
    memory_refreshed: bool = False
    embedding_requested: bool = False

    def statuses(self, *, wait: bool) -> tuple[str, str]:
        if self.semantic_actions:
            if any(action is FreshnessAction.REFRESH_NOW for action in self.semantic_actions):
                semantic_status = "complete" if wait else "queued"
            elif any(action is FreshnessAction.MARK_PENDING for action in self.semantic_actions):
                semantic_status = "deferred"
            else:
                semantic_status = "skipped"
        else:
            semantic_status = "complete" if self.memory_refreshed else "skipped"

        vector_work = bool(self.semantic_actions) or self.embedding_requested
        vector_status = ("complete" if wait else "queued") if vector_work else "skipped"
        return semantic_status, vector_status


class ContentWriteCoordinator:
    """Write a file (create or modify) and trigger downstream maintenance."""

    def __init__(
        self,
        viking_fs: VikingFS,
        vikingdb: Any = None,
        privacy_sanitizer: PrivacySanitizer | None = None,
        commit_content: Callable[..., Awaitable[Any]] | None = None,
        commit_batch: Callable[..., Awaitable[None]] | None = None,
    ):
        self._viking_fs = viking_fs
        self._vikingdb = vikingdb
        self._privacy_sanitizer = privacy_sanitizer
        self._content_commit = commit_content
        self._batch_commit = commit_batch

    async def _commit_content(self, uris: list[str], lease: Any, write) -> Any:
        """Write bytes and managed versions under the lease before refresh."""
        if self._content_commit is not None:
            return await self._content_commit(uris, lease_ref=lease, write=write)
        return await write()

    async def _sanitize_text(
        self,
        content: str,
        *,
        uri: str,
        ctx: RequestContext,
    ) -> str:
        if self._privacy_sanitizer is None:
            return content
        result = await self._privacy_sanitizer.sanitize(content, ctx=ctx, uri=uri)
        if result.ambiguous_references:
            raise InvalidArgumentError(
                "Content contains a Privacy value mapped to multiple fields; "
                "remove the duplicate mapping or insert the intended reference explicitly",
                details={
                    "uri": uri,
                    "candidate_references": [
                        list(candidates) for candidates in result.ambiguous_references
                    ],
                },
            )
        return result.content

    async def _sanitize_batch_operations(
        self,
        operations: list[dict[str, Any]],
        *,
        ctx: RequestContext,
    ) -> None:
        if self._privacy_sanitizer is None:
            return
        for operation in operations:
            content = operation.get("content")
            uri = operation.get("uri")
            if isinstance(content, str) and isinstance(uri, str):
                operation["content"] = await self._sanitize_text(content, uri=uri, ctx=ctx)

    async def write(
        self,
        *,
        uri: str,
        content: str | bytes,
        ctx: RequestContext,
        mode: str = "replace",
        wait: bool = False,
        processing_mode: ProcessingMode = DEFAULT_PROCESSING_MODE,
        tags: list[str] | None = None,
        tag_mode: str = "replace",
        acl: AclSpec | Dict[str, Any] | None = None,
    ) -> Dict[str, Any]:
        task = None
        task_id = None

        async def create_root_task() -> Any:
            nonlocal task, task_id
            if task is None:
                task_id = task_id or str(uuid4())
                task = await get_task_tracker().create(
                    "content_write",
                    account_id=ctx.account_id,
                    user_id=ctx.user.user_id,
                    resource_id=uri,
                    task_id=task_id,
                )
            if task is None:
                raise RuntimeError("Content write Task was not created")
            return task

        try:
            result = await self._write(
                uri=uri,
                content=content,
                ctx=ctx,
                mode=mode,
                wait=wait,
                processing_mode=processing_mode,
                tags=tags,
                tag_mode=tag_mode,
                acl=acl,
                root_operation="content_write",
                root_task_factory=create_root_task,
            )
        except BaseException as exc:
            if task_id is not None and not _root_owns_failure(exc):
                await self._fail_content_write_task(
                    task.task_id if task is not None else task_id, exc, ctx=ctx
                )
            raise
        if task is None:
            raise RuntimeError("Content write completed without creating a root Task")
        return await self._return_or_wait_for_content_write_task(
            task.task_id,
            result,
            ctx=ctx,
            wait=wait,
        )

    async def _fail_content_write_task(
        self,
        task_id: str,
        exc: BaseException,
        *,
        ctx: RequestContext,
    ) -> None:
        await run_to_completion(
            lambda: get_task_tracker().fail(
                task_id,
                str(exc) or type(exc).__name__,
                account_id=ctx.account_id,
                user_id=ctx.user.user_id,
            )
        )

    async def _return_or_wait_for_content_write_task(
        self,
        task_id: str,
        result: Dict[str, Any],
        *,
        ctx: RequestContext,
        wait: bool,
        accept_failed_result: Any = None,
    ) -> Dict[str, Any]:
        if not wait:
            result.setdefault("task_id", task_id)
            return result
        terminal = await self._wait_for_content_write_terminal(task_id, ctx=ctx)
        terminal_result = (
            deepcopy(terminal.result) if isinstance(terminal.result, dict) else deepcopy(result)
        )
        if isinstance(terminal_result, dict):
            terminal_result.pop("task_id", None)
        if terminal.status == TaskStatus.FAILED and not (
            accept_failed_result is not None and accept_failed_result(terminal_result)
        ):
            raise OpenVikingError(
                terminal.error or "Task processing failed",
                code="PROCESSING_ERROR",
                details={"task_id": task_id},
            )
        if terminal.status == TaskStatus.CANCELLED:
            raise OpenVikingError(
                "Task processing was cancelled",
                code="PROCESSING_ERROR",
                details={"task_id": task_id, "status": "cancelled"},
            )
        return terminal_result

    async def _wait_for_content_write_terminal(
        self,
        task_id: str,
        *,
        ctx: RequestContext,
    ) -> Any:
        tracker = get_task_tracker()
        try:
            return await tracker.wait(
                task_id,
                account_id=ctx.account_id,
                user_id=ctx.user.user_id,
            )
        except (TimeoutError, asyncio.TimeoutError) as exc:
            raise DeadlineExceededError("queue processing", None) from exc

    async def _enqueue_content_write_root(
        self,
        *,
        operation: str,
        resource_id: str,
        result: Dict[str, Any],
        refresh: Dict[str, Any],
        ctx: RequestContext,
        telemetry_id: str,
        lock_handoff: dict[str, Any] | None = None,
        lock_scope: str = "descendants",
        failure: dict[str, Any] | None = None,
        root_task_factory: Callable[[], Awaitable[Any]] | None = None,
    ) -> str:
        context = get_task_context()
        if context is None:
            if root_task_factory is None:
                raise RuntimeError("TaskContext is required for content-write root work")
            task = await root_task_factory()
            with bind_task_context(
                task.task_id,
                ctx.account_id,
                ctx.user.user_id,
                enqueued_work=[],
            ):
                return await self._enqueue_content_write_root(
                    operation=operation,
                    resource_id=resource_id,
                    result=result,
                    refresh=refresh,
                    ctx=ctx,
                    telemetry_id=telemetry_id,
                    lock_handoff=lock_handoff,
                    lock_scope=lock_scope,
                    failure=failure,
                )
        from openviking.storage.queuefs.content_write_msg import ContentWriteMsg

        queue_manager = get_queue_manager()
        queue = queue_manager.get_queue(queue_manager.CONTENT_WRITE, allow_create=True)
        msg = ContentWriteMsg(
            task_id=context.task_id,
            operation=operation,
            resource_id=resource_id,
            result=deepcopy(result),
            refresh=deepcopy(refresh),
            account_id=ctx.account_id,
            user_id=ctx.user.user_id,
            role=str(ctx.role),
            group_ids=list(ctx.group_ids),
            actor_peer_id=ctx.actor_peer_id,
            bypass_acl=ctx.bypass_acl,
            telemetry_id=telemetry_id,
            lock_handoff=lock_handoff,
            lock_scope=lock_scope,
            failure=deepcopy(failure) if failure is not None else None,
        )
        receipt = await queue.enqueue(msg.to_dict())
        if not receipt.work_id:
            raise RuntimeError("Content write root was enqueued without Task Work identity")
        return receipt.work_id

    async def _write(
        self,
        *,
        uri: str,
        content: str | bytes,
        ctx: RequestContext,
        mode: str = "replace",
        wait: bool = False,
        processing_mode: ProcessingMode = DEFAULT_PROCESSING_MODE,
        tags: list[str] | None = None,
        tag_mode: str = "replace",
        acl: AclSpec | Dict[str, Any] | None = None,
        root_operation: str = "content_write",
        root_task_factory: Callable[[], Awaitable[Any]] | None = None,
    ) -> Dict[str, Any]:
        self._validate_mode(mode)
        processing_mode = normalize_processing_mode(processing_mode)
        normalized_uri = normalize_storage_target_uri(
            self._validate_uri_path(uri, field_name="uri")
        )
        self._ensure_content_write_policy(normalized_uri)
        await self._viking_fs._ensure_access(normalized_uri, ctx, action=AclAction.WRITE)
        namespace_policy = namespace_policy_for_uri(normalized_uri)
        if (
            namespace_policy.processing_mode is NamespaceProcessingMode.FILE_ONLY
            and is_abstract_overview_uri(normalized_uri)
        ):
            raise InvalidArgumentError(
                f"semantic sidecars are not supported for namespace: {normalized_uri}"
            )
        if tags is not None and not namespace_policy.supports_tags:
            raise InvalidArgumentError(f"tags are not supported for namespace: {normalized_uri}")
        if acl is not None and not namespace_policy.supports_acl:
            raise InvalidArgumentError(f"ACL is not supported for namespace: {normalized_uri}")
        if isinstance(content, str):
            content = await self._sanitize_text(content, uri=normalized_uri, ctx=ctx)
        content = normalize_account_memory_template_write(normalized_uri, content, mode)
        ingest_options = IngestOptions.from_search_tags(tags, mode=tag_mode)
        if acl is not None:
            ingest_options = replace(
                ingest_options,
                acl_update=await self._viking_fs.prepare_acl_update(normalized_uri, acl, ctx),
            )

        # ``create`` is an upsert alias for ``replace``: it overwrites an existing
        # file or materializes a missing one, never conflicting. Normalize it away
        # so no caller-visible create mode enters the write pipeline;
        # ``response_mode`` still echoes the request for API stability.
        response_mode = mode
        if mode == "create":
            mode = "replace"

        context_type = context_type_for_uri(normalized_uri)
        if namespace_policy.processing_mode is NamespaceProcessingMode.FILE_ONLY:
            parent = VikingURI(normalized_uri).parent
            root_uri = parent.uri if parent is not None else normalized_uri
            return await self._write_file_only(
                uri=normalized_uri,
                root_uri=root_uri,
                content=content,
                mode=mode,
                response_mode=response_mode,
                context_type=context_type,
                ctx=ctx,
                root_operation=root_operation,
                root_task_factory=root_task_factory,
            )

        root_uri = await self._resolve_root_uri(
            normalized_uri,
            ctx=ctx,
            _allow_not_found=True,
            anchor_to_parent=True,
            validate_storage=False,
        )
        telemetry_id = get_current_telemetry().telemetry_id

        if context_type == "memory" and not is_abstract_overview_uri(normalized_uri):
            return await self._write_memory_with_refresh(
                uri=normalized_uri,
                root_uri=root_uri,
                content=content,
                mode=mode,
                response_mode=response_mode,
                ctx=ctx,
                telemetry_id=telemetry_id,
                ingest_options=ingest_options,
                root_operation=root_operation,
                root_task_factory=root_task_factory,
            )

        return await self._write_direct_with_refresh(
            uri=normalized_uri,
            root_uri=root_uri,
            content=content,
            mode=mode,
            response_mode=response_mode,
            context_type=context_type,
            wait=wait,
            ctx=ctx,
            telemetry_id=telemetry_id,
            processing_mode=processing_mode,
            ingest_options=ingest_options,
            root_operation=root_operation,
            root_task_factory=root_task_factory,
        )

    async def batch_write(
        self,
        *,
        root_uri: str,
        operations: list[dict[str, Any]],
        ctx: RequestContext,
        wait: bool = True,
        skip_conflicts: bool = False,
        defer_processing: bool = False,
        processing_mode: ProcessingMode = DEFAULT_PROCESSING_MODE,
    ) -> Dict[str, Any]:
        task = None
        task_id = None

        async def create_root_task() -> Any:
            nonlocal task, task_id
            if task is None:
                task_id = task_id or str(uuid4())
                task = await get_task_tracker().create(
                    "content_batch_write",
                    account_id=ctx.account_id,
                    user_id=ctx.user.user_id,
                    resource_id=root_uri,
                    task_id=task_id,
                )
            if task is None:
                raise RuntimeError("Content batch write Task was not created")
            return task

        try:
            result = await self._batch_write(
                root_uri=root_uri,
                operations=operations,
                ctx=ctx,
                wait=wait,
                skip_conflicts=skip_conflicts,
                defer_processing=defer_processing,
                processing_mode=processing_mode,
                root_operation="content_batch_write",
                root_task_factory=create_root_task,
            )
        except BaseException as exc:
            if task_id is not None:
                if _root_owns_failure(exc):
                    if wait and task is not None:
                        await self._wait_for_content_write_terminal(task.task_id, ctx=ctx)
                else:
                    await self._fail_content_write_task(
                        task.task_id if task is not None else task_id,
                        exc,
                        ctx=ctx,
                    )
            raise
        if task is None:
            raise RuntimeError("Content batch write completed without creating a root Task")
        result = await self._return_or_wait_for_content_write_task(
            task.task_id,
            result,
            ctx=ctx,
            wait=wait,
            accept_failed_result=_result_has_queue_errors if wait else None,
        )
        if wait:
            self._raise_refresh_errors(result.get("queue_status", {}))
        return result

    async def _batch_write(
        self,
        *,
        root_uri: str,
        operations: list[dict[str, Any]],
        ctx: RequestContext,
        wait: bool = True,
        skip_conflicts: bool = False,
        defer_processing: bool = False,
        processing_mode: ProcessingMode = DEFAULT_PROCESSING_MODE,
        root_operation: str = "content_batch_write",
        root_task_factory: Callable[[], Awaitable[Any]] | None = None,
    ) -> Dict[str, Any]:
        """Write validated files under locks, optionally deferring all index work.

        Each operation follows the same create/replace/append semantics as ``write``;
        ``upsert`` is available for callers that already hold the desired final tree.
        Target files stay locked through refresh while unrelated files remain writable.
        Deferred batches reject index-dependent metadata and release their locks
        before returning. Partial I/O failures preserve earlier successful writes.
        skip_conflicts leaves conflicting targets untouched; permission, transport
        and write failures remain errors.
        """
        normalized_root = self._validate_uri_path(root_uri, field_name="root_uri")
        processing_mode = normalize_processing_mode(processing_mode)
        await self._validate_batch_root(normalized_root, ctx=ctx)
        normalized_operations = await self._normalize_batch_operations(
            normalized_root, operations, ctx=ctx
        )
        if defer_processing and any(
            operation["ingest_options"] != IngestOptions() for operation in normalized_operations
        ):
            raise InvalidArgumentError("defer_processing does not support tags or ACL updates")
        await self._sanitize_batch_operations(normalized_operations, ctx=ctx)
        for operation in normalized_operations:
            operation["content"] = normalize_account_memory_template_write(
                operation["uri"], operation["content"], operation["mode"]
            )
        root_policy = namespace_policy_for_uri(normalized_root)
        if any(
            namespace_policy_for_uri(operation["uri"]).processing_mode
            is not root_policy.processing_mode
            for operation in normalized_operations
        ):
            raise InvalidArgumentError(
                "batch-write cannot mix FILE_ONLY and INDEXED namespace targets"
            )
        file_only = root_policy.processing_mode is NamespaceProcessingMode.FILE_ONLY

        target_paths = [
            self._viking_fs._uri_to_path(operation["uri"], ctx=ctx)
            for operation in normalized_operations
        ]
        try:
            lease = await self._viking_fs._async_agfs.pathlock_acquire_exact_batch(target_paths)
        except LockAcquisitionError as exc:
            raise ResourceBusyError(
                f"resource is busy and cannot be written now: {normalized_root}",
                uri=normalized_root,
            ) from exc
        lock_guard = PathLockGuard(self._viking_fs._async_agfs, lease)

        created: list[str] = []
        updated: list[str] = []
        unchanged: list[str] = []
        refresh_kinds: dict[str, str] = {}
        file_md5s: dict[str, str] = {}
        file_abstracts: dict[str, str] = {}
        sidecar_directories: dict[str, IngestOptions] = {}
        pending: list[tuple[dict[str, Any], bool, str]] = []
        conflicts: list[dict[str, str]] = []
        prepared_resources: dict[str, _PreparedInlineResource] = {}
        committed_resource_plans: list[_PreparedInlineResource] = []
        write_error: Exception | None = None
        lock_settled = False
        try:
            await self._viking_fs._ensure_access_many(
                [normalized_root, *[operation["uri"] for operation in normalized_operations]],
                ctx,
                action=AclAction.WRITE,
            )
            write_ctx = replace(ctx, bypass_acl=True)
            for operation in normalized_operations:
                uri = operation["uri"]
                stat = await self._safe_stat(uri, ctx=write_ctx, allow_not_found=True)
                exists = not stat.get("not_found")
                if exists and stat.get("isDir") and not skip_conflicts:
                    raise InvalidArgumentError(f"batch-write target must be a file: {uri}")

                requested_mode = operation["mode"]
                # Batch-write follows the same materialization contract as a
                # single explicit write: create/replace/upsert all establish
                # the requested final body, while append only needs the old
                # body when it already exists.
                write_mode = "append" if requested_mode == "append" and exists else "replace"

                if is_abstract_overview_uri(uri):
                    if not exists:
                        raise InvalidArgumentError(
                            f"cannot create generated abstract overview directly: {uri}"
                        )
                try:
                    if exists and stat.get("isDir"):
                        raise ConflictError(f"Target is a directory: {uri}")
                    expected = operation.get("expected_sha256")
                    if expected is not None:
                        if not exists:
                            raise ConflictError(f"Expected content no longer exists: {uri}")
                        current = await self._viking_fs.read_file_bytes(uri, ctx=write_ctx)
                        if hashlib.sha256(current).hexdigest() != expected:
                            raise ConflictError(f"Content revision changed: {uri}")
                        desired = operation["content"]
                        if isinstance(desired, str):
                            desired = desired.encode("utf-8")
                        if (
                            skip_conflicts
                            and write_mode == "replace"
                            and current == desired
                            and operation["ingest_options"] == IngestOptions()
                        ):
                            unchanged.append(uri)
                            continue
                except ConflictError as exc:
                    if not skip_conflicts:
                        raise
                    conflicts.append({"uri": uri, "code": exc.code, "message": str(exc)})
                    continue
                pending.append((operation, exists, write_mode))

            if not file_only and not defer_processing:
                prepared_resources = await self._prepare_batch_resource_plans(
                    pending=pending,
                    ctx=ctx,
                    lease=lease,
                    processing_mode=processing_mode,
                )
                file_abstracts = await self._load_file_abstracts(
                    [
                        operation["uri"]
                        for operation, existed, _ in pending
                        if existed
                        and context_type_for_uri(operation["uri"]) in {"resource", "skill"}
                        and not is_abstract_overview_uri(operation["uri"])
                        and operation["uri"] not in prepared_resources
                    ],
                    ctx=ctx,
                )
            try:
                for operation, existed, write_mode in pending:
                    uri = operation["uri"]
                    try:
                        prepared = prepared_resources.get(uri)
                        if prepared is not None:
                            write_content = partial(
                                execute_content_tree_actions,
                                prepared.plan.content_tree_actions,
                                store=prepared.store,
                                artifact_ref=prepared.artifact_ref,
                                target=prepared.target,
                            )
                        else:
                            write_content = partial(
                                self._write_in_place,
                                uri,
                                operation["content"],
                                mode=write_mode,
                                ctx=write_ctx,
                                lease_ref=lease,
                                is_new_file=not existed,
                            )
                        final_content = await self._commit_content([uri], lease, write_content)
                        if prepared is not None:
                            final_content = prepared.final_bytes
                            committed_resource_plans.append(prepared)
                        if (
                            not file_only
                            and context_type_for_uri(uri)
                            in {
                                "resource",
                                "skill",
                            }
                            and not is_abstract_overview_uri(uri)
                        ):
                            file_md5s[uri] = content_md5(final_content)
                        options = operation["ingest_options"]
                        if options.acl_update:
                            await self._viking_fs.acl_manager.apply_indexed_update(
                                options.acl_update, ctx
                            )
                    except Exception as exc:
                        write_error = exc
                        break
                    if existed:
                        updated.append(uri)
                        if is_abstract_overview_uri(uri):
                            parent = VikingURI(uri).parent
                            if parent is not None:
                                # Sidecars represent directory L0/L1 records. If
                                # multiple sidecar writes share a directory, the
                                # deterministic operation order matches sequential
                                # write semantics: the last operation's options win.
                                sidecar_directories[parent.uri] = options
                        elif not file_only and prepared is None:
                            refresh_kinds[uri] = "modified"
                    else:
                        created.append(uri)
                        if not file_only and prepared is None:
                            refresh_kinds[uri] = "added"
            finally:
                if self._batch_commit is not None:
                    error = sys.exc_info()[1]
                    try:
                        await run_to_completion(lambda: self._batch_commit(lease_ref=lease))
                    except BaseException:
                        if error is None and write_error is None:
                            raise
                        logger.exception("Failed to version partial batch writes")
        except BaseException:
            await lock_guard.close()
            raise

        telemetry_id = get_current_telemetry().telemetry_id
        try:
            if root_task_factory is not None:
                result = {
                    "root_uri": normalized_root,
                    "created": created,
                    "updated": updated,
                    "unchanged": unchanged,
                    "queue_status": None,
                }
                if skip_conflicts:
                    result["conflicts"] = conflicts
                if file_only:
                    result["semantic_status"] = "skipped"
                    result["vector_status"] = "skipped"
                elif defer_processing:
                    result.update(semantic_status="deferred", vector_status="deferred")
                has_refresh_work = (
                    not file_only
                    and not defer_processing
                    and bool(refresh_kinds or sidecar_directories or committed_resource_plans)
                )
                if write_error is not None and not has_refresh_work:
                    await lock_guard.close()
                    lock_settled = True
                    raise write_error
                if has_refresh_work:
                    result["semantic_status"] = "queued"
                    result["vector_status"] = "queued"
                    lock_handoff = await lock_guard.to_handoff()
                    legacy_refresh_uris = set(refresh_kinds)
                    refresh = {
                        "kind": "batch",
                        "force_refresh": wait,
                        "refresh_kinds": dict(refresh_kinds),
                        "file_md5s": {
                            uri: value
                            for uri, value in file_md5s.items()
                            if uri in legacy_refresh_uris
                        },
                        "file_abstracts": {
                            uri: value
                            for uri, value in file_abstracts.items()
                            if uri in legacy_refresh_uris
                        },
                        "sidecar_directories": {
                            uri: options.to_dict()
                            for uri, options in sorted(sidecar_directories.items())
                        },
                        "file_ingest_options": {
                            operation["uri"]: operation["ingest_options"].to_dict()
                            for operation, _, _ in pending
                            if operation["uri"] in legacy_refresh_uris
                        },
                        "resource_plans": [
                            {
                                "plan": prepared.plan.after_content_commit().to_dict(),
                                "existed": prepared.existed,
                                "previous_abstract": prepared.previous_abstract,
                                "ingest_options": prepared.ingest_options.to_dict(),
                            }
                            for prepared in committed_resource_plans
                        ],
                    }
                else:
                    lock_handoff = None
                    refresh = {"kind": "none"}
                await root_task_factory()
                root_dispatched = False

                async def dispatch() -> str:
                    nonlocal root_dispatched
                    await self._enqueue_content_write_root(
                        operation=root_operation,
                        resource_id=normalized_root,
                        result=result,
                        refresh=refresh,
                        ctx=ctx,
                        telemetry_id=telemetry_id,
                        lock_handoff=lock_handoff,
                        failure=_failure_payload(write_error) if write_error is not None else None,
                        root_task_factory=root_task_factory,
                    )
                    root_dispatched = True
                    return "enqueued"

                try:
                    if lock_handoff is not None:
                        await lock_guard.handoff_and_dispatch(lock_handoff, dispatch)
                    else:
                        await lock_guard.dispatch_and_close(dispatch)
                except BaseException as exc:
                    if root_dispatched:
                        _mark_root_owns_failure(exc)
                    raise
                lock_settled = True
                if write_error is not None:
                    _mark_root_owns_failure(write_error)
                    raise write_error
                return result
        finally:
            if not lock_settled:
                await lock_guard.close()

        raise RuntimeError("Content batch write root Task was not created")

    def _validate_uri_path(self, uri: str, *, field_name: str) -> str:
        try:
            return validate_safe_viking_uri_path(uri)
        except ValueError as exc:
            raise InvalidArgumentError(f"invalid {field_name}: {exc}") from exc

    async def _validate_batch_root(self, root_uri: str, *, ctx: RequestContext) -> None:
        classification = classify_uri(root_uri)
        parts = uri_parts(root_uri)
        is_root_system_skill_namespace = (
            ctx.role == Role.ROOT
            and classification.scope == "system"
            and classification.is_skill_namespace
        )
        if (
            classification.context_type not in {"resource", "memory"}
            and not is_root_system_skill_namespace
        ):
            raise InvalidArgumentError("batch-write root must be a resource or memory directory")
        if classification.context_type == "memory":
            if (
                classification.content_index is None
                or len(parts) <= classification.content_index + 1
            ):
                raise InvalidArgumentError(
                    "batch-write root must be inside a memory type directory"
                )
        elif classification.context_type == "resource" and (
            parts == ["resources"]
            or (
                classification.content_index is not None
                and len(parts) <= classification.content_index + 1
            )
        ):
            raise InvalidArgumentError("batch-write root must be inside a resource directory")
        await self._viking_fs._ensure_access(root_uri, ctx, action=AclAction.WRITE)
        stat = await self._safe_stat(root_uri, ctx=replace(ctx, bypass_acl=True))
        if not stat.get("isDir"):
            raise InvalidArgumentError(
                f"batch-write root must be an existing directory: {root_uri}"
            )

    async def _normalize_batch_operations(
        self,
        root_uri: str,
        operations: list[dict[str, Any]],
        *,
        ctx: RequestContext,
    ) -> list[dict[str, Any]]:
        if not operations:
            raise InvalidArgumentError("batch-write operations must not be empty")

        context_type = context_type_for_uri(root_uri)
        normalized: list[dict[str, Any]] = []
        seen: set[str] = set()
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
            namespace_policy = namespace_policy_for_uri(uri)
            if (
                namespace_policy.processing_mode is NamespaceProcessingMode.FILE_ONLY
                and is_abstract_overview_uri(uri)
            ):
                raise InvalidArgumentError(
                    f"semantic sidecars are not supported for namespace: {uri}"
                )

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
                # Reject invalid UTF-8 before any operation writes to the target tree.
                content.encode("utf-8")
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
                    content = base64.b64decode(content_base64, validate=True)
                except (binascii.Error, ValueError) as exc:
                    raise InvalidArgumentError(
                        f"batch-write content_base64 is invalid: {uri}"
                    ) from exc

            mode = raw.get("mode", "replace")
            expected = raw.get("expected_sha256")
            if expected is not None and (
                not isinstance(expected, str)
                or len(expected) != 64
                or any(c not in "0123456789abcdef" for c in expected)
            ):
                raise InvalidArgumentError("expected_sha256 must be a lowercase SHA-256 digest")
            self._validate_batch_mode(mode)
            if has_content_base64 and mode == "append":
                raise InvalidArgumentError(
                    f"batch-write append does not support binary content: {uri}"
                )
            if context_type == "memory" and mode in {"create", "upsert"}:
                self._validate_create_extension(uri)
            if context_type == "memory" and mode == "append":
                raise InvalidArgumentError("batch-write append is not supported for memories")
            tags = raw.get("tags")
            if tags is not None and (
                not isinstance(tags, list) or any(not isinstance(tag, str) for tag in tags)
            ):
                raise InvalidArgumentError(f"batch-write tags must be a string list: {uri}")
            if tags is not None and not namespace_policy.supports_tags:
                raise InvalidArgumentError(f"tags are not supported for namespace: {uri}")
            tag_mode = raw.get("tag_mode", "replace")
            self._validate_tag_mode(tag_mode)
            ingest_options = IngestOptions.from_search_tags(tags, mode=tag_mode)
            if (acl := raw.get("acl")) is not None:
                if not namespace_policy.supports_acl:
                    raise InvalidArgumentError(f"ACL is not supported for namespace: {uri}")
                ingest_options = replace(
                    ingest_options,
                    acl_update=await self._viking_fs.prepare_acl_update(uri, acl, ctx),
                )
            normalized.append(
                {
                    "uri": uri,
                    "content": content,
                    "mode": mode,
                    "expected_sha256": raw.get("expected_sha256"),
                    "ingest_options": ingest_options,
                }
            )
        await self._viking_fs._ensure_access_many(
            [operation["uri"] for operation in normalized], ctx, action=AclAction.WRITE
        )
        return sorted(normalized, key=lambda operation: operation["uri"])

    async def _refresh_batch(
        self,
        *,
        refresh_kinds: dict[str, str],
        ctx: RequestContext,
        wait: bool,
        telemetry_id: str,
        file_md5s: dict[str, str] | None = None,
        file_abstracts: dict[str, str] | None = None,
        file_ingest_options: dict[str, IngestOptions] | None = None,
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
                file_md5s={
                    uri: file_md5s[uri]
                    for values in changes.values()
                    for uri in values
                    if file_md5s and uri in file_md5s
                },
                file_abstracts={
                    uri: file_abstracts[uri]
                    for values in changes.values()
                    for uri in values
                    if file_abstracts and uri in file_abstracts
                },
                file_ingest_options={
                    uri: options
                    for values in changes.values()
                    for uri in values
                    if file_ingest_options and (options := file_ingest_options.get(uri)) is not None
                },
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
                    ingest_options=(file_ingest_options or {}).get(uri),
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
        )
        self._raise_refresh_errors(queue_status)
        return _BatchRefreshOutcome(
            queue_status=queue_status,
            semantic_actions=tuple(semantic_actions),
            memory_refreshed=bool(memory_groups),
            embedding_requested=embedding_requested,
        )

    async def _execute_batch_refresh_payload(
        self,
        payload: dict[str, Any],
        *,
        ctx: RequestContext,
    ) -> _BatchRefreshOutcome:
        force_refresh = bool(payload.get("force_refresh", False))
        for directory_uri, options in sorted((payload.get("sidecar_directories") or {}).items()):
            await self._vectorize_semantic_directory(
                directory_uri=str(directory_uri),
                ctx=ctx,
                ingest_options=IngestOptions.from_value(options),
            )

        direct_index_requested = False
        planned_refreshes: list[FileRefreshRequest] = []
        for item in payload.get("resource_plans") or ():
            if not isinstance(item, dict):
                continue
            plan = ContextUpdatePlan.from_dict(item["plan"])
            ingest_options = IngestOptions.from_value(item.get("ingest_options"))
            direct_index_requested = (
                await enqueue_direct_index_actions(
                    plan.direct_index_actions,
                    ctx=ctx,
                    acl_update=ingest_options.acl_update,
                )
                or direct_index_requested
            )
            if plan.file_refresh is not None:
                existed = bool(item.get("existed", False))
                planned_refreshes.append(
                    FileRefreshRequest(
                        file_uri=plan.file_refresh.file_uri,
                        created=not existed,
                        md5=plan.file_refresh.md5,
                        abstract=str(item.get("previous_abstract") or ""),
                        vector_action="upsert" if not existed else "",
                        ingest_options=ingest_options,
                    )
                )

        parent_result = (
            await Summarizer(vlm_processor=None).refresh_file_parents(
                changes=planned_refreshes,
                ctx=ctx,
                generation_trigger="content_write",
                force_refresh=force_refresh,
            )
            if planned_refreshes
            else {"enqueued_count": 0, "parent_actions": {}}
        )
        file_ingest_options = {
            str(uri): IngestOptions.from_value(options)
            for uri, options in (payload.get("file_ingest_options") or {}).items()
        }
        refresh_kinds = {
            str(uri): str(change_type)
            for uri, change_type in (payload.get("refresh_kinds") or {}).items()
        }
        legacy_refresh_result = (
            await self._refresh_batch(
                refresh_kinds=refresh_kinds,
                ctx=ctx,
                wait=False,
                telemetry_id="",
                file_md5s={
                    str(uri): str(value) for uri, value in (payload.get("file_md5s") or {}).items()
                },
                file_abstracts={
                    str(uri): str(value)
                    for uri, value in (payload.get("file_abstracts") or {}).items()
                },
                file_ingest_options=file_ingest_options,
            )
            if refresh_kinds
            else None
        )
        legacy_outcome = (
            legacy_refresh_result
            if isinstance(legacy_refresh_result, _BatchRefreshOutcome)
            else _BatchRefreshOutcome(queue_status=None)
        )
        planned_actions = tuple(
            FreshnessAction(action) for action in parent_result["parent_actions"].values()
        )
        return _BatchRefreshOutcome(
            queue_status=legacy_outcome.queue_status,
            semantic_actions=planned_actions + legacy_outcome.semantic_actions,
            memory_refreshed=legacy_outcome.memory_refreshed,
            embedding_requested=direct_index_requested or legacy_outcome.embedding_requested,
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
        ingest_options: IngestOptions | None = None,
        file_md5s: dict[str, str] | None = None,
        file_abstracts: dict[str, str] | None = None,
        file_ingest_options: dict[str, IngestOptions] | None = None,
    ) -> FreshnessAction:
        changed_entries = len({uri for values in changes.values() for uri in values})
        semantic_config = get_openviking_config().semantic
        try:
            decision = await plan_abstract_overview_refresh(
                viking_fs=self._viking_fs,
                dir_uri=root_uri,
                changed_entries=changed_entries,
                ctx=ctx,
                overview_sample_limit=getattr(semantic_config, "overview_sample_limit", 32),
                refresh_ratio=getattr(semantic_config, "freshness_refresh_ratio", 0.10),
                force_refresh=force_refresh,
            )
            action = decision.action
        except LockAcquisitionError:
            # Parent aggregation is best-effort; changed-file work must still run.
            logger.info("Skipping busy parent semantic refresh: %s", root_uri)
            action = FreshnessAction.NOOP
        aggregate_directory = action is FreshnessAction.REFRESH_NOW
        has_live_files = any(changes.get(kind) for kind in ("added", "modified"))
        if not aggregate_directory and not has_live_files:
            return action
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
            group_ids=ctx.group_ids,
            role=str(ctx.role),
            skip_vectorization=False,
            telemetry_id=telemetry.telemetry_id,
            ingest_options=ingest_options,
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
            file_md5s=file_md5s,
            file_abstracts=file_abstracts,
            file_ingest_options=file_ingest_options,
        )
        await semantic_queue.enqueue(msg)
        return action

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
        normalized_uri = self._validate_uri_path(uri, field_name="uri")
        requested_mode = mode
        normalized_tags = (
            [] if requested_mode == "clear" else normalize_search_tags(tags, discard_invalid=True)
        )
        if not namespace_policy_for_uri(normalized_uri).supports_tags:
            raise InvalidArgumentError(f"tags are not supported for namespace: {normalized_uri}")
        await self._viking_fs._ensure_access(normalized_uri, ctx, action=AclAction.WRITE)
        stat = await self._safe_stat(normalized_uri, ctx=replace(ctx, bypass_acl=True))
        context_type = context_type_for_uri(normalized_uri)
        root_uri = await self._resolve_root_uri(normalized_uri, ctx=ctx, target_stat=stat)
        if requested_mode == "replace" and not normalized_tags:
            return self._build_tags_result(
                uri=normalized_uri,
                updated_uris=[],
                skipped_count=1,
                failed_count=0,
                root_uri=root_uri,
                context_type=context_type,
                tags=normalized_tags,
                mode=requested_mode,
            )
        storage_mode = "replace" if requested_mode == "clear" else requested_mode
        if stat.get("isDir"):
            result = await self._set_directory_tags(
                uri=normalized_uri,
                tags=normalized_tags,
                mode=storage_mode,
                recursive=recursive,
                ctx=ctx,
            )
        else:
            result = await self._set_single_uri_tags(
                uri=normalized_uri,
                root_uri=root_uri,
                tags=normalized_tags,
                mode=storage_mode,
                recursive=recursive,
                ctx=ctx,
            )
        result["mode"] = requested_mode
        result["tags"] = normalized_tags
        return result

    def _build_write_result(
        self,
        *,
        uri: str,
        root_uri: str,
        context_type: str,
        mode: str,
        written_bytes: int,
        queue_status: Optional[Dict[str, Any]],
        semantic_status: str,
        vector_status: str,
        overview_status: Optional[str] = None,
    ) -> Dict[str, Any]:
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

    @staticmethod
    def _queue_work_status(
        *,
        requested: bool,
        wait: bool,
        queue_status: Optional[Dict[str, Any]],
        queue_name: str,
    ) -> str:
        if not requested:
            return "skipped"
        if not wait:
            return "queued"
        return "failed" if _queue_has_errors(queue_status, queue_name) else "complete"

    async def _prepare_batch_resource_plans(
        self,
        *,
        pending: list[tuple[dict[str, Any], bool, str]],
        ctx: RequestContext,
        lease: Dict[str, Any],
        processing_mode: ProcessingMode,
    ) -> dict[str, _PreparedInlineResource]:
        """Compile file-scoped RNFV plans before the first batch F mutation.

        The legacy fallback is intentionally limited to an absent vector store,
        which is useful for lightweight callers and old test doubles.  Real
        service batch writes always use the exact-URI hydration path below.
        """
        resource_pending = [
            item
            for item in pending
            if context_type_for_uri(item[0]["uri"]) in {"resource", "skill"}
            and not is_abstract_overview_uri(item[0]["uri"])
        ]
        if not resource_pending:
            return {}

        vector_store = self._vikingdb
        if vector_store is None:
            get_vector_store = getattr(self._viking_fs, "_get_vector_store", None)
            vector_store = get_vector_store() if callable(get_vector_store) else None
        if vector_store is None or not hasattr(vector_store, "get_incremental_inventory_by_uris"):
            return {}

        existing_uris = [operation["uri"] for operation, existed, _ in resource_pending if existed]
        requests = {
            operation["uri"]: RequestIntent.from_ingest_options(
                target_uri=operation["uri"],
                processing_mode=processing_mode,
                ingest_options=operation["ingest_options"],
            )
            for operation, _, _ in resource_pending
        }
        projection = set()
        for request in requests.values():
            projection.update(request.required_vector_fields())
            # File-scoped and vectors-only write planning may reuse a hydrated
            # L2 abstract as an embedding source.
            projection.add("abstract")
        inventories = (
            await vector_store.get_incremental_inventory_by_uris(
                existing_uris,
                ctx=ctx,
                output_fields=sorted(projection),
            )
            if existing_uris
            else {}
        )

        prepared: dict[str, _PreparedInlineResource] = {}
        for operation, existed, write_mode in resource_pending:
            uri = operation["uri"]
            existing_raw: str | bytes | None = None
            if write_mode == "append" and existed:
                existing_raw = await self._viking_fs.read_file(
                    uri, ctx=replace(ctx, bypass_acl=True)
                )
            final_bytes = self._render_final_bytes(
                uri,
                operation["content"],
                mode=write_mode,
                existing_raw=existing_raw,
                is_new_file=not existed,
            )
            prepared[uri] = await self._prepare_inline_resource_plan(
                uri=uri,
                final_bytes=final_bytes,
                target_preexisting=existed,
                formal_snapshot=(
                    ({"": FormalEntry(is_dir=False)}, True) if existed else ({}, True)
                ),
                vector_inventory=inventories.get(uri, {}) if existed else {},
                vector_store=vector_store,
                request_intent=requests[uri],
                ctx=ctx,
                lease=lease,
                ingest_options=operation["ingest_options"],
                include_previous_abstract=True,
                defer_vector_hydration=True,
            )
        return prepared

    async def _prepare_inline_resource_plan(
        self,
        *,
        uri: str,
        final_bytes: bytes,
        target_preexisting: bool,
        formal_snapshot: tuple[dict[str, FormalEntry], bool],
        vector_inventory: dict[str, dict[str, Any]] | None,
        vector_store: Any,
        request_intent: RequestIntent,
        ctx: RequestContext,
        lease: Dict[str, Any],
        ingest_options: IngestOptions | None,
        include_previous_abstract: bool = False,
        defer_vector_hydration: bool = False,
    ) -> _PreparedInlineResource:
        """Build one file-scoped RNFV plan from already-locked, final bytes.

        Both public write adapters own their own locking and final-byte render.
        This helper only turns those immutable facts into the shared inline N/F/V
        plan; it never reads target content or acquires/releases a lock.
        """
        store = InlineBytesStore(final_bytes)
        artifact_ref = _InlineArtifactRef(uri)
        inventory = make_inline_file_inventory(final_bytes)
        target = AgfsResourceTarget(
            viking_fs=self._viking_fs,
            root_uri=uri,
            ctx=replace(ctx, bypass_acl=True),
            lease_ref=lease,
        )
        rnfv = await build_rnfv_snapshot(
            viking_fs=self._viking_fs,
            vikingdb=vector_store,
            store=store,
            artifact_ref=artifact_ref,
            target_uri=uri,
            ctx=ctx,
            request_intent=request_intent,
            root_is_file=True,
            target_preexisting=target_preexisting,
            formal_snapshot=formal_snapshot,
            artifact_inventory=inventory,
            vector_scope="self",
            # Explicit write treats missing F as authoritative new state; an
            # orphan canonical V record is overwritten by the later UPSERT.
            vector_inventory=vector_inventory,
        )
        if defer_vector_hydration:
            plan = await self._build_deferred_batch_file_plan(
                rnfv=rnfv,
                store=store,
                artifact_ref=artifact_ref,
                target=target,
                inventory=inventory,
                request_intent=request_intent,
                ctx=ctx,
                target_preexisting=target_preexisting,
            )
        else:
            _, plan = await build_context_update_plan_from_snapshot(
                snapshot=rnfv,
                store=store,
                artifact_ref=artifact_ref,
                target=target,
                vikingdb=vector_store,
                context_type=context_type_for_uri(uri),
                is_code_repo=False,
                account_id=ctx.account_id,
                ctx=ctx,
                root_preexisting=target_preexisting,
                artifact_paths=inventory.artifact_paths,
                ingest_options=ingest_options,
                root_is_file=True,
            )
            plan = self._ensure_explicit_write_content_action(plan, md5=inventory.entries[""].md5)
        previous_abstract = (
            next(
                (
                    str(record.fields.get("abstract") or "")
                    for record in rnfv.vectors.records_by_id.values()
                    if record.uri == uri and record.level == 2
                ),
                "",
            )
            if include_previous_abstract
            else ""
        )
        return _PreparedInlineResource(
            uri=uri,
            existed=target_preexisting,
            final_bytes=final_bytes,
            plan=plan,
            store=store,
            artifact_ref=artifact_ref,
            target=target,
            previous_abstract=previous_abstract,
            ingest_options=IngestOptions.from_value(ingest_options),
        )

    @staticmethod
    def _file_scalar_patch(request: RequestIntent) -> FieldPatch | None:
        values: dict[str, Any] = {}
        modes: dict[str, str] = {}
        for intent in request.scalar_intents:
            if int(ContextLevel.DETAIL) not in intent.target_levels:
                continue
            values[intent.field] = (
                list(intent.value) if isinstance(intent.value, tuple) else intent.value
            )
            modes[intent.field] = intent.mode
        return FieldPatch(values, modes) if values else None

    async def _build_deferred_batch_file_plan(
        self,
        *,
        rnfv: Any,
        store: InlineBytesStore,
        artifact_ref: _InlineArtifactRef,
        target: AgfsResourceTarget,
        inventory: Any,
        request_intent: RequestIntent,
        ctx: RequestContext,
        target_preexisting: bool,
    ) -> ContextUpdatePlan:
        """Build a batch file plan without synchronously hydrating V scalars.

        Batch keeps exact file locks only for F state and content mutation.  An
        existing L2 uses the durable embedding queue's MERGE action, whose
        worker exact-gets the current record before resolving FieldPatch.  This
        avoids N per-file remote hydrations while preserving latest-state scalar
        merge semantics.
        """
        diff = await resolve_resource_diff(
            rnfv,
            store=store,
            artifact_ref=artifact_ref,
            target=target,
            artifact_paths=inventory.artifact_paths,
        )
        entry = diff.entries[""]
        records = tuple(rnfv.vectors.records_by_id.values())
        canonical, duplicates = canonical_vector_records_by_level(records)
        detail = canonical.get(int(ContextLevel.DETAIL))
        scalar_patch = self._file_scalar_patch(request_intent)
        direct_actions: list[DirectIndexAction] = [
            DirectIndexAction(IndexAction.DELETE, record.uri, record.level, record.record_id)
            for record in duplicates
        ]
        for level, record in canonical.items():
            if level != int(ContextLevel.DETAIL):
                direct_actions.append(
                    DirectIndexAction(IndexAction.DELETE, record.uri, level, record.record_id)
                )

        content_changed = ContentState(entry.content_state) in {
            ContentState.ADDED,
            ContentState.RESTORE,
            ContentState.MODIFIED,
            ContentState.REPLACE_KIND,
        }
        index_needs_vector = IndexState(entry.index_state) in {
            IndexState.MISSING,
            IndexState.PARTIAL,
            IndexState.STALE,
            IndexState.LEVEL_CONFLICT,
        }
        needs_vector = request_intent.vectorize and (
            content_changed or detail is None or index_needs_vector
        )
        has_detail_abstract = detail is not None and bool(
            str(detail.fields.get("abstract") or "").strip()
        )
        needs_file_refresh = (
            request_intent.processing_mode != "vectors_only"
            and request_intent.vectorize
            and (needs_vector or not has_detail_abstract)
        )
        if request_intent.processing_mode == "vectors_only" and needs_vector:
            action = IndexAction.MERGE if detail is not None else IndexAction.UPSERT
            direct_actions.append(
                DirectIndexAction(
                    action=action,
                    uri=request_intent.target_uri,
                    level=int(ContextLevel.DETAIL),
                    record_id=(
                        detail.record_id
                        if detail is not None
                        else vector_record_id(
                            ctx.account_id,
                            request_intent.target_uri,
                            int(ContextLevel.DETAIL),
                        )
                    ),
                    upsert_fields=(
                        scalar_patch.resolve({})
                        if action is IndexAction.UPSERT and scalar_patch
                        else {}
                    ),
                    field_patch=scalar_patch if action is IndexAction.MERGE else None,
                    md5=entry.md5,
                    summary=str(detail.fields.get("abstract") or "") if detail else "",
                )
            )
        elif scalar_patch is not None and detail is not None and not needs_vector:
            direct_actions.append(
                DirectIndexAction(
                    IndexAction.UPDATE_FIELDS,
                    detail.uri,
                    int(ContextLevel.DETAIL),
                    detail.record_id,
                    field_patch=scalar_patch.with_seed(
                        {
                            "uri": detail.uri,
                            "account_id": ctx.account_id,
                            "level": int(ContextLevel.DETAIL),
                        }
                    ),
                )
            )

        return ContextUpdatePlan(
            root_uri=request_intent.target_uri,
            context_type=context_type_for_uri(request_intent.target_uri),
            content_tree_actions=(
                ContentTreeAction(
                    ContentTreeOperation.UPSERT,
                    "",
                    old_kind="file" if target_preexisting else None,
                    new_kind="file",
                    artifact_path="",
                    md5=inventory.entries[""].md5,
                ),
            ),
            direct_index_actions=tuple(direct_actions),
            file_refresh=(
                FileRefreshIntent(request_intent.target_uri, entry.md5)
                if needs_file_refresh
                else None
            ),
        )

    async def _write_direct_with_refresh(
        self,
        *,
        uri: str,
        root_uri: str,
        content: str | bytes,
        mode: str,
        response_mode: str,
        context_type: str,
        wait: bool,
        ctx: RequestContext,
        telemetry_id: str,
        processing_mode: ProcessingMode = DEFAULT_PROCESSING_MODE,
        ingest_options: IngestOptions | None = None,
        root_operation: str = "content_write",
        root_task_factory: Callable[[], Awaitable[Any]] | None = None,
    ) -> Dict[str, Any]:
        # ``.abstract.md`` / ``.overview.md`` are generated sidecars whose body is
        # re-embedded in place (no LLM regeneration and no RNFV plan). Keep that
        # specialized path; everything else goes through the shared RNFV pipeline.
        if is_abstract_overview_uri(uri):
            return await self._write_abstract_overview_with_refresh(
                uri=uri,
                root_uri=root_uri,
                content=content,
                mode=mode,
                response_mode=response_mode,
                context_type=context_type,
                ctx=ctx,
                telemetry_id=telemetry_id,
                ingest_options=ingest_options,
                root_operation=root_operation,
                root_task_factory=root_task_factory,
            )

        lock_path = self._viking_fs._uri_to_path(uri, ctx=ctx)
        acquire_lock = self._viking_fs._async_agfs.pathlock_acquire_exact
        try:
            lease = await acquire_lock(lock_path)
        except LockAcquisitionError as exc:
            raise ResourceBusyError(
                f"resource is busy and cannot be written now: {uri}",
                uri=uri,
            ) from exc

        previous_content: Optional[str] = None
        lock_settled = False
        lock_guard = PathLockGuard(self._viking_fs._async_agfs, lease)
        try:
            target_state = await self._classify_locked_write_target(uri, ctx=ctx)
            write_ctx = replace(ctx, bypass_acl=True)
            target_preexisting = target_state.preexisting
            is_new_file = target_state.is_new_file
            if mode == "append" and target_preexisting:
                previous_content = await self._viking_fs.read_file(uri, ctx=write_ctx)
            final_bytes = self._render_final_bytes(
                uri,
                content,
                mode=mode,
                existing_raw=previous_content,
                is_new_file=is_new_file,
            )
            request = RequestIntent.from_ingest_options(
                target_uri=uri,
                processing_mode=processing_mode,
                ingest_options=ingest_options,
            )
            prepared = await self._prepare_inline_resource_plan(
                uri=uri,
                final_bytes=final_bytes,
                target_preexisting=target_preexisting,
                formal_snapshot=target_state.formal_snapshot,
                # This write owns the complete new-file state. Do not read or
                # inherit an orphan V record when locked F is absent.
                vector_inventory={} if not target_preexisting else None,
                vector_store=self._vikingdb,
                request_intent=request,
                ctx=ctx,
                lease=lease,
                ingest_options=ingest_options,
            )

            await self._commit_content(
                [uri],
                lease,
                lambda: execute_content_tree_actions(
                    prepared.plan.content_tree_actions,
                    store=prepared.store,
                    artifact_ref=prepared.artifact_ref,
                    target=prepared.target,
                ),
            )
            has_async_work = bool(
                prepared.plan.direct_index_actions or prepared.plan.file_refresh is not None
            )
            if root_task_factory is not None:
                if ingest_options.acl_update:
                    await self._viking_fs.acl_manager.apply_indexed_update(
                        ingest_options.acl_update, ctx
                    )

                written_bytes = len(final_bytes)
                semantic_status = "queued" if prepared.plan.file_refresh is not None else "skipped"
                vector_status = (
                    "queued"
                    if prepared.plan.direct_index_actions or prepared.plan.file_refresh is not None
                    else "skipped"
                )
                result = self._build_write_result(
                    uri=uri,
                    root_uri=root_uri,
                    context_type=context_type,
                    mode=response_mode,
                    written_bytes=written_bytes,
                    queue_status=None,
                    semantic_status=semantic_status,
                    vector_status=vector_status,
                )
                refresh = (
                    {
                        "kind": "plan",
                        "plan": prepared.plan.after_content_commit().to_dict(),
                        "file_created": not target_preexisting,
                        "force_refresh": wait,
                        "ingest_options": ingest_options.to_dict()
                        if ingest_options is not None
                        else IngestOptions().to_dict(),
                    }
                    if has_async_work
                    else {"kind": "none"}
                )
                # Skill semantic processing owns the package tree lock. Keep the
                # synchronous write lock scoped to this target file and release
                # it after durable root dispatch, so Semantic can acquire its
                # own package lock without inheriting a conflicting lease.
                lock_scope = "dispatch" if context_type == "skill" else "descendants"
                if has_async_work and lock_scope != "dispatch":
                    lock_handoff = await lock_guard.to_handoff()
                else:
                    lock_handoff = None
                await root_task_factory()
                root_dispatched = False

                async def dispatch() -> str:
                    nonlocal root_dispatched
                    await self._enqueue_content_write_root(
                        operation=root_operation,
                        resource_id=uri,
                        result=result,
                        refresh=refresh,
                        ctx=ctx,
                        telemetry_id=telemetry_id,
                        lock_handoff=lock_handoff,
                        lock_scope=lock_scope,
                        root_task_factory=root_task_factory,
                    )
                    root_dispatched = True
                    return "enqueued"

                try:
                    if lock_handoff is not None:
                        await lock_guard.handoff_and_dispatch(lock_handoff, dispatch)
                    else:
                        await lock_guard.dispatch_and_close(dispatch)
                except BaseException as exc:
                    if root_dispatched:
                        _mark_root_owns_failure(exc)
                    raise
                lock_settled = True
                return result
        finally:
            if not lock_settled:
                await lock_guard.close()

        raise RuntimeError("Content write root Task was not created")

    @staticmethod
    def _ensure_explicit_write_content_action(
        plan: ContextUpdatePlan,
        *,
        md5: str,
    ) -> ContextUpdatePlan:
        """Make an explicit single-file write authoritative for formal content.

        RNFV may classify N as unchanged when its MD5 matches V. That is enough
        to skip derived work, but V can temporarily lag F while an earlier
        asynchronous write is still being indexed. Keep the shared planner's
        convergence behavior unchanged and add the formal UPSERT only at the
        content-write adapter boundary.
        """
        if plan.content_tree_actions:
            return plan
        return replace(
            plan,
            content_tree_actions=(
                ContentTreeAction(
                    ContentTreeOperation.UPSERT,
                    "",
                    old_kind="file",
                    new_kind="file",
                    artifact_path="",
                    md5=md5,
                ),
            ),
        )

    @staticmethod
    def _plan_statuses(
        work: Any,
        *,
        wait: bool,
        queue_status: Optional[Dict[str, Any]],
    ) -> tuple[str, str]:
        """Map executed plan work + queue status to semantic/vector statuses."""
        semantic_action = getattr(work, "semantic_action", None)
        if semantic_action == FreshnessAction.MARK_PENDING.value:
            semantic_status = "deferred"
        elif semantic_action == FreshnessAction.NOOP.value:
            semantic_status = "skipped"
        elif not getattr(work, "semantic_requested", False):
            semantic_status = "skipped"
        elif not wait:
            semantic_status = "queued"
        elif _queue_has_errors(queue_status, "Semantic"):
            semantic_status = "failed"
        else:
            semantic_status = "complete"

        if not getattr(work, "vector_requested", False):
            vector_status = "skipped"
        elif not wait:
            vector_status = "queued"
        elif _queue_has_errors(queue_status, "Embedding"):
            vector_status = "failed"
        else:
            vector_status = "complete"
        return semantic_status, vector_status

    async def _write_abstract_overview_with_refresh(
        self,
        *,
        uri: str,
        root_uri: str,
        content: str,
        mode: str,
        response_mode: str,
        context_type: str,
        ctx: RequestContext,
        telemetry_id: str,
        ingest_options: IngestOptions | None = None,
        root_operation: str = "content_write",
        root_task_factory: Callable[[], Awaitable[Any]] | None = None,
    ) -> Dict[str, Any]:
        """Re-embed a manually edited L0/L1 sidecar body without LLM regeneration."""
        lock_path = self._viking_fs._uri_to_path(uri, ctx=ctx)
        lock_guard = PathLockGuard(self._viking_fs._async_agfs)
        try:
            lease = await lock_guard.acquire(
                lambda: self._viking_fs._async_agfs.pathlock_acquire_exact(lock_path)
            )
        except LockAcquisitionError as exc:
            raise ResourceBusyError(
                f"resource is busy and cannot be written now: {uri}",
                uri=uri,
            ) from exc
        except BaseException:
            await lock_guard.close()
            raise

        try:
            # Preserve the public invariant that generated sidecars cannot be
            # materialized, while keeping the state check inside the exact lock.
            await self._classify_locked_write_target(uri, ctx=ctx)
            write_ctx = replace(ctx, bypass_acl=True)
            previous_content = await self._viking_fs.read_file(uri, ctx=write_ctx)
            await self._write_in_place(
                uri,
                content,
                mode=mode,
                ctx=write_ctx,
                lease_ref=lease,
                existing_raw=previous_content,
            )
            result = self._build_write_result(
                uri=uri,
                root_uri=root_uri,
                context_type=context_type,
                mode=response_mode,
                written_bytes=len(content.encode("utf-8")),
                queue_status=None,
                semantic_status="skipped",
                vector_status="queued",
            )
            if root_task_factory is not None:
                await root_task_factory()
                root_dispatched = False

                async def dispatch() -> str:
                    nonlocal root_dispatched
                    work_id = await self._enqueue_content_write_root(
                        operation=root_operation,
                        resource_id=uri,
                        result=result,
                        refresh={
                            "kind": "abstract_overview",
                            "uri": uri,
                            "ingest_options": ingest_options.to_dict()
                            if ingest_options is not None
                            else IngestOptions().to_dict(),
                        },
                        ctx=ctx,
                        telemetry_id=telemetry_id,
                        lock_scope="dispatch",
                        root_task_factory=root_task_factory,
                    )
                    root_dispatched = True
                    return work_id

                try:
                    await lock_guard.dispatch_and_close(dispatch)
                except BaseException as exc:
                    if root_dispatched:
                        _mark_root_owns_failure(exc)
                    raise
                return result
            raise RuntimeError("Content write root Task was not created")
        finally:
            if lock_guard.lease is not None:
                await lock_guard.close()

    async def _vectorize_abstract_overview(
        self,
        *,
        uri: str,
        ctx: RequestContext,
        ingest_options: IngestOptions | None = None,
    ) -> bool:
        """Re-index a manually edited L0/L1 body without regenerating it."""

        parent = VikingURI(uri).parent
        if parent is None:
            return False
        await self._vectorize_semantic_directory(
            directory_uri=parent.uri, ctx=ctx, ingest_options=ingest_options
        )
        return True

    async def _vectorize_semantic_directory(
        self,
        *,
        directory_uri: str,
        ctx: RequestContext,
        ingest_options: IngestOptions | None = None,
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
            ingest_options=ingest_options,
        )

    def _validate_mode(self, mode: str) -> None:
        if mode not in {"replace", "append", "create"}:
            raise InvalidArgumentError(f"unsupported write mode: {mode}")

    def _validate_batch_mode(self, mode: str) -> None:
        if not isinstance(mode, str) or mode not in {"replace", "append", "create", "upsert"}:
            raise InvalidArgumentError(f"unsupported batch-write mode: {mode}")

    def _validate_tag_mode(self, mode: str) -> None:
        if mode not in {"replace", "append", "clear"}:
            raise InvalidArgumentError(f"unsupported tag mode: {mode}")

    def _ensure_content_write_policy(self, uri: str) -> None:
        name = uri.rstrip("/").split("/")[-1]
        if name in _DERIVED_FILENAMES:
            raise InvalidArgumentError(f"cannot write derived semantic file directly: {uri}")
        if any(is_storage_internal_name(part) for part in uri_parts(uri)):
            raise InvalidArgumentError(f"cannot write storage internal file directly: {uri}")
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

    async def _write_file_only(
        self,
        *,
        uri: str,
        root_uri: str,
        content: str,
        mode: str,
        response_mode: str,
        context_type: str,
        ctx: RequestContext,
        root_operation: str = "content_write",
        root_task_factory: Callable[[], Awaitable[Any]] | None = None,
    ) -> Dict[str, Any]:
        lock_path = self._viking_fs._uri_to_path(uri, ctx=ctx)
        lock_guard = PathLockGuard(self._viking_fs._async_agfs)
        try:
            lease = await lock_guard.acquire(
                lambda: self._viking_fs._async_agfs.pathlock_acquire_exact(lock_path)
            )
        except LockAcquisitionError as exc:
            raise ResourceBusyError(
                f"resource is busy and cannot be written now: {uri}",
                uri=uri,
            ) from exc
        except BaseException:
            await lock_guard.close()
            raise

        try:
            target_state = await self._classify_locked_write_target(uri, ctx=ctx)
            write_ctx = replace(ctx, bypass_acl=True)
            final_bytes = await self._write_in_place(
                uri,
                content,
                mode=mode,
                ctx=write_ctx,
                lease_ref=lease,
                is_new_file=target_state.is_new_file,
            )
            result = self._build_write_result(
                uri=uri,
                root_uri=root_uri,
                context_type=context_type,
                mode=response_mode,
                written_bytes=len(final_bytes),
                queue_status=None,
                semantic_status="skipped",
                vector_status="skipped",
            )
            if root_task_factory is not None:
                await root_task_factory()
                root_dispatched = False

                async def dispatch() -> str:
                    nonlocal root_dispatched
                    work_id = await self._enqueue_content_write_root(
                        operation=root_operation,
                        resource_id=uri,
                        result=result,
                        refresh={"kind": "none"},
                        ctx=ctx,
                        telemetry_id=get_current_telemetry().telemetry_id,
                        lock_scope="dispatch",
                        root_task_factory=root_task_factory,
                    )
                    root_dispatched = True
                    return work_id

                try:
                    await lock_guard.dispatch_and_close(dispatch)
                except BaseException as exc:
                    if root_dispatched:
                        _mark_root_owns_failure(exc)
                    raise
                return result
            raise RuntimeError("Content write root Task was not created")
        finally:
            if lock_guard.lease is not None:
                await lock_guard.close()

    async def _safe_stat(
        self, uri: str, *, ctx: RequestContext, allow_not_found: bool = False
    ) -> Dict[str, Any]:
        try:
            return await self._viking_fs.stat(uri, ctx=ctx, skip_count=True)
        except Exception as exc:
            if self._is_not_found(exc):
                if allow_not_found:
                    return {"not_found": True}
                if isinstance(exc, NotFoundError):
                    raise
                raise NotFoundError(uri, "file") from exc
            raise NotFoundError(uri, "file") from exc

    async def _classify_locked_write_target(
        self,
        uri: str,
        *,
        ctx: RequestContext,
    ) -> _LockedWriteTarget:
        """Read target state exactly once after its exact write lock is held."""
        await self._viking_fs._ensure_access(uri, ctx, action=AclAction.WRITE)
        stat = await self._safe_stat(uri, ctx=replace(ctx, bypass_acl=True), allow_not_found=True)
        preexisting = not stat.get("not_found")
        if preexisting:
            if stat.get("isDir"):
                raise InvalidArgumentError(
                    f"write only supports existing files, got directory: {uri}"
                )
            return _LockedWriteTarget(True, ({"": FormalEntry(is_dir=False)}, True))
        if is_abstract_overview_uri(uri):
            raise InvalidArgumentError(f"cannot create generated abstract overview directly: {uri}")
        self._validate_create_extension(uri)
        return _LockedWriteTarget(False, ({}, True))

    def _validate_create_extension(self, uri: str) -> None:
        _, ext = os.path.splitext(uri)
        if ext.lower() not in _CREATE_ALLOWED_EXTENSIONS:
            raise InvalidArgumentError(
                f"creating a new file does not allow extension '{ext}': {uri}"
            )

    def _render_final_bytes(
        self,
        uri: str,
        content: str | bytes,
        *,
        mode: str,
        existing_raw: str | bytes | None,
        is_new_file: bool = False,
    ) -> bytes:
        """Render the final on-disk bytes for a write without touching storage.

        Splitting render from write lets the RNFV single-file path compute the
        content md5 (its N snapshot) up front and lets ``execute_content_tree_actions``
        commit the exact same bytes. Abstract-overview and memory renders keep
        their specialized handling; ``existing_raw`` must be supplied for the
        modes that need the prior content (append / memory replace-append /
        abstract-overview merge).
        """
        if is_abstract_overview_uri(uri):
            if existing_raw is None:
                raise ValueError("abstract-overview render requires existing content")
            rendered = self._prepare_abstract_overview_content(
                uri=uri,
                current_raw=existing_raw,
                requested_raw=content,
                mode=mode,
            )
            return rendered.encode("utf-8")

        if context_type_for_uri(uri) == "memory":
            if is_new_file:
                mf = MemoryFileUtils.read(content, uri=uri)
            elif mode == "replace":
                mf = MemoryFileUtils.read(existing_raw, uri=uri)
                mf.content = content
            elif mode == "append":
                mf = MemoryFileUtils.read(existing_raw, uri=uri)
                mf.content = mf.content + content
            else:
                raise InvalidArgumentError(f"unsupported memory write mode: {mode}")
            sync_memory_resource_refs(mf, source=RESOURCE_REF_SOURCE_CONTENT_WRITE)
            return MemoryFileUtils.write(mf).encode("utf-8")

        if mode == "append":
            # Plain concatenation for resource/skill files: MEMORY_FIELDS is a
            # reserved trailer of memory namespaces only (see content_visibility),
            # so non-memory appends must not round-trip through MemoryFileUtils
            # (which strips trailing newlines and injects a metadata trailer).
            if is_new_file:
                if not isinstance(content, str):
                    raise InvalidArgumentError(f"append only supports text content: {uri}")
                return content.encode("utf-8")
            if not isinstance(existing_raw, str) or not isinstance(content, str):
                raise InvalidArgumentError(f"append only supports text content: {uri}")
            return (existing_raw + content).encode("utf-8")

        return content if isinstance(content, bytes) else content.encode("utf-8")

    async def _write_in_place(
        self,
        uri: str,
        content: str | bytes,
        *,
        mode: str,
        ctx: RequestContext,
        lease_ref: Optional[Dict[str, Any]] = None,
        existing_raw: str | bytes | None = None,
        is_new_file: bool | None = None,
    ) -> bytes:
        if is_new_file is None:
            # batch-write still uses its historical create mode internally; map it
            # to the same explicit materialization state as single-file write.
            is_new_file = mode == "create"
        if is_abstract_overview_uri(uri) and existing_raw is None:
            existing_raw = await self._viking_fs.read_file(uri, ctx=ctx)
        elif (
            context_type_for_uri(uri) == "memory"
            and not is_new_file
            and mode in {"replace", "append"}
        ):
            existing_raw = await self._viking_fs.read_file(uri, ctx=ctx)
        elif (
            context_type_for_uri(uri) != "memory"
            and not is_abstract_overview_uri(uri)
            and mode == "append"
            and existing_raw is None
        ):
            existing_raw = await self._viking_fs.read_file(uri, ctx=ctx)
        final_bytes = self._render_final_bytes(
            uri,
            content,
            mode=mode,
            existing_raw=existing_raw,
            is_new_file=is_new_file,
        )
        await self._viking_fs.write_file_bytes(uri, final_bytes, ctx=ctx, lease_ref=lease_ref)
        return final_bytes

    async def _load_file_abstracts(self, uris: list[str], *, ctx: RequestContext) -> dict[str, str]:
        vector_store = self._vikingdb
        if vector_store is None:
            get_vector_store = getattr(self._viking_fs, "_get_vector_store", None)
            vector_store = get_vector_store() if callable(get_vector_store) else None
        if vector_store is None or not uris:
            return {}
        try:
            records = await vector_store.get_l2_diff_records_by_uris(sorted(set(uris)), ctx=ctx)
        except Exception as exc:
            logger.warning("Failed to load existing file abstracts: %s", exc)
            return {}
        return {
            uri: str(record.get("abstract") or "")
            for uri, record in records.items()
            if uri in uris and record.get("abstract") is not None
        }

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
        ingest_options: IngestOptions | None = None,
        file_md5: str | None = None,
        file_abstract: str = "",
    ) -> FreshnessAction:
        return await self._enqueue_semantic_refresh_changes(
            root_uri=root_uri,
            context_type=context_type,
            ctx=ctx,
            changes={change_type: [changed_uri]},
            target_uri=target_uri,
            recursive=recursive,
            force_refresh=force_refresh,
            ingest_options=ingest_options,
            file_md5s={changed_uri: file_md5} if file_md5 else None,
            file_abstracts={changed_uri: file_abstract} if file_abstract else None,
        )

    async def _wait_for_request(
        self,
        *,
        telemetry_id: str,
    ) -> Dict[str, Any]:
        del telemetry_id
        context = get_task_context()
        if context is None:
            raise RuntimeError("TaskContext is required for request-scoped queue waiting")
        work_ids = get_enqueued_work_ids()
        tracker = get_task_tracker()
        try:
            if context.work_id is None:
                await tracker.wait_for_task_work(context.task_id, timeout=None)
            elif work_ids:
                await tracker.wait_for_work(context.task_id, work_ids, timeout=None)
        except (TimeoutError, asyncio.TimeoutError) as exc:
            raise DeadlineExceededError("queue processing", None) from exc
        return await tracker.build_queue_status(
            context.task_id, work_ids if context.work_id is not None else None
        )

    async def _refresh_memory_after_write(
        self,
        *,
        root_uri: str,
        uri: str,
        ctx: RequestContext,
        ingest_options: IngestOptions | None = None,
    ) -> tuple[bool, bool]:
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
            strict=True,
            ingest_options=ingest_options,
        )
        return overview_refreshed, embedding_requested

    async def _write_memory_with_refresh(
        self,
        *,
        uri: str,
        root_uri: str,
        content: str,
        mode: str,
        response_mode: str,
        ctx: RequestContext,
        telemetry_id: str,
        ingest_options: IngestOptions | None = None,
        root_operation: str = "content_write",
        root_task_factory: Callable[[], Awaitable[Any]] | None = None,
    ) -> Dict[str, Any]:
        lock_path = self._viking_fs._uri_to_path(uri, ctx=ctx)
        lock_guard = PathLockGuard(self._viking_fs._async_agfs)
        try:
            lease = await lock_guard.acquire(
                lambda: self._viking_fs._async_agfs.pathlock_acquire_exact(lock_path)
            )
        except LockAcquisitionError as exc:
            raise ResourceBusyError(
                f"resource is busy and cannot be written now: {uri}",
                uri=uri,
            ) from exc
        except BaseException:
            await lock_guard.close()
            raise

        try:
            target_state = await self._classify_locked_write_target(uri, ctx=ctx)
            write_ctx = replace(ctx, bypass_acl=True)
            is_new_file = target_state.is_new_file
            write_kwargs = {
                "mode": mode,
                "ctx": write_ctx,
                "lease_ref": lease,
            }
            if is_new_file:
                write_kwargs["is_new_file"] = True
            final_bytes = await self._commit_content(
                [uri], lease, lambda: self._write_in_place(uri, content, **write_kwargs)
            )
            written_bytes = len(final_bytes)
            if root_task_factory is not None:
                result = self._build_write_result(
                    uri=uri,
                    root_uri=root_uri,
                    context_type="memory",
                    mode=response_mode,
                    written_bytes=written_bytes,
                    queue_status=None,
                    semantic_status="skipped",
                    vector_status="queued",
                    overview_status="queued",
                )
                await root_task_factory()
                root_dispatched = False

                async def dispatch() -> str:
                    nonlocal root_dispatched
                    work_id = await self._enqueue_content_write_root(
                        operation=root_operation,
                        resource_id=uri,
                        result=result,
                        refresh={
                            "kind": "memory",
                            "root_uri": root_uri,
                            "uri": uri,
                            "ingest_options": ingest_options.to_dict()
                            if ingest_options is not None
                            else IngestOptions().to_dict(),
                        },
                        ctx=ctx,
                        telemetry_id=telemetry_id,
                        lock_scope="dispatch",
                        root_task_factory=root_task_factory,
                    )
                    root_dispatched = True
                    return work_id

                try:
                    await lock_guard.dispatch_and_close(dispatch)
                except BaseException as exc:
                    if root_dispatched:
                        _mark_root_owns_failure(exc)
                    raise
                return result
            raise RuntimeError("Content write root Task was not created")
        finally:
            if lock_guard.lease is not None:
                await lock_guard.close()

    def _vikingdb_has_queue(self) -> bool:
        if not self._vikingdb:
            return False
        return bool(getattr(self._vikingdb, "has_queue_manager", False))

    async def _set_single_uri_tags(
        self,
        *,
        uri: str,
        root_uri: str,
        tags: list[str],
        mode: str,
        recursive: bool,
        ctx: RequestContext,
    ) -> Dict[str, Any]:
        del recursive
        context_type = context_type_for_uri(uri)
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

        targets = [str(target["uri"]) for target in updated_targets]
        update_ctx = replace(ctx, bypass_acl=True)
        await self._viking_fs._ensure_access_many(targets, update_ctx, action=AclAction.WRITE)
        if recursive:
            await self._viking_fs._ensure_descendant_access(
                uri, ctx, action=AclAction.WRITE, targets=targets
            )

        applied_uris: list[str] = []
        skipped_count = 0
        for target in updated_targets:
            updated_uris = await self._upsert_uri_tags(
                uri=target["uri"],
                tags=tags,
                mode=mode,
                ctx=update_ctx,
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
            ctx=replace(ctx, bypass_acl=True),
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
        validate_storage: bool = True,
        target_stat: dict[str, Any] | None = None,
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
                    # the semantic-tree root: its own L2 vector and the parent's L0/L1 are
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

        if validate_storage:
            stat = (
                target_stat
                if root_uri == uri and target_stat is not None
                else await self._safe_stat(root_uri, ctx=ctx, allow_not_found=_allow_not_found)
            )
            if stat.get("not_found") or not stat.get("isDir"):
                parent = VikingURI(uri).parent
                if parent is None:
                    raise InvalidArgumentError(f"could not resolve write root for {uri}")
                root_uri = parent.uri
        return root_uri
