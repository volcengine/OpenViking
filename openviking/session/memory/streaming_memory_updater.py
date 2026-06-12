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
import threading
from dataclasses import dataclass, field
from typing import Any, Hashable

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
)
from openviking.session.memory.merge_op import MergeOpFactory
from openviking.session.memory.patch_merge_context_provider import (
    PatchMergeContextProvider,
    PatchMergePatch,
)
from openviking.session.memory.utils.memory_file_utils import MemoryFileUtils
from openviking.session.memory.utils.streaming_batcher import (
    StreamingBatcher,
    StreamingBatcherConfig,
)
from openviking.storage.viking_fs import get_viking_fs
from openviking.telemetry import tracer
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
    _batcher: StreamingBatcher[MemoryUpdateRequest, StreamingMemoryUpdateResult] = field(
        init=False, repr=False
    )
    _apply_lock: asyncio.Lock = field(init=False, repr=False)
    _last_result: StreamingMemoryUpdateResult | None = field(init=False, default=None, repr=False)
    _closed: bool = field(init=False, default=False, repr=False)

    def __post_init__(self) -> None:
        self.registry = self.registry or create_default_registry()
        self._batcher = StreamingBatcher(
            name="openviking-streaming-memory-updater",
            process_batch=self._process_batch,
            config=StreamingBatcherConfig(
                max_items_per_batch=self.config.max_operations_per_update,
                max_wait_seconds=self.config.max_wait_seconds,
                timer_check_interval_seconds=self.config.timer_check_interval_seconds,
            ),
            item_size=lambda request: _operation_count(request.operations),
            result_metadata=lambda result: result.metadata,
        )
        self._apply_lock = asyncio.Lock()
        self._last_result = None
        self._closed = False

    @property
    def closed(self) -> bool:
        return self._closed

    @property
    def last_result(self) -> StreamingMemoryUpdateResult | None:
        return self._last_result

    async def get_buffered_operation_count(self) -> int:
        return await self._batcher.get_buffered_size()

    async def close(self) -> StreamingMemoryUpdateResult | None:
        if self._closed:
            return None
        self._closed = True
        return await self._batcher.close()

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
        merge_result = await self._batcher.submit(merge_request) if merge_request is not None else None
        result = combine_streaming_memory_results(
            append_result,
            merge_result,
            fallback_request_count=1,
        )
        self._last_result = result
        tracer.info(
            "StreamingMemoryUpdater submit finished "
            f"batch_id={result.metadata.get('batch_id')} "
            f"batch_trace_id={result.metadata.get('batch_trace_id')} "
            f"flush_reason={result.metadata.get('flush_reason')} "
            f"request_count={result.request_count} "
            f"operation_count={result.metadata.get('operation_count')} "
            f"written_uris={result.apply_result.written_uris} "
            f"edited_uris={result.apply_result.edited_uris} "
            f"deleted_uris={result.apply_result.deleted_uris} "
            f"errors={result.apply_result.errors}",
            console=self.config.trace_console,
        )
        return result

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
        requests: list[MemoryUpdateRequest],
        reason: str,
    ) -> StreamingMemoryUpdateResult:
        input_operations = sum(_operation_count(request.operations) for request in requests)
        input_patches = sum(
            len(getattr(request.operations, "upsert_operations", []) or [])
            for request in requests
        )
        input_deletes = sum(
            len(getattr(request.operations, "delete_file_contents", []) or [])
            for request in requests
        )
        tracer.info(
            "StreamingMemoryUpdater flush started "
            f"reason={reason} request_count={len(requests)} "
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
            },
        )
        self._last_result = result
        tracer.info(
            "StreamingMemoryUpdater flush finished "
            f"reason={reason} request_count={len(requests)} "
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
        )
        for request in requests:
            ops = request.operations
            all_ops.upsert_operations.extend(list(ops.upsert_operations or []))
            all_ops.delete_file_contents.extend(list(ops.delete_file_contents or []))
            all_ops.errors.extend(list(ops.errors or []))
            all_ops.resolved_links.extend(list(getattr(ops, "resolved_links", []) or []))
        return await merge_memory_operations(
            operations=all_ops,
            messages=_combined_request_messages(requests),
            ctx=requests[0].ctx,
            registry=self.registry or create_default_registry(),
            strict_extract_errors=any(request.strict_extract_errors for request in requests),
            trace_console=self.config.trace_console,
        )


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

    groups: dict[str, list[ResolvedOperation]] = {}
    passthrough_upserts: list[ResolvedOperation] = []
    for op in operations.upsert_operations:
        if not op.uris:
            passthrough_upserts.append(op)
            continue
        for uri in op.uris:
            single_uri_op = clone_operation_for_uri(op, uri)
            groups.setdefault(single_uri_op.memory_type, []).append(single_uri_op)

    tracer.info(
        "[streaming_memory_updater] merge batch "
        f"patch_count={len(operations.upsert_operations or [])} "
        f"delete_count={len(operations.delete_file_contents or [])} "
        f"passthrough_upserts={len(passthrough_upserts)} "
        f"memory_type_count={len(groups)} "
        f"memory_types={sorted(groups.keys())}",
        console=trace_console,
    )

    merged_upserts = list(passthrough_upserts)
    merged_deletes = list(operations.delete_file_contents)
    merged_links = merge_link_lists(list(getattr(operations, "resolved_links", []) or []))
    registry = registry or create_default_registry()
    merge_results = await asyncio.gather(
        *[
            _merge_memory_type_group(
                memory_type=memory_type,
                operations=memory_ops,
                messages=messages,
                ctx=ctx,
                registry=registry,
                trace_console=trace_console,
            )
            for memory_type, memory_ops in groups.items()
        ],
        return_exceptions=True,
    )

    for (memory_type, memory_ops), merge_result in zip(
        groups.items(), merge_results, strict=True
    ):
        if not isinstance(merge_result, Exception):
            merged = merge_result
            merged_upserts.extend(merged.upsert_operations)
            merged_deletes.extend(merged.delete_file_contents)
            merged_links = merge_link_lists(
                merged_links,
                list(getattr(merged, "resolved_links", []) or []),
            )
            continue

        tracer.info(
            "[streaming_memory_updater] merge fallback "
            f"memory_type={memory_type} mode=fallback_original "
            f"reason=llm_merge_failed patch_count={len(memory_ops)} "
            f"target_count={len(_unique_operation_uris(memory_ops))} error={merge_result}",
            console=trace_console,
        )
        logger.warning(
            "[streaming_memory_updater] merge failed for %s: %s", memory_type, merge_result
        )
        if strict_extract_errors or is_cross_extraction_group(memory_ops):
            raise merge_result
        merged_upserts.extend(memory_ops)

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
    )


async def _merge_memory_type_group(
    *,
    memory_type: str,
    operations: list[ResolvedOperation],
    messages: list[Message],
    ctx: RequestContext,
    registry: MemoryTypeRegistry,
    trace_console: bool = False,
) -> ResolvedOperations:
    return await merge_one_memory_type_operations(
        memory_type=memory_type,
        operations=operations,
        messages=messages,
        ctx=ctx,
        registry=registry,
        trace_console=trace_console,
    )


async def merge_one_memory_type_operations(
    *,
    memory_type: str,
    operations: list[ResolvedOperation],
    messages: list[Message],
    ctx: RequestContext,
    registry: MemoryTypeRegistry | None = None,
    trace_console: bool = False,
) -> ResolvedOperations:
    registry = registry or create_default_registry()
    schema = registry.get(memory_type)
    patch_count = len(operations)
    target_uris = _unique_operation_uris(operations)
    target_count = len(target_uris)
    existing_file_count = sum(
        1 for op in operations if getattr(op, "old_memory_file_content", None) is not None
    )
    duplicate_target_count = patch_count - target_count
    operation_mode = (
        getattr(schema, "operation_mode", "unknown") if schema is not None else "unknown"
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
        )

    tracer.info(
        "[streaming_memory_updater] memory_type merge decision "
        f"memory_type={memory_type} mode=llm_merge "
        f"reason={fast_path_reason} operation_mode={operation_mode} "
        f"patch_count={patch_count} target_count={target_count} "
        f"duplicate_target_count={duplicate_target_count} "
        f"existing_file_count={existing_file_count}",
        console=trace_console,
    )

    if schema is None:
        raise ValueError(f"Memory schema not found: {memory_type}")

    extract_context = ExtractContext(messages)
    required_file_uris = list(
        dict.fromkeys(
            uri
            for op in operations
            for uri in op.uris
            if getattr(op, "old_memory_file_content", None) is not None
        )
    )
    patches = [
        operation_to_patch(op, schema=schema, extract_context=extract_context) for op in operations
    ]
    provider = PatchMergeContextProvider(
        memory_type=memory_type,
        required_file_uris=required_file_uris,
        patches=patches,
    )
    provider._ctx = ctx
    provider._viking_fs = safe_get_viking_fs()
    provider._extract_context = extract_context
    isolation_handler = MemoryIsolationHandler(
        ctx, extract_context, allowed_memory_types={memory_type}
    )
    isolation_handler.prepare_messages()
    provider._isolation_handler = isolation_handler
    seed_patch_merge_read_contents(provider, operations)
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
    existing_input_uris = {
        uri
        for op in operations
        if getattr(op, "old_memory_file_content", None) is not None
        for uri in (op.uris or [])
        if uri
    }
    output_upsert_uris = {
        uri for op in (merged.upsert_operations or []) for uri in (op.uris or []) if uri
    }
    missing_delete_uris = sorted(existing_input_uris - output_upsert_uris)
    if missing_delete_uris:
        existing_by_uri = {
            uri: getattr(op, "old_memory_file_content", None)
            for op in operations
            for uri in (op.uris or [])
            if getattr(op, "old_memory_file_content", None) is not None
        }
        existing_delete_uris = {
            file.uri for file in (merged.delete_file_contents or []) if getattr(file, "uri", None)
        }
        for uri in missing_delete_uris:
            if uri in existing_delete_uris:
                continue
            old_file = existing_by_uri.get(uri)
            if old_file is not None:
                merged.delete_file_contents.append(old_file)
                existing_delete_uris.add(uri)
    tracer.info(
        "[streaming_memory_updater] llm merge output "
        f"memory_type={memory_type} upserts={len(merged.upsert_operations)} "
        f"deletes={len(merged.delete_file_contents)} errors={len(merged.errors)}",
        console=trace_console,
    )
    return merged


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
    if all_new_files and duplicate_target_count == 0:
        return True, "unique_new_files"
    if len(operations) != 1:
        return False, "multi_patch_existing_or_conflict"

    op = operations[0]
    old_file = getattr(op, "old_memory_file_content", None)
    if old_file is None:
        return True, "single_new_file"
    fields = dict(getattr(op, "memory_fields", {}) or {})
    if "content" not in fields:
        return False, "single_existing_non_content_patch"
    if old_file.plain_content().strip() == str(fields.get("content") or "").strip():
        return True, "single_existing_content_unchanged"
    return False, "single_existing_content_changed"


def can_fast_path_memory_operations(
    operations: list[ResolvedOperation],
    *,
    schema: MemoryTypeSchema | None = None,
) -> bool:
    return classify_memory_merge_mode(operations, schema=schema)[0]


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
        combined_operations.delete_file_contents.extend(result.operations.delete_file_contents or [])
        combined_operations.errors.extend(result.operations.errors or [])
        combined_operations.resolved_links = merge_link_lists(
            combined_operations.resolved_links,
            list(getattr(result.operations, "resolved_links", []) or []),
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
