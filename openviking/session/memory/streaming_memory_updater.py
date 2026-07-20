# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0
"""Streaming updater for ordinary user memories.

This module provides a realtime batching layer for session user-memory writes.
Multiple concurrent commits can submit resolved memory operations; the updater
buffers them for a small count/time window, merges patches with the generic
PatchMergeContextProvider, then applies the merged operations with MemoryUpdater.
"""

from __future__ import annotations

import asyncio
import re
import threading
from dataclasses import dataclass, field
from typing import Any, Hashable

from openviking.core.peer_id import safe_peer_id
from openviking.message import Message
from openviking.server.identity import RequestContext
from openviking.session.memory.dataclass import (
    MemoryFile,
    MemoryOperationSource,
    MemoryTypeSchema,
    ResolvedOperation,
    ResolvedOperations,
    StoredLink,
)
from openviking.session.memory.extract_loop import ExtractLoop
from openviking.session.memory.memory_isolation_handler import MemoryIsolationHandler
from openviking.session.memory.memory_type_registry import (
    MemoryTypeRegistry,
    create_default_registry,
)
from openviking.session.memory.memory_updater import (
    ExtractContext,
    MemoryUpdater,
    MemoryUpdateResult,
    remap_stored_links,
    write_stored_links,
)
from openviking.session.memory.merge_op import MergeOpFactory
from openviking.session.memory.patch_merge_context_provider import (
    PatchMergeContextProvider,
    PatchMergePatch,
)
from openviking.session.memory.session_extract_context_provider import SessionExtractContextProvider
from openviking.session.memory.utils.memory_file_utils import MemoryFileUtils, next_memory_version
from openviking.session.memory.utils.streaming_batcher import (
    StreamingBatcher,
    StreamingBatcherConfig,
)
from openviking.storage.viking_fs import get_viking_fs
from openviking.telemetry import tracer
from openviking.telemetry.tracer import get_trace_id
from openviking_cli.utils import get_logger
from openviking_cli.utils.config import get_openviking_config

logger = get_logger(__name__)


@dataclass(slots=True)
class StreamingMemoryUpdaterConfig:
    """Configuration for automatic streaming ordinary-memory updates."""

    max_operations_per_update: int = 8
    max_wait_seconds: float = 10.0
    timer_check_interval_seconds: float = 1.0
    trace_console: bool = False

    def __post_init__(self) -> None:
        if self.max_operations_per_update <= 0:
            raise ValueError("max_operations_per_update must be > 0")
        if self.max_wait_seconds <= 0:
            raise ValueError("max_wait_seconds must be > 0")
        if self.timer_check_interval_seconds <= 0:
            raise ValueError("timer_check_interval_seconds must be > 0")


@dataclass(frozen=True, slots=True)
class StreamingMemoryUpdaterKey:
    """Process-local registry key for one shared user-memory updater."""

    account_id: str
    user_id: str


@dataclass(frozen=True, slots=True)
class MemoryMergeGroupKey:
    """Per-scope/type batching key for second-stage memory merges."""

    peer_id: str | None
    memory_type: str


@dataclass(slots=True)
class MemoryUpdateRequest:
    """One commit's resolved user-memory update request."""

    operations: ResolvedOperations
    messages: list[Message]
    ctx: RequestContext
    strict_extract_errors: bool = False
    isolation_options: dict[str, Any] = field(default_factory=dict)
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass(slots=True)
class StreamingMemoryUpdateResult:
    """Result returned when a submit triggers a flush."""

    operations: ResolvedOperations
    apply_result: MemoryUpdateResult
    request_count: int
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass(slots=True)
class StreamingMemoryUpdater:
    """Long-lived ordinary-memory updater with count/time window batching."""

    registry: MemoryTypeRegistry | None = None
    vikingdb: Any = None
    config: StreamingMemoryUpdaterConfig = field(default_factory=StreamingMemoryUpdaterConfig)
    _group_batchers: dict[
        MemoryMergeGroupKey,
        StreamingBatcher[MemoryUpdateRequest, StreamingMemoryUpdateResult],
    ] = field(init=False, repr=False)
    _group_batchers_lock: asyncio.Lock = field(init=False, repr=False)
    _apply_lock: asyncio.Lock = field(init=False, repr=False)
    _last_result: StreamingMemoryUpdateResult | None = field(init=False, default=None, repr=False)
    _closed: bool = field(init=False, default=False, repr=False)

    def __post_init__(self) -> None:
        self.registry = self.registry or create_default_registry()
        self._group_batchers = {}
        self._group_batchers_lock = asyncio.Lock()
        self._apply_lock = asyncio.Lock()
        self._last_result = None
        self._closed = False

    @property
    def closed(self) -> bool:
        return self._closed

    async def get_buffered_operation_count(self) -> int:
        async with self._group_batchers_lock:
            batchers = list(self._group_batchers.values())
        sizes = await asyncio.gather(*(batcher.get_buffered_size() for batcher in batchers))
        return sum(sizes)

    async def close(self) -> StreamingMemoryUpdateResult | None:
        if self._closed:
            return None
        self._closed = True
        async with self._group_batchers_lock:
            batchers = list(self._group_batchers.values())
            self._group_batchers = {}
        results = await asyncio.gather(*(batcher.close() for batcher in batchers))
        return combine_streaming_memory_results(*results)

    @tracer("memory.streaming_updater.submit", ignore_result=True, ignore_args=True)
    async def submit(self, request: MemoryUpdateRequest) -> StreamingMemoryUpdateResult:
        """Submit one resolved update request.

        The request is buffered and flushed by the shared count/time window.
        ``submit`` waits until the batch containing this request is merged and
        applied, preserving session.commit's "write is visible on return"
        semantics while still allowing concurrent commits to batch together.
        """

        if self._closed:
            raise RuntimeError("StreamingMemoryUpdater is closed")
        if request.ctx is None:
            raise ValueError("MemoryUpdateRequest.ctx is required")
        attach_source_to_request_operations(request)
        append_only_request, merge_request = self._split_append_only_request(request)
        append_result = (
            await self._apply_append_only_request_now(append_only_request)
            if append_only_request is not None
            else None
        )
        merge_result = (
            await self._submit_grouped_merge_request(merge_request)
            if merge_request is not None
            else None
        )
        result = combine_streaming_memory_results(
            append_result,
            merge_result,
            fallback_request_count=1,
        )
        self._last_result = result
        scoped_result = scope_memory_update_result_to_submitter(result, request)
        tracer.info(
            "StreamingMemoryUpdater submit finished "
            f"batch_id={result.metadata.get('batch_id')} "
            f"batch_trace_id={result.metadata.get('batch_trace_id')} "
            f"flush_reason={result.metadata.get('flush_reason')} "
            f"request_count={result.request_count} "
            f"operation_count={result.metadata.get('operation_count')} "
            f"written_uris={scoped_result.apply_result.written_uris} "
            f"edited_uris={scoped_result.apply_result.edited_uris} "
            f"deleted_uris={scoped_result.apply_result.deleted_uris} "
            f"errors={scoped_result.apply_result.errors}",
            console=self.config.trace_console,
        )
        return scoped_result

    async def _submit_grouped_merge_request(
        self,
        request: MemoryUpdateRequest,
    ) -> StreamingMemoryUpdateResult | None:
        grouped_requests = split_request_by_merge_group(request)
        if not grouped_requests:
            return None
        submissions = [
            (await self._get_group_batcher(group_key)).submit(group_request)
            for group_key, group_request in grouped_requests
        ]
        group_results = list(await asyncio.gather(*submissions))
        result = combine_streaming_memory_results(*group_results, fallback_request_count=1)
        await self._apply_post_group_links(request, result)
        return result

    async def _apply_post_group_links(
        self,
        request: MemoryUpdateRequest,
        result: StreamingMemoryUpdateResult,
    ) -> None:
        links = merge_link_lists(list(getattr(request.operations, "resolved_links", []) or []))
        if not links:
            return
        links = remap_stored_links(
            links, dict(getattr(result.operations, "delete_replacements", {}) or {})
        )
        valid_links = await filter_valid_links(
            links,
            upsert_operations=result.operations.upsert_operations,
            delete_file_contents=result.operations.delete_file_contents,
            ctx=request.ctx,
            trace_console=self.config.trace_console,
        )
        if not valid_links:
            return
        viking_fs = safe_get_viking_fs()
        if viking_fs is not None:
            updated_uris = await write_stored_links(valid_links, request.ctx, viking_fs)
            for uri in dict.fromkeys(updated_uris):
                result.apply_result.add_edited(uri)
        result.operations.resolved_links = merge_link_lists(
            list(getattr(result.operations, "resolved_links", []) or []),
            valid_links,
        )

    async def _get_group_batcher(
        self,
        group_key: MemoryMergeGroupKey,
    ) -> StreamingBatcher[MemoryUpdateRequest, StreamingMemoryUpdateResult]:
        async with self._group_batchers_lock:
            batcher = self._group_batchers.get(group_key)
            if batcher is not None:
                return batcher

            batcher = self._create_group_batcher(group_key)
            self._group_batchers[group_key] = batcher
            return batcher

    def _create_group_batcher(
        self,
        group_key: MemoryMergeGroupKey,
    ) -> StreamingBatcher[MemoryUpdateRequest, StreamingMemoryUpdateResult]:
        async def process_batch(
            requests: list[MemoryUpdateRequest],
            reason: str,
        ) -> StreamingMemoryUpdateResult:
            return await self._process_batch(group_key, requests, reason)

        batcher = StreamingBatcher(
            name=(
                "openviking-streaming-memory-updater:"
                f"{group_key.peer_id or 'self'}:{group_key.memory_type}"
            ),
            process_batch=process_batch,
            config=StreamingBatcherConfig(
                max_items_per_batch=self.config.max_operations_per_update,
                max_wait_seconds=self.config.max_wait_seconds,
                timer_check_interval_seconds=self.config.timer_check_interval_seconds,
            ),
            item_size=lambda request: _operation_count(request.operations),
            result_metadata=lambda result: result.metadata,
        )
        return batcher

    def _split_append_only_request(
        self, request: MemoryUpdateRequest
    ) -> tuple[MemoryUpdateRequest | None, MemoryUpdateRequest | None]:
        operations = request.operations
        registry = self.registry or create_default_registry()
        append_ops: list[ResolvedOperation] = []
        merge_ops: list[ResolvedOperation] = []
        for op in list(operations.upsert_operations or []):
            schema = registry.get(op.memory_type)
            if op.uris and getattr(schema, "operation_mode", None) == "add_only":
                append_ops.append(op)
            else:
                merge_ops.append(op)

        append_links, merge_links = split_links_for_append_only_ops(
            list(getattr(operations, "resolved_links", []) or []),
            append_ops=append_ops,
            merge_ops=merge_ops,
        )
        append_request = None
        if append_ops:
            append_request = clone_memory_update_request(
                request,
                operations=ResolvedOperations(
                    upsert_operations=append_ops,
                    delete_file_contents=[],
                    errors=[],
                    resolved_links=append_links,
                ),
            )

        merge_request = None
        if merge_ops or operations.delete_file_contents or operations.errors:
            merge_request = clone_memory_update_request(
                request,
                operations=ResolvedOperations(
                    upsert_operations=merge_ops,
                    delete_file_contents=list(operations.delete_file_contents or []),
                    errors=list(operations.errors or []),
                    resolved_links=merge_links,
                    delete_replacements=dict(getattr(operations, "delete_replacements", {}) or {}),
                ),
            )
        return append_request, merge_request

    async def _apply_append_only_request_now(
        self,
        request: MemoryUpdateRequest,
    ) -> StreamingMemoryUpdateResult:
        tracer.info(
            "StreamingMemoryUpdater fast path started "
            f"reason=append_only operation_count={_operation_count(request.operations)}",
            console=self.config.trace_console,
        )
        operations = request.operations.model_copy(deep=True)
        operations.resolved_links = await filter_valid_links(
            merge_link_lists(list(getattr(operations, "resolved_links", []) or [])),
            upsert_operations=operations.upsert_operations,
            delete_file_contents=operations.delete_file_contents,
            ctx=request.ctx,
            trace_console=self.config.trace_console,
        )
        apply_result = await self._apply_operations(
            operations=operations,
            request=request,
            messages=request.messages,
        )
        result = StreamingMemoryUpdateResult(
            operations=operations,
            apply_result=apply_result,
            request_count=1,
            metadata={
                "flush_reason": "append_only_fast_path",
                "operation_count": _operation_count(operations),
                "fast_path": True,
                "append_only_operation_count": _operation_count(operations),
            },
        )
        tracer.info(
            "StreamingMemoryUpdater fast path finished "
            f"written_uris={apply_result.written_uris} "
            f"edited_uris={apply_result.edited_uris} "
            f"deleted_uris={apply_result.deleted_uris} "
            f"errors={apply_result.errors}",
            console=self.config.trace_console,
        )
        return result

    async def _process_batch(
        self,
        group_key: MemoryMergeGroupKey,
        requests: list[MemoryUpdateRequest],
        reason: str,
    ) -> StreamingMemoryUpdateResult:
        input_operations = sum(_operation_count(request.operations) for request in requests)
        input_patches = sum(
            len(getattr(request.operations, "upsert_operations", []) or []) for request in requests
        )
        input_deletes = sum(
            len(getattr(request.operations, "delete_file_contents", []) or [])
            for request in requests
        )
        tracer.info(
            "StreamingMemoryUpdater flush started "
            f"group={group_key} reason={reason} request_count={len(requests)} "
            f"input_operations={input_operations} "
            f"input_patches={input_patches} "
            f"input_deletes={input_deletes}",
            console=self.config.trace_console,
        )
        merged_operations = await self._merge_requests(requests)
        first_request = requests[0]
        apply_result = await self._apply_operations(
            operations=merged_operations,
            request=first_request,
            messages=_combined_request_messages(requests),
        )
        result = StreamingMemoryUpdateResult(
            operations=merged_operations,
            apply_result=apply_result,
            request_count=len(requests),
            metadata={
                "flush_reason": reason,
                "operation_count": _operation_count(merged_operations),
                "merge_group": _merge_group_key_label(group_key),
            },
        )
        self._last_result = result
        tracer.info(
            "StreamingMemoryUpdater flush finished "
            f"group={group_key} reason={reason} request_count={len(requests)} "
            f"written_uris={apply_result.written_uris} "
            f"edited_uris={apply_result.edited_uris} "
            f"deleted_uris={apply_result.deleted_uris} "
            f"errors={apply_result.errors}",
            console=self.config.trace_console,
        )
        return result

    async def _apply_operations(
        self,
        *,
        operations: ResolvedOperations,
        request: MemoryUpdateRequest,
        messages: list[Message],
    ) -> MemoryUpdateResult:
        updater = MemoryUpdater(
            registry=self.registry,
            vikingdb=self.vikingdb,
            transaction_handle=None,
        )
        extract_context = ExtractContext(messages)
        isolation_handler = _make_isolation_handler(request, extract_context)
        async with self._apply_lock:
            return await updater.apply_operations(
                operations,
                request.ctx,
                extract_context=extract_context,
                isolation_handler=isolation_handler,
            )

    async def _merge_requests(self, requests: list[MemoryUpdateRequest]) -> ResolvedOperations:
        all_ops = ResolvedOperations(
            upsert_operations=[],
            delete_file_contents=[],
            errors=[],
            resolved_links=[],
            delete_replacements={},
        )
        for request in requests:
            ops = request.operations
            all_ops.upsert_operations.extend(list(ops.upsert_operations or []))
            all_ops.delete_file_contents.extend(list(ops.delete_file_contents or []))
            all_ops.errors.extend(list(ops.errors or []))
            all_ops.resolved_links.extend(list(getattr(ops, "resolved_links", []) or []))
            all_ops.delete_replacements.update(dict(getattr(ops, "delete_replacements", {}) or {}))
        return await merge_memory_operations(
            operations=all_ops,
            messages=_combined_request_messages(requests),
            ctx=requests[0].ctx,
            registry=self.registry or create_default_registry(),
            strict_extract_errors=any(request.strict_extract_errors for request in requests),
            trace_console=self.config.trace_console,
        )


def split_request_by_merge_group(
    request: MemoryUpdateRequest,
) -> list[tuple[MemoryMergeGroupKey, MemoryUpdateRequest]]:
    """Split one commit request into per-(peer_id, memory_type) merge requests.

    A submit/session.commit awaits all returned group requests, so commits touching
    multiple memory types still return only after every affected group is merged
    and applied.
    """
    operations = request.operations
    upsert_groups: dict[MemoryMergeGroupKey, list[ResolvedOperation]] = {}
    delete_groups: dict[MemoryMergeGroupKey, list[MemoryFile]] = {}
    passthrough_upserts: list[ResolvedOperation] = []

    for op in list(operations.upsert_operations or []):
        if not op.uris:
            passthrough_upserts.append(op)
            continue
        peer_id = _peer_id_for_operation(op)
        for uri in op.uris:
            single_uri_op = clone_operation_for_uri(op, uri)
            group_key = MemoryMergeGroupKey(peer_id=peer_id, memory_type=single_uri_op.memory_type)
            upsert_groups.setdefault(group_key, []).append(single_uri_op)

    for file in list(operations.delete_file_contents or []):
        group_key = MemoryMergeGroupKey(
            peer_id=_peer_id_for_memory_file(file),
            memory_type=file.memory_type or "",
        )
        delete_groups.setdefault(group_key, []).append(file)

    group_keys = list(dict.fromkeys(list(upsert_groups.keys()) + list(delete_groups.keys())))
    grouped_requests: list[tuple[MemoryMergeGroupKey, MemoryUpdateRequest]] = []
    for group_key in group_keys:
        group_upserts = upsert_groups.get(group_key, [])
        group_deletes = delete_groups.get(group_key, [])
        grouped_requests.append(
            (
                group_key,
                clone_memory_update_request(
                    request,
                    operations=ResolvedOperations(
                        upsert_operations=group_upserts,
                        delete_file_contents=group_deletes,
                        errors=list(operations.errors or []),
                        resolved_links=[],
                        delete_replacements={
                            file.uri: replacement_uri
                            for file in group_deletes
                            if file.uri
                            if (
                                replacement_uri := (
                                    getattr(operations, "delete_replacements", {}) or {}
                                ).get(file.uri)
                            )
                        },
                    ),
                ),
            )
        )

    if passthrough_upserts:
        group_key = MemoryMergeGroupKey(peer_id=None, memory_type="")
        grouped_requests.append(
            (
                group_key,
                clone_memory_update_request(
                    request,
                    operations=ResolvedOperations(
                        upsert_operations=passthrough_upserts,
                        delete_file_contents=[],
                        errors=list(operations.errors or []),
                        resolved_links=[],
                        delete_replacements={},
                    ),
                ),
            )
        )
    return grouped_requests


def _merge_group_key_label(group_key: MemoryMergeGroupKey) -> str:
    peer_label = group_key.peer_id or "self"
    memory_type = group_key.memory_type or "unknown"
    return f"peer={peer_label},memory_type={memory_type}"


async def merge_memory_operations(
    *,
    operations: ResolvedOperations,
    messages: list[Message],
    ctx: RequestContext,
    registry: MemoryTypeRegistry | None = None,
    strict_extract_errors: bool = False,
    trace_console: bool = False,
) -> ResolvedOperations:
    """Merge resolved memory operations by memory type/URI using patch context."""

    if operations.has_errors():
        tracer.info(
            "[streaming_memory_updater] merge skipped reason=operation_errors "
            f"error_count={len(operations.errors)} "
            f"patch_count={len(operations.upsert_operations or [])} "
            f"delete_count={len(operations.delete_file_contents or [])}",
            console=trace_console,
        )
        return operations

    # Group by (peer_id, memory_type) — peer_id is None for self memories.
    # Upserts get peer_id from memory_fields; deletes get it from extra_fields.
    # Types with ranges (e.g. events) pop peer_id from memory_fields, but those are
    # add_only and skip merge entirely, so they never reach this grouping.
    upsert_groups: dict[tuple[str | None, str], list[ResolvedOperation]] = {}
    delete_groups: dict[tuple[str | None, str], list[MemoryFile]] = {}
    passthrough_upserts: list[ResolvedOperation] = []
    for op in operations.upsert_operations:
        if not op.uris:
            passthrough_upserts.append(op)
            continue
        peer_id = _peer_id_for_operation(op)
        for uri in op.uris:
            single_uri_op = clone_operation_for_uri(op, uri)
            upsert_groups.setdefault((peer_id, single_uri_op.memory_type), []).append(single_uri_op)
    for df in operations.delete_file_contents:
        peer_id = _peer_id_for_memory_file(df)
        memory_type = df.memory_type or ""
        delete_groups.setdefault((peer_id, memory_type), []).append(df)

    # Union all group keys from both upserts and deletes
    all_group_keys = list(dict.fromkeys(list(upsert_groups.keys()) + list(delete_groups.keys())))

    tracer.info(
        "[streaming_memory_updater] merge batch "
        f"patch_count={len(operations.upsert_operations or [])} "
        f"delete_count={len(operations.delete_file_contents or [])} "
        f"passthrough_upserts={len(passthrough_upserts)} "
        f"group_count={len(all_group_keys)} "
        f"groups={sorted(str(k) for k in all_group_keys)}",
        console=trace_console,
    )

    merged_upserts = list(passthrough_upserts)
    merged_deletes: list[MemoryFile] = []
    merged_delete_replacements: dict[str, str] = {}
    merged_links = merge_link_lists(list(getattr(operations, "resolved_links", []) or []))
    registry = registry or create_default_registry()
    merge_results = await asyncio.gather(
        *[
            merge_one_memory_type_operations(
                memory_type=memory_type,
                operations=upsert_groups.get((peer_id, memory_type), []),
                delete_files=delete_groups.get((peer_id, memory_type), []),
                messages=messages,
                ctx=ctx,
                registry=registry,
                peer_id=peer_id,
                trace_console=trace_console,
            )
            for (peer_id, memory_type) in all_group_keys
        ],
        return_exceptions=True,
    )

    for (peer_id, memory_type), group_key, merge_result in zip(
        all_group_keys, all_group_keys, merge_results, strict=True
    ):
        ops_list = upsert_groups.get(group_key, [])
        if not isinstance(merge_result, Exception):
            merged = merge_result
            enforce_merge_group_peer_id(
                merged.upsert_operations,
                peer_id=peer_id,
                memory_type=memory_type,
                registry=registry,
                ctx=ctx,
            )
            _inherit_source_metadata_to_merged_operations(ops_list, merged.upsert_operations)
            merged_upserts.extend(merged.upsert_operations)
            merged_deletes.extend(merged.delete_file_contents)
            merged_delete_replacements.update(
                dict(getattr(merged, "delete_replacements", {}) or {})
            )
            merged_links = merge_link_lists(
                merged_links,
                list(getattr(merged, "resolved_links", []) or []),
            )
            continue

        peer_label = f"peer={peer_id}" if peer_id else "peer=self"
        tracer.info(
            "[streaming_memory_updater] merge fallback "
            f"memory_type={memory_type} {peer_label} mode=fallback_original "
            f"reason=llm_merge_failed patch_count={len(ops_list)} "
            f"target_count={len(_unique_operation_uris(ops_list))} error={merge_result}",
            console=trace_console,
        )
        logger.warning(
            "[streaming_memory_updater] merge failed for %s (%s): %s",
            memory_type,
            peer_label,
            merge_result,
        )
        if strict_extract_errors or is_cross_extraction_group(ops_list):
            raise merge_result
        # Fallback: keep original operations and delete files for this group
        merged_upserts.extend(ops_list)
        fallback_deletes = delete_groups.get(group_key, [])
        merged_deletes.extend(fallback_deletes)
        for delete_file in fallback_deletes:
            replacement_uri = dict(getattr(operations, "delete_replacements", {}) or {}).get(
                delete_file.uri
            )
            if replacement_uri:
                merged_delete_replacements[delete_file.uri] = replacement_uri

    merged_links = await filter_valid_links(
        merged_links,
        upsert_operations=merged_upserts,
        delete_file_contents=merged_deletes,
        ctx=ctx,
        trace_console=trace_console,
    )
    return ResolvedOperations(
        upsert_operations=merged_upserts,
        delete_file_contents=merged_deletes,
        errors=list(operations.errors),
        resolved_links=merged_links,
        delete_replacements=merged_delete_replacements,
    )


async def merge_one_memory_type_operations(
    *,
    memory_type: str,
    operations: list[ResolvedOperation],
    delete_files: list[MemoryFile] | None = None,
    messages: list[Message],
    ctx: RequestContext,
    registry: MemoryTypeRegistry | None = None,
    peer_id: str | None = None,
    trace_console: bool = False,
) -> ResolvedOperations:
    registry = registry or create_default_registry()
    schema = registry.get(memory_type)
    delete_files = list(delete_files or [])
    patch_count = len(operations)
    target_uris = _unique_operation_uris(operations)
    target_count = len(target_uris)
    existing_file_count = sum(
        1 for op in operations if getattr(op, "old_memory_file_content", None) is not None
    )
    delete_count = len(delete_files)
    duplicate_target_count = patch_count - target_count
    operation_mode = (
        getattr(schema, "operation_mode", "unknown") if schema is not None else "unknown"
    )

    # Fast path: no upserts, only deletes — passthrough directly
    if not operations and delete_files:
        tracer.info(
            "[streaming_memory_updater] memory_type merge decision "
            f"memory_type={memory_type} mode=no_merge "
            f"reason=delete_only delete_count={delete_count}",
            console=trace_console,
        )
        return ResolvedOperations(
            upsert_operations=[],
            delete_file_contents=list(delete_files),
            errors=[],
            resolved_links=[],
            delete_replacements={},
        )
    if operation_mode == "add_only":
        tracer.info(
            "[streaming_memory_updater] memory_type merge decision "
            f"memory_type={memory_type} mode=no_merge "
            f"reason=add_only operation_mode={operation_mode} "
            f"patch_count={patch_count} target_count={target_count} "
            f"duplicate_target_count={duplicate_target_count} "
            f"existing_file_count={existing_file_count}",
            console=trace_console,
        )
        return ResolvedOperations(
            upsert_operations=list(operations),
            delete_file_contents=[],
            errors=[],
            resolved_links=[],
            delete_replacements={},
        )

    fast_path, fast_path_reason = classify_memory_merge_mode(operations, schema=schema)
    if fast_path:
        tracer.info(
            "[streaming_memory_updater] memory_type merge decision "
            f"memory_type={memory_type} mode=no_merge "
            f"reason={fast_path_reason} operation_mode={operation_mode} "
            f"patch_count={patch_count} target_count={target_count} "
            f"duplicate_target_count={duplicate_target_count} "
            f"existing_file_count={existing_file_count}",
            console=trace_console,
        )
        return ResolvedOperations(
            upsert_operations=list(operations),
            delete_file_contents=[],
            errors=[],
            resolved_links=[],
            delete_replacements={},
        )

    tracer.info(
        "[streaming_memory_updater] memory_type merge decision "
        f"memory_type={memory_type} mode=llm_merge "
        f"reason={fast_path_reason} operation_mode={operation_mode} "
        f"patch_count={patch_count} delete_count={delete_count} "
        f"target_count={target_count} "
        f"duplicate_target_count={duplicate_target_count} "
        f"existing_file_count={existing_file_count}",
        console=trace_console,
    )

    if schema is None:
        raise ValueError(f"Memory schema not found: {memory_type}")

    extract_context = ExtractContext(messages)
    # Existing files: both upsert old_content and delete files count as "existing"
    required_file_uris = list(
        dict.fromkeys(
            [
                uri
                for op in operations
                for uri in op.uris
                if getattr(op, "old_memory_file_content", None) is not None
            ]
            + [df.uri for df in delete_files if df.uri]
        )
    )
    patches = [
        operation_to_patch(op, schema=schema, extract_context=extract_context) for op in operations
    ] + [
        memory_file_to_delete_patch(df, schema=schema, extract_context=extract_context)
        for df in delete_files
    ]
    provider = PatchMergeContextProvider(
        memory_type=memory_type,
        required_file_uris=required_file_uris,
        patches=patches,
        output_language=merge_output_language_from_messages(messages),
    )
    provider._ctx = ctx
    provider._viking_fs = safe_get_viking_fs()
    provider._extract_context = extract_context
    # Build isolation handler matching this group's peer scope.
    # peer_id=None → self scope; peer_id set → peer-only scope.
    if peer_id:
        isolation_handler = MemoryIsolationHandler(
            ctx,
            extract_context,
            allowed_memory_types={memory_type},
            allow_self=False,
            allowed_peer_ids={peer_id},
        )
    else:
        isolation_handler = MemoryIsolationHandler(
            ctx,
            extract_context,
            allowed_memory_types={memory_type},
            allow_self=True,
        )
    isolation_handler.prepare_messages()
    provider._isolation_handler = isolation_handler
    seed_patch_merge_read_contents(provider, operations)
    # Also seed delete files into read_contents so LLM can see their content
    for df in delete_files:
        if df.uri:
            provider.read_file_contents[df.uri] = df
    prefetch_messages = await provider.prefetch()

    async def _prefetch():
        return list(prefetch_messages)

    provider.prefetch = _prefetch
    vlm = get_openviking_config().vlm.get_vlm_instance()
    tracer.info(
        "[streaming_memory_updater] llm merge input "
        f"memory_type={memory_type} required_file_count={len(required_file_uris)} "
        f"required_files={required_file_uris} patch_count={len(patches)} "
        f"target_count={target_count}",
        console=trace_console,
    )
    orchestrator = ExtractLoop(
        vlm=vlm,
        viking_fs=safe_get_viking_fs(),
        ctx=ctx,
        context_provider=provider,
        isolation_handler=isolation_handler,
        max_iterations=1,
    )
    merged, _ = await orchestrator.run()
    merged = merged or ResolvedOperations(upsert_operations=[], delete_file_contents=[], errors=[])
    tracer.info(
        "[streaming_memory_updater] llm merge output "
        f"memory_type={memory_type} upserts={len(merged.upsert_operations)} "
        f"deletes={len(merged.delete_file_contents)} errors={len(merged.errors)}",
        console=trace_console,
    )
    return merged


def merge_output_language_from_messages(messages: list[Message]) -> str | None:
    if not any(
        getattr(part, "text", None)
        for message in messages or []
        for part in getattr(message, "parts", [])
    ):
        return None
    return SessionExtractContextProvider(messages=messages).get_output_language()


def clone_operation_for_uri(op: ResolvedOperation, uri: str) -> ResolvedOperation:
    old_file = getattr(op, "old_memory_file_content", None)
    if old_file is not None and getattr(old_file, "uri", None) not in (None, uri):
        old_file = None
    return op.model_copy(
        update={
            "uris": [uri],
            "memory_fields": dict(getattr(op, "memory_fields", {}) or {}),
            "old_memory_file_content": old_file,
            "source": getattr(op, "source", None),
        },
        deep=True,
    )


def memory_file_to_delete_patch(
    mf: MemoryFile,
    *,
    schema: MemoryTypeSchema,
    extract_context: ExtractContext,
) -> PatchMergePatch:
    """Convert a delete-file MemoryFile to a PatchMergePatch.

    The before_file is the original content; after_file is empty content,
    representing a deletion proposal. The merge LLM should put deleted files
    in delete_ids.
    """
    after_file = MemoryFile(
        uri=mf.uri,
        memory_type=mf.memory_type,
        content="",
        extra_fields=dict(mf.extra_fields or {}),
    )
    return PatchMergePatch(
        before_file=mf,
        after_file=after_file,
    )


def operation_to_patch(
    op: ResolvedOperation,
    *,
    schema: MemoryTypeSchema,
    extract_context: ExtractContext,
) -> PatchMergePatch:
    old_file = getattr(op, "old_memory_file_content", None)
    after_file = render_operation_after_file(
        op,
        schema=schema,
        extract_context=extract_context,
    )
    return PatchMergePatch(
        before_file=old_file,
        after_file=after_file,
    )


def render_operation_after_file(
    op: ResolvedOperation,
    *,
    schema: MemoryTypeSchema,
    extract_context: ExtractContext,
) -> MemoryFile:
    after_content = render_operation_after_file_content(
        op,
        schema=schema,
        extract_context=extract_context,
    )
    return MemoryFileUtils.read(after_content, uri=_first_uri(getattr(op, "uris", []) or []))


def render_operation_after_file_content(
    op: ResolvedOperation,
    *,
    schema: MemoryTypeSchema,
    extract_context: ExtractContext,
) -> str:
    old_content = getattr(op, "old_memory_file_content", None)
    metadata: dict[str, Any] = dict(getattr(op, "memory_fields", {}) or {})
    source_extraction_id = source_extraction_id_for_operation(op)
    if source_extraction_id:
        metadata["source_extraction_id"] = source_extraction_id
    source_trace_id = source_trace_id_for_operation(op)
    if source_trace_id:
        metadata["last_update_trace_id"] = source_trace_id
    for field_def in schema.fields:
        if field_def.name not in metadata:
            continue
        if old_content is None:
            current_value = None
        elif field_def.name == "content":
            current_value = old_content.plain_content()
        else:
            current_value = old_content.extra_fields.get(field_def.name)
        try:
            metadata[field_def.name] = MergeOpFactory.from_field(field_def).apply(
                current_value,
                metadata[field_def.name],
            )
        except Exception as exc:
            logger.debug(
                "Failed to preview memory patch field: memory_type=%s field=%s",
                op.memory_type,
                field_def.name,
                exc_info=True,
            )
            tracer.info(
                "[streaming_memory_updater] skipping preview field update after merge_op failure "
                f"memory_type={op.memory_type} field={field_def.name} error={exc}"
            )
            if current_value is None:
                metadata.pop(field_def.name, None)
            else:
                metadata[field_def.name] = current_value

    if old_content and old_content.extra_fields:
        schema_field_names = {field.name for field in schema.fields} | {"content", "memory_type"}
        for key, value in old_content.extra_fields.items():
            if key not in schema_field_names and key not in metadata and value is not None:
                metadata[key] = value
    metadata["version"] = next_memory_version(old_content)
    metadata.setdefault("memory_type", op.memory_type)
    mf = MemoryFile.from_parsed(uri=_first_uri(op.uris), parsed=dict(metadata))
    return MemoryFileUtils.write(
        mf,
        content_template=schema.content_template,
        extract_context=extract_context,
    )


def classify_memory_merge_mode(
    operations: list[ResolvedOperation],
    *,
    schema: MemoryTypeSchema | None = None,
) -> tuple[bool, str]:
    if not operations:
        return True, "empty_batch"

    uris = [_first_uri(op.uris) for op in operations]
    unique_uri_count = len(set(uris))
    duplicate_target_count = len(uris) - unique_uri_count
    all_new_files = all(getattr(op, "old_memory_file_content", None) is None for op in operations)
    operation_mode = getattr(schema, "operation_mode", "") if schema is not None else ""

    if operation_mode == "add_only":
        return True, "add_only"
    if is_cross_extraction_group(operations):
        return False, "cross_extraction_batch"
    # Multi-patch batches always go through LLM merge even if all files are new and
    # URIs are unique — the LLM handles semantic deduplication and directory name
    # normalization (e.g. activity vs activities, art_form vs art_forms).
    if len(operations) > 1:
        return False, "multi_patch_semantic_merge"
    if all_new_files and duplicate_target_count == 0:
        return True, "unique_new_files"

    op = operations[0]
    old_file = getattr(op, "old_memory_file_content", None)
    if old_file is None:
        return True, "single_new_file"
    fields = dict(getattr(op, "memory_fields", {}) or {})
    if "content" not in fields:
        return False, "single_existing_non_content_patch"
    old_plain_content = old_file.plain_content().strip()
    if schema is not None:
        try:
            after_content = render_operation_after_file_content(
                op,
                schema=schema,
                extract_context=ExtractContext([]),
            )
            after_file = MemoryFileUtils.read(
                after_content, uri=_first_uri(getattr(op, "uris", []) or [])
            )
            if old_plain_content == after_file.plain_content().strip():
                return True, "single_existing_content_unchanged"
        except Exception as exc:
            logger.debug(
                "Failed to render memory patch preview for merge-mode classification: "
                "memory_type=%s",
                getattr(op, "memory_type", None),
                exc_info=True,
            )
            tracer.info(
                "[streaming_memory_updater] merge-mode preview failed; falling back to "
                f"raw content comparison memory_type={getattr(op, 'memory_type', None)} "
                f"error={exc}"
            )
    if old_plain_content == str(fields.get("content") or "").strip():
        return True, "single_existing_content_unchanged"
    return False, "single_existing_content_changed"


def _inherit_source_metadata_to_merged_operations(
    input_operations: list[ResolvedOperation],
    merged_operations: list[ResolvedOperation],
) -> None:
    """Best-effort provenance restore after patch-merge LLM output.

    Patch merge hides system provenance fields from the model, so generated
    operations can lose source_extraction_id. Reattach it by exact URI match
    where possible. If a merged output has no URI match but only one input
    source exists, copy that source; otherwise record all input source IDs as an
    ambiguous multi-source operation.
    """

    input_by_uri: dict[str, list[ResolvedOperation]] = {}
    all_source_ids: set[str] = set()
    for input_op in input_operations or []:
        op_source_ids = _operation_source_extraction_ids(input_op)
        all_source_ids.update(op_source_ids)
        for uri in list(getattr(input_op, "uris", []) or []):
            if uri:
                input_by_uri.setdefault(uri, []).append(input_op)

    if not all_source_ids:
        return

    for merged_op in merged_operations or []:
        if _operation_source_extraction_ids(merged_op):
            continue
        matched_inputs: list[ResolvedOperation] = []
        for uri in list(getattr(merged_op, "uris", []) or []):
            matched_inputs.extend(input_by_uri.get(uri, []))
        matched_ids = {
            source_id
            for input_op in matched_inputs
            for source_id in _operation_source_extraction_ids(input_op)
        }
        if len(matched_ids) == 1:
            _set_operation_source_extraction_id(merged_op, next(iter(matched_ids)))
        elif len(matched_ids) > 1:
            merged_op.memory_fields["source_extraction_ids"] = sorted(matched_ids)
        elif len(all_source_ids) == 1:
            _set_operation_source_extraction_id(merged_op, next(iter(all_source_ids)))
        else:
            merged_op.memory_fields["source_extraction_ids"] = sorted(all_source_ids)


def _set_operation_source_extraction_id(op: ResolvedOperation, extraction_id: str) -> None:
    op.memory_fields["source_extraction_id"] = extraction_id
    source = getattr(op, "source", None)
    if source is None:
        op.source = MemoryOperationSource(extraction_id=extraction_id)
    elif not getattr(source, "extraction_id", None):
        source.extraction_id = extraction_id


def enforce_merge_group_peer_id(
    operations: list[ResolvedOperation],
    *,
    peer_id: str | None,
    memory_type: str,
    registry: MemoryTypeRegistry,
    ctx: RequestContext,
) -> None:
    """Pin merged operations to the peer scope selected by group-by.

    The second-stage merge LLM may omit or hallucinate peer_id. The group key is
    authoritative because it is decided before merge from the original request
    routing; all merged upserts must therefore be rewritten to that scope.
    """
    schema = registry.get(memory_type)
    effective_peer_id = peer_id if getattr(schema, "peer_enabled", True) else None
    for op in operations or []:
        if op.memory_type != memory_type:
            continue
        if effective_peer_id:
            op.memory_fields["peer_id"] = effective_peer_id
        else:
            op.memory_fields.pop("peer_id", None)
        if schema is not None:
            op.uris = _uris_for_merge_group_operation(
                op,
                schema=schema,
                ctx=ctx,
                peer_id=effective_peer_id,
            )


def _uris_for_merge_group_operation(
    op: ResolvedOperation,
    *,
    schema: MemoryTypeSchema,
    ctx: RequestContext,
    peer_id: str | None,
) -> list[str]:
    fields = dict(op.memory_fields or {})
    user_id = getattr(getattr(ctx, "user", None), "user_id", None) or fields.get("user_id")
    if not user_id:
        return list(op.uris or [])
    fields["user_id"] = user_id
    if peer_id:
        fields["peer_id"] = peer_id
        user_space = f"{user_id}/peers/{peer_id}"
    else:
        fields.pop("peer_id", None)
        user_space = user_id
    try:
        from openviking.session.memory.utils.uri import generate_uri

        return [
            generate_uri(
                memory_type=schema,
                fields=fields,
                user_space=user_space,
            )
        ]
    except Exception as exc:
        tracer.info(
            "[streaming_memory_updater] failed to enforce merge group uri "
            f"memory_type={op.memory_type} peer_id={peer_id} old_uris={op.uris} error={exc}"
        )
        return list(op.uris or [])


def _peer_id_from_uri(uri: str | None) -> str | None:
    if not uri:
        return None
    match = re.search(r"/peers/([^/]+)/memories/", uri)
    if not match:
        return None
    return safe_peer_id(match.group(1))


def _peer_id_for_operation(op: ResolvedOperation) -> str | None:
    """Get peer_id from a resolved operation, falling back to peer URI scope.

    Returns None for self (user-level) memories.
    """
    fields = dict(getattr(op, "memory_fields", {}) or {})
    peer_id = safe_peer_id(fields.get("peer_id"))
    if peer_id:
        return peer_id
    old_file = getattr(op, "old_memory_file_content", None)
    if old_file is not None:
        old_peer_id = safe_peer_id((old_file.extra_fields or {}).get("peer_id"))
        if old_peer_id:
            return old_peer_id
        old_uri_peer_id = _peer_id_from_uri(getattr(old_file, "uri", None))
        if old_uri_peer_id:
            return old_uri_peer_id
    for uri in getattr(op, "uris", []) or []:
        uri_peer_id = _peer_id_from_uri(uri)
        if uri_peer_id:
            return uri_peer_id
    return None


def _peer_id_for_memory_file(mf: MemoryFile) -> str | None:
    """Get peer_id from a MemoryFile, falling back to peer URI scope.

    Returns None for self (user-level) memories.
    """
    peer_id = safe_peer_id((mf.extra_fields or {}).get("peer_id"))
    return peer_id or _peer_id_from_uri(mf.uri)


def _unique_operation_uris(operations: list[ResolvedOperation]) -> list[str]:
    return list(dict.fromkeys(uri for op in operations for uri in (op.uris or []) if uri))


def attach_source_to_request_operations(request: MemoryUpdateRequest) -> None:
    source = memory_operation_source_from_request(request)
    if source is None:
        return
    for op in list(getattr(request.operations, "upsert_operations", []) or []):
        if getattr(op, "source", None) is None:
            op.source = source
        source_extraction_id = getattr(op.source, "extraction_id", None)
        if source_extraction_id:
            op.memory_fields.setdefault("source_extraction_id", source_extraction_id)
        source_trace_id = getattr(op.source, "trace_id", None)
        if source_trace_id:
            op.memory_fields.setdefault("last_update_trace_id", source_trace_id)


def memory_operation_source_from_request(
    request: MemoryUpdateRequest,
) -> MemoryOperationSource | None:
    metadata = dict(getattr(request, "metadata", {}) or {})
    extraction_id = metadata.get("source_extraction_id") or metadata.get("extraction_id")
    if not extraction_id:
        return None
    return MemoryOperationSource(
        extraction_id=str(extraction_id),
        session_id=_optional_str(metadata.get("session_id")),
        archive_uri=_optional_str(metadata.get("archive_uri")),
        task_id=_optional_str(metadata.get("task_id")),
        trace_id=_optional_str(metadata.get("trace_id")),
        extracted_at=_optional_str(metadata.get("extracted_at")),
    )


def source_extraction_id_for_operation(op: ResolvedOperation) -> str | None:
    source = getattr(op, "source", None)
    extraction_id = getattr(source, "extraction_id", None) if source is not None else None
    if extraction_id:
        return str(extraction_id)
    fields = dict(getattr(op, "memory_fields", {}) or {})
    field_value = fields.get("source_extraction_id")
    return str(field_value) if field_value else None


def source_trace_id_for_operation(op: ResolvedOperation) -> str | None:
    source = getattr(op, "source", None)
    trace_id = getattr(source, "trace_id", None) if source is not None else None
    if trace_id:
        return str(trace_id)
    fields = dict(getattr(op, "memory_fields", {}) or {})
    field_value = fields.get("last_update_trace_id") or fields.get("trace_id")
    if field_value:
        return str(field_value)
    current_trace_id = get_trace_id()
    return current_trace_id or None


def is_cross_extraction_group(operations: list[ResolvedOperation]) -> bool:
    extraction_ids = {
        extraction_id
        for extraction_id in (source_extraction_id_for_operation(op) for op in operations)
        if extraction_id
    }
    return len(extraction_ids) > 1


def _optional_str(value: Any) -> str | None:
    if value is None:
        return None
    text = str(value)
    return text if text else None


def seed_patch_merge_read_contents(
    provider: PatchMergeContextProvider, operations: list[ResolvedOperation]
) -> None:
    for op in operations:
        old_file = getattr(op, "old_memory_file_content", None)
        uri = _first_uri(getattr(op, "uris", []) or [])
        if old_file is not None and uri:
            provider.read_file_contents[uri] = old_file


def safe_get_viking_fs() -> Any | None:
    try:
        return get_viking_fs()
    except Exception:
        return None


def merge_link_lists(*link_lists: list[StoredLink]) -> list[StoredLink]:
    """Merge links by endpoint/type/anchor, preferring stronger metadata."""

    merged: dict[tuple[str, str, str, str | None], StoredLink] = {}
    for links in link_lists:
        for link in links or []:
            key = (link.from_uri, link.to_uri, link.link_type, link.match_text)
            current = merged.get(key)
            if current is None:
                merged[key] = link
                continue
            current_weight = float(current.weight or 0.0)
            new_weight = float(link.weight or 0.0)
            if new_weight > current_weight:
                current.weight = link.weight
            if len(link.description or "") > len(current.description or ""):
                current.description = link.description
            if not current.created_at and link.created_at:
                current.created_at = link.created_at
    return list(merged.values())


async def filter_valid_links(
    links: list[StoredLink],
    *,
    upsert_operations: list[ResolvedOperation],
    delete_file_contents: list[MemoryFile],
    ctx: RequestContext,
    trace_console: bool = False,
) -> list[StoredLink]:
    """Drop links whose endpoints are deleted or missing from storage."""

    if not links:
        return []
    upsert_uris = {uri for op in upsert_operations for uri in (op.uris or []) if uri}
    deleted_uris = {file.uri for file in delete_file_contents if getattr(file, "uri", None)}
    viking_fs = safe_get_viking_fs()
    endpoint_exists_cache: dict[str, bool] = {}

    async def _endpoint_exists(uri: str) -> bool:
        if not uri or uri in deleted_uris:
            return False
        if uri in upsert_uris:
            return True
        if uri in endpoint_exists_cache:
            return endpoint_exists_cache[uri]
        if viking_fs is None:
            endpoint_exists_cache[uri] = False
            return False
        try:
            content = await viking_fs.read_file(uri, ctx=ctx)
            exists = bool(content)
        except Exception:
            exists = False
        endpoint_exists_cache[uri] = exists
        return exists

    valid_links: list[StoredLink] = []
    dropped = 0
    for link in merge_link_lists(links):
        if await _endpoint_exists(link.from_uri) and await _endpoint_exists(link.to_uri):
            valid_links.append(link)
        else:
            dropped += 1

    tracer.info(
        "[streaming_memory_updater] links filtered "
        f"input_links={len(links)} output_links={len(valid_links)} dropped_links={dropped}",
        console=trace_console,
    )
    return valid_links


def split_links_for_append_only_ops(
    links: list[StoredLink],
    *,
    append_ops: list[ResolvedOperation],
    merge_ops: list[ResolvedOperation],
) -> tuple[list[StoredLink], list[StoredLink]]:
    append_uris = {uri for op in append_ops for uri in (op.uris or []) if uri}
    merge_uris = {uri for op in merge_ops for uri in (op.uris or []) if uri}
    append_links: list[StoredLink] = []
    merge_links: list[StoredLink] = []
    for link in links:
        touches_append = link.from_uri in append_uris or link.to_uri in append_uris
        touches_merge = link.from_uri in merge_uris or link.to_uri in merge_uris
        if touches_append and not touches_merge:
            append_links.append(link)
        else:
            merge_links.append(link)
    return append_links, merge_links


def clone_memory_update_request(
    request: MemoryUpdateRequest,
    *,
    operations: ResolvedOperations,
) -> MemoryUpdateRequest:
    return MemoryUpdateRequest(
        operations=operations,
        messages=list(request.messages or []),
        ctx=request.ctx,
        strict_extract_errors=request.strict_extract_errors,
        isolation_options=dict(request.isolation_options or {}),
        metadata=dict(request.metadata or {}),
    )


def scope_memory_update_result_to_submitter(
    result: StreamingMemoryUpdateResult,
    request: MemoryUpdateRequest,
) -> StreamingMemoryUpdateResult:
    """Return the submitting request's view of a shared streaming flush.

    StreamingBatcher intentionally resolves every waiter in one flush with the
    same aggregate batch result. Per-session consumers (archive memory_diff,
    contexts, case URI mapping) must not see writes/deletes that were produced
    by other concurrently flushed commits.
    """

    scope = _memory_submitter_scope_from_request(request)
    if scope.is_empty:
        return result

    scoped_operations = _scope_operations_to_submitter(result.operations, scope=scope)
    operation_uris = _operation_uri_set(scoped_operations)
    submitter_uris = _request_uri_set(request)
    scoped_link_uris = _link_endpoint_uri_set(
        getattr(scoped_operations, "resolved_links", []) or []
    )
    scoped_uris = operation_uris | submitter_uris | scoped_link_uris

    scoped_apply_result = _scope_apply_result_to_uris(
        result.apply_result,
        scoped_uris=scoped_uris,
    )
    metadata = dict(result.metadata or {})
    metadata.update(
        {
            "batch_request_count": result.request_count,
            "batch_operation_count": metadata.get("operation_count"),
            "request_count": 1,
            "operation_count": _operation_count(scoped_operations),
            "source": "streaming_memory_scoped",
            "scoped_to_submitter": True,
        }
    )
    if scope.extraction_id:
        metadata["scoped_to_source_extraction_id"] = scope.extraction_id
    if scope.archive_uri:
        metadata["scoped_to_archive_uri"] = scope.archive_uri
    if scope.session_id:
        metadata["scoped_to_session_id"] = scope.session_id
    metadata["unscoped_written_uris"] = list(getattr(result.apply_result, "written_uris", []) or [])
    metadata["unscoped_edited_uris"] = list(getattr(result.apply_result, "edited_uris", []) or [])
    metadata["unscoped_deleted_uris"] = list(getattr(result.apply_result, "deleted_uris", []) or [])

    return StreamingMemoryUpdateResult(
        operations=scoped_operations,
        apply_result=scoped_apply_result,
        request_count=1,
        metadata=metadata,
    )


@dataclass(frozen=True, slots=True)
class _MemorySubmitterScope:
    extraction_id: str | None = None
    session_id: str | None = None
    archive_uri: str | None = None
    request_uris: frozenset[str] = frozenset()

    @property
    def is_empty(self) -> bool:
        return (
            not self.extraction_id
            and not self.session_id
            and not self.archive_uri
            and not self.request_uris
        )


def _memory_submitter_scope_from_request(request: MemoryUpdateRequest) -> _MemorySubmitterScope:
    metadata = dict(getattr(request, "metadata", {}) or {})
    source = memory_operation_source_from_request(request)
    extraction_id = _optional_str(
        metadata.get("source_extraction_id")
        or metadata.get("extraction_id")
        or getattr(source, "extraction_id", None)
    )
    return _MemorySubmitterScope(
        extraction_id=extraction_id,
        session_id=_optional_str(metadata.get("session_id") or getattr(source, "session_id", None)),
        archive_uri=_optional_str(
            metadata.get("archive_uri") or getattr(source, "archive_uri", None)
        ),
        request_uris=frozenset(_request_uri_set(request)),
    )


def _scope_operations_to_submitter(
    operations: ResolvedOperations,
    *,
    scope: _MemorySubmitterScope,
) -> ResolvedOperations:
    upserts = [
        op
        for op in list(getattr(operations, "upsert_operations", []) or [])
        if _operation_matches_scope(op, scope=scope)
    ]
    deletes = [
        file
        for file in list(getattr(operations, "delete_file_contents", []) or [])
        if _memory_file_matches_scope(file, scope=scope)
    ]
    kept_uris = _operation_uri_set(
        ResolvedOperations(upsert_operations=upserts, delete_file_contents=deletes, errors=[])
    )
    request_uris = set(scope.request_uris)
    return ResolvedOperations(
        upsert_operations=upserts,
        delete_file_contents=deletes,
        errors=list(getattr(operations, "errors", []) or []),
        resolved_links=[
            link
            for link in list(getattr(operations, "resolved_links", []) or [])
            if _link_matches_scoped_uris(link, scoped_uris=kept_uris, request_uris=request_uris)
        ],
        delete_replacements={
            str(deleted_uri): str(replacement_uri)
            for deleted_uri, replacement_uri in dict(
                getattr(operations, "delete_replacements", {}) or {}
            ).items()
            if str(deleted_uri) in kept_uris or str(replacement_uri) in kept_uris
        },
    )


def _scope_apply_result_to_uris(
    apply_result: MemoryUpdateResult,
    *,
    scoped_uris: set[str],
) -> MemoryUpdateResult:
    scoped = MemoryUpdateResult()
    scoped.written_uris = [
        uri for uri in list(getattr(apply_result, "written_uris", []) or []) if uri in scoped_uris
    ]
    scoped.edited_uris = [
        uri for uri in list(getattr(apply_result, "edited_uris", []) or []) if uri in scoped_uris
    ]
    scoped.deleted_uris = [
        uri for uri in list(getattr(apply_result, "deleted_uris", []) or []) if uri in scoped_uris
    ]
    scoped.errors = [
        error
        for error in list(getattr(apply_result, "errors", []) or [])
        if _apply_error_matches_scoped_uris(error, scoped_uris=scoped_uris)
    ]
    return scoped


def _operation_matches_scope(op: ResolvedOperation, *, scope: _MemorySubmitterScope) -> bool:
    if scope.extraction_id and scope.extraction_id in _operation_source_extraction_ids(op):
        return True
    source = getattr(op, "source", None)
    if (
        scope.archive_uri
        and _optional_str(getattr(source, "archive_uri", None)) == scope.archive_uri
    ):
        return True
    if scope.session_id and _optional_str(getattr(source, "session_id", None)) == scope.session_id:
        return True
    if scope.request_uris and any(
        uri in scope.request_uris for uri in list(getattr(op, "uris", []) or [])
    ):
        return True
    return False


def _memory_file_matches_scope(file: MemoryFile, *, scope: _MemorySubmitterScope) -> bool:
    fields = dict(getattr(file, "extra_fields", {}) or {})
    if scope.extraction_id and scope.extraction_id in _source_extraction_ids_from_fields(fields):
        return True
    uri = getattr(file, "uri", None)
    return bool(uri and uri in scope.request_uris)


def _operation_source_extraction_ids(op: ResolvedOperation) -> set[str]:
    fields = dict(getattr(op, "memory_fields", {}) or {})
    ids = _source_extraction_ids_from_fields(fields)
    source_id = source_extraction_id_for_operation(op)
    if source_id:
        ids.add(source_id)
    return ids


def _source_extraction_ids_from_fields(fields: dict[str, Any]) -> set[str]:
    ids: set[str] = set()
    value = fields.get("source_extraction_id")
    if value:
        ids.add(str(value))
    values = fields.get("source_extraction_ids")
    if isinstance(values, (list, tuple, set)):
        ids.update(str(item) for item in values if item)
    elif values:
        ids.add(str(values))
    return ids


def _request_uri_set(request: MemoryUpdateRequest) -> set[str]:
    return _operation_uri_set(getattr(request, "operations", None))


def _operation_uri_set(operations: ResolvedOperations | None) -> set[str]:
    if operations is None:
        return set()
    uris = {
        uri
        for op in list(getattr(operations, "upsert_operations", []) or [])
        for uri in list(getattr(op, "uris", []) or [])
        if uri
    }
    uris.update(
        str(file.uri)
        for file in list(getattr(operations, "delete_file_contents", []) or [])
        if getattr(file, "uri", None)
    )
    return uris


def _link_endpoint_uri_set(links: list[StoredLink]) -> set[str]:
    uris: set[str] = set()
    for link in links or []:
        from_uri = str(getattr(link, "from_uri", "") or "")
        to_uri = str(getattr(link, "to_uri", "") or "")
        if from_uri:
            uris.add(from_uri)
        if to_uri:
            uris.add(to_uri)
    return uris


def _link_matches_scoped_uris(
    link: StoredLink,
    *,
    scoped_uris: set[str],
    request_uris: set[str],
) -> bool:
    if not scoped_uris:
        return False
    from_uri = str(getattr(link, "from_uri", "") or "")
    to_uri = str(getattr(link, "to_uri", "") or "")
    # Keep links between this submitter's touched files and their neighbors, but
    # do not leak links that only connect other submitters' files.
    return (
        from_uri in scoped_uris
        or to_uri in scoped_uris
        or from_uri in request_uris
        or to_uri in request_uris
    )


def _apply_error_matches_scoped_uris(error: Any, *, scoped_uris: set[str]) -> bool:
    if not scoped_uris:
        return False
    try:
        uri = error[0]
    except Exception:
        return True
    return str(uri) in scoped_uris or str(uri) == "unknown"


def combine_streaming_memory_results(
    *results: StreamingMemoryUpdateResult | None,
    fallback_request_count: int = 0,
) -> StreamingMemoryUpdateResult:
    present_results = [result for result in results if result is not None]
    if not present_results:
        return StreamingMemoryUpdateResult(
            operations=ResolvedOperations(upsert_operations=[], delete_file_contents=[], errors=[]),
            apply_result=MemoryUpdateResult(),
            request_count=fallback_request_count,
            metadata={"flush_reason": "empty", "operation_count": 0},
        )
    if len(present_results) == 1:
        return present_results[0]

    combined_operations = ResolvedOperations(
        upsert_operations=[],
        delete_file_contents=[],
        errors=[],
        resolved_links=[],
        delete_replacements={},
    )
    combined_apply_result = MemoryUpdateResult()
    metadata: dict[str, Any] = {
        "flush_reason": "+".join(
            str(result.metadata.get("flush_reason", "unknown")) for result in present_results
        ),
        "combined_result": True,
    }
    request_count = 0
    for result in present_results:
        request_count += result.request_count
        combined_operations.upsert_operations.extend(result.operations.upsert_operations or [])
        combined_operations.delete_file_contents.extend(
            result.operations.delete_file_contents or []
        )
        combined_operations.errors.extend(result.operations.errors or [])
        combined_operations.resolved_links = merge_link_lists(
            combined_operations.resolved_links,
            list(getattr(result.operations, "resolved_links", []) or []),
        )
        combined_operations.delete_replacements.update(
            dict(getattr(result.operations, "delete_replacements", {}) or {})
        )
        combined_apply_result.written_uris.extend(result.apply_result.written_uris)
        combined_apply_result.edited_uris.extend(result.apply_result.edited_uris)
        combined_apply_result.deleted_uris.extend(result.apply_result.deleted_uris)
        combined_apply_result.errors.extend(result.apply_result.errors)
        for key in ("batch_id", "batch_trace_id"):
            if result.metadata.get(key):
                metadata.setdefault(key, result.metadata.get(key))
        if result.metadata.get("fast_path"):
            metadata["fast_path"] = True
    metadata["operation_count"] = _operation_count(combined_operations)
    return StreamingMemoryUpdateResult(
        operations=combined_operations,
        apply_result=combined_apply_result,
        request_count=request_count or fallback_request_count,
        metadata=metadata,
    )


def _combined_request_messages(items: list[MemoryUpdateRequest]) -> list[Message]:
    messages: list[Message] = []
    for item in items:
        messages.extend(item.messages)
    return messages


def _make_isolation_handler(
    request: MemoryUpdateRequest,
    extract_context: ExtractContext,
) -> MemoryIsolationHandler:
    options = dict(request.isolation_options or {})
    return MemoryIsolationHandler(
        request.ctx,
        extract_context,
        allowed_memory_types=options.get("allowed_memory_types"),
        allow_self=options.get("allow_self", True),
        allowed_peer_ids=options.get("allowed_peer_ids"),
    )


def _operation_count(operations: ResolvedOperations) -> int:
    return len(operations.upsert_operations or []) + len(operations.delete_file_contents or [])


def _first_uri(uris: list[str] | None) -> str | None:
    return uris[0] if uris else None


_streaming_memory_updater_registry: dict[Hashable, StreamingMemoryUpdater] = {}
_streaming_memory_updater_registry_lock = threading.RLock()


async def get_streaming_memory_updater(
    *,
    key: StreamingMemoryUpdaterKey | Hashable,
    registry: MemoryTypeRegistry | None = None,
    vikingdb: Any = None,
    config: StreamingMemoryUpdaterConfig | None = None,
) -> StreamingMemoryUpdater:
    """Get or create the process-global streaming updater for one user key."""

    with _streaming_memory_updater_registry_lock:
        existing = _streaming_memory_updater_registry.get(key)
        if existing is not None:
            return existing
        updater = StreamingMemoryUpdater(
            registry=registry,
            vikingdb=vikingdb,
            config=config or StreamingMemoryUpdaterConfig(),
        )
        _streaming_memory_updater_registry[key] = updater
        return updater


def make_streaming_memory_updater_key(*, request_context: Any) -> StreamingMemoryUpdaterKey:
    user = getattr(request_context, "user", None)
    account_id = (
        getattr(request_context, "account_id", None)
        or getattr(user, "account_id", None)
        or "default"
    )
    user_id = getattr(request_context, "user_id", None) or getattr(user, "user_id", None) or ""
    return StreamingMemoryUpdaterKey(account_id=str(account_id), user_id=str(user_id))
