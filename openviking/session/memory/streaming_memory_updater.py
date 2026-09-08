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
import hashlib
import json
import re
import threading
from collections.abc import Iterable
from dataclasses import dataclass, field
from typing import Any, Hashable, Optional, Union

from pydantic import BaseModel, ConfigDict, Field, ValidationError, create_model

from openviking.core.peer_id import safe_peer_id
from openviking.message import Message
from openviking.server.identity import RequestContext
from openviking.session.memory.case_aggregation import (
    CASE_DYNAMIC_FIELDS,
    CASE_IDENTITY_FIELD,
    CASE_MEMORY_TYPE,
    CASE_PENDING_SOURCES_FIELD,
    CASE_SOURCE_IDS_FIELD,
    PROPOSED_CASE_IDENTITY_FIELD,
    CaseIdentity,
    CaseIdentityComparison,
    case_identity_generalization_violations,
    case_input_generalization_violations,
    fallback_case_identity,
    generalize_case_year_literals,
    merged_case_pending_sources,
    merged_case_source_state,
    normalize_case_status,
    parse_case_identity,
    prepare_case_operation,
    select_case_primary,
    should_compact_case,
)
from openviking.session.memory.dataclass import (
    MemoryFile,
    MemoryOperationSource,
    MemoryTypeSchema,
    ResolvedOperation,
    ResolvedOperations,
    SkippedMemoryOperation,
    StoredLink,
)
from openviking.session.memory.experience_case_links import (
    acquire_case_link_lease,
    release_case_link_lease,
)
from openviking.session.memory.experience_lifecycle import (
    experience_case_link_uris,
    experience_is_case_linkable,
)
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
    render_operation_after_file,
    render_operation_after_file_content,
    write_stored_links,
)
from openviking.session.memory.merge_op import MergeOp, MergeOpFactory
from openviking.session.memory.merge_op.base import get_python_type_for_field
from openviking.session.memory.merge_op.link_merge import merge_links
from openviking.session.memory.patch_merge_context_provider import (
    PatchMergeContextProvider,
    PatchMergePatch,
    candidate_id_for_uri,
)
from openviking.session.memory.session_extract_context_provider import SessionExtractContextProvider
from openviking.session.memory.utils.json_parser import parse_json_strict
from openviking.session.memory.utils.memory_file_utils import MemoryFileUtils, bump_memory_version
from openviking.session.memory.utils.streaming_batcher import (
    StreamingBatcher,
    StreamingBatcherConfig,
    StreamingBatchItemOutcome,
    StreamingBatchResults,
)
from openviking.storage.viking_fs import get_viking_fs
from openviking.telemetry import tracer
from openviking.telemetry.tracer import get_trace_id
from openviking_cli.exceptions import NotFoundError
from openviking_cli.utils import get_logger
from openviking_cli.utils.config import get_openviking_config

logger = get_logger(__name__)

_MEMORY_APPLY_LOCK_TIMEOUT_SECONDS = 300.0
_MEMORY_APPLY_LOCK_MAX_ACQUISITIONS = 3


class MemoryMergePlanError(ValueError):
    """Raised when a merge plan is truncated, malformed, or incomplete."""


class MemoryMergePlanParseError(MemoryMergePlanError):
    """Raised when a merge plan is not a complete JSON document."""


class _MergePlanFieldOperationsBase(BaseModel):
    model_config = ConfigDict(extra="forbid")


class _MergePlanGroupBase(BaseModel):
    model_config = ConfigDict(extra="forbid")


class _MergePlanBase(BaseModel):
    model_config = ConfigDict(extra="forbid")


class _CaseComparisonRepairResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    case_comparisons: list[CaseIdentityComparison]


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
class MemoryMergeProposal:
    """One required patch proposal or optional stored candidate in a merge plan."""

    proposal_id: str
    patch: PatchMergePatch
    operation: ResolvedOperation | None = None
    delete_file: MemoryFile | None = None
    is_candidate: bool = False

    @property
    def is_explicit_delete(self) -> bool:
        return self.delete_file is not None


@dataclass(slots=True)
class StreamingMemoryUpdateResult:
    """Result returned when a submit triggers a flush."""

    operations: ResolvedOperations
    apply_result: MemoryUpdateResult
    request_count: int
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass(slots=True)
class _MergedRequestPartition:
    """One mergeable request subset, or one isolated failing request."""

    indexes: list[int]
    requests: list[MemoryUpdateRequest]
    operations: ResolvedOperations | None = None
    exception: Exception | None = None


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
        # Collect embedded relations before splitting memory types. Otherwise
        # the Case group can finish before a same-request EXP exists and lose
        # the only copy of its deferred link.
        _extract_case_experience_links(request.operations)
        append_only_request, merge_request = self._split_append_only_request(request)
        append_result = (
            await self._apply_append_only_request_now(append_only_request)
            if append_only_request is not None
            else None
        )
        if (
            append_only_request is not None
            and append_result is not None
            and merge_request is not None
        ):
            allocated_uris = {}
            for before, after in zip(
                append_only_request.operations.upsert_operations,
                append_result.operations.upsert_operations,
                strict=True,
            ):
                for previous_uri, allocated_uri in zip(before.uris, after.uris, strict=True):
                    allocated_uris.setdefault(previous_uri, allocated_uri)
            merge_request.operations.resolved_links = _remap_allocated_links_once(
                merge_request.operations.resolved_links,
                allocated_uris,
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
            f"skipped_reason_codes={_skipped_reason_codes(scoped_result.apply_result)} "
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
        bounded_group_requests = [
            (group_key, chunk)
            for group_key, group_request in grouped_requests
            for chunk in split_memory_update_request_by_operation_limit(
                group_request,
                self._operation_limit_for_group(group_key),
            )
        ]
        submissions = [
            (await self._get_group_batcher(group_key)).submit(group_request)
            for group_key, group_request in bounded_group_requests
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
            links,
            {
                **dict(getattr(result.operations, "delete_replacements", {}) or {}),
                **dict(getattr(result.operations, "link_replacements", {}) or {}),
            },
        )
        viking_fs = safe_get_viking_fs()
        lock_paths = _uri_lock_paths(_link_endpoint_uri_set(links), viking_fs, request.ctx)
        async with self._apply_lock:
            lease = None
            if lock_paths:
                lease = await acquire_case_link_lease(
                    viking_fs._async_agfs,
                    lock_paths,
                )
            try:
                valid_links = await filter_valid_links(
                    links,
                    # Group writes have finished. Their operation previews may
                    # be stale (or may have failed to persist); only the current
                    # endpoint files read under this lease can authorize links.
                    upsert_operations=[],
                    delete_file_contents=result.operations.delete_file_contents,
                    ctx=request.ctx,
                    trace_console=self.config.trace_console,
                )
                if not valid_links:
                    return
                if viking_fs is not None:
                    case_links = [link for link in valid_links if _is_case_experience_link(link)]
                    valid_links = [
                        link for link in valid_links if not _is_case_experience_link(link)
                    ]
                    updated_uris = await write_stored_links(
                        valid_links,
                        request.ctx,
                        viking_fs,
                        lease_ref=lease,
                    )
                    for uri in dict.fromkeys(updated_uris):
                        result.apply_result.add_edited(uri)
                    valid_links.extend(
                        await self._write_case_experience_links(
                            case_links, request.ctx, viking_fs, lease, result.apply_result
                        )
                    )
                result.operations.resolved_links = merge_link_lists(
                    list(getattr(result.operations, "resolved_links", []) or []),
                    valid_links,
                )
            finally:
                if lease is not None:
                    await release_case_link_lease(viking_fs._async_agfs, lease)

    async def _write_case_experience_links(
        self,
        links: list[StoredLink],
        ctx: RequestContext,
        viking_fs: Any,
        lease: Any,
        result: MemoryUpdateResult,
    ) -> list[StoredLink]:
        """Publish validated links under their endpoint lease, backlink first.

        A failed EXP write must not expose a forward link. A failed Case write
        may leave a backlink, which retains the information needed for repair.
        """
        if not links:
            return []
        case_uris = {link.from_uri for link in links}
        updated_experiences = set(
            await write_stored_links(links, ctx, viking_fs, skip_uris=case_uris, lease_ref=lease)
        )
        for uri in {link.to_uri for link in links} - updated_experiences:
            result.add_error(uri, RuntimeError("Failed to persist Case/Experience backlink"))
        for uri in sorted(updated_experiences):
            if uri not in result.written_uris and uri not in result.edited_uris:
                result.add_edited(uri)
            # The operation cache predates the link-only write.
            result.files_by_uri.pop(uri, None)

        schema = (self.registry or create_default_registry()).get(CASE_MEMORY_TYPE)
        published: list[StoredLink] = []
        for case_uri in sorted(case_uris):
            case_links = [
                link
                for link in links
                if link.from_uri == case_uri and link.to_uri in updated_experiences
            ]
            if not case_links:
                continue
            try:
                raw = await viking_fs.read_file(case_uri, ctx=ctx)
                case = MemoryFileUtils.read(raw, uri=case_uri)
                case.links = merge_links(case.links, [link.model_dump() for link in case_links])
                trace_id = get_trace_id()
                if trace_id:
                    case.extra_fields["last_update_trace_id"] = trace_id
                bump_memory_version(case)
                rendered = MemoryFileUtils.write(
                    case, content_template=getattr(schema, "content_template", None)
                )
                await viking_fs.write_file(case_uri, rendered, ctx=ctx, lease_ref=lease)
                result.cache_file(case_uri, MemoryFileUtils.read(rendered, uri=case_uri))
                if case_uri not in result.written_uris and case_uri not in result.edited_uris:
                    result.add_edited(case_uri)
                published.extend(case_links)
            except Exception as exc:
                result.add_error(case_uri, exc)
                tracer.error(f"Failed to publish Case/Experience links to {case_uri}: {exc}")
        return published

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
        async def process_batch_items(
            requests: list[MemoryUpdateRequest],
            reason: str,
        ) -> StreamingBatchResults[StreamingMemoryUpdateResult]:
            return await self._process_batch_items(group_key, requests, reason)

        batcher = StreamingBatcher(
            name=(
                "openviking-streaming-memory-updater:"
                f"{group_key.peer_id or 'self'}:{group_key.memory_type}"
            ),
            process_batch_items=process_batch_items,
            config=StreamingBatcherConfig(
                max_items_per_batch=self._operation_limit_for_group(group_key),
                max_wait_seconds=self.config.max_wait_seconds,
                timer_check_interval_seconds=self.config.timer_check_interval_seconds,
            ),
            item_size=lambda request: _operation_count(request.operations),
            result_metadata=lambda result: result.metadata,
        )
        return batcher

    def _operation_limit_for_group(self, group_key: MemoryMergeGroupKey) -> int:
        if group_key.memory_type == CASE_MEMORY_TYPE:
            # Apply Case proposals sequentially so every proposal compares
            # against already-persisted candidates, never another in-flight proposal.
            return 1
        return self.config.max_operations_per_update

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
            defer_case_experience_links=True,
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
            f"skipped_reason_codes={_skipped_reason_codes(apply_result)} "
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
        return await self._apply_merged_batch(
            group_key=group_key,
            requests=requests,
            reason=reason,
            merged_operations=merged_operations,
        )

    async def _process_batch_items(
        self,
        group_key: MemoryMergeGroupKey,
        requests: list[MemoryUpdateRequest],
        reason: str,
    ) -> StreamingBatchResults[StreamingMemoryUpdateResult]:
        """Merge with bisection before writes and settle each submit independently."""

        input_operations = sum(_operation_count(request.operations) for request in requests)
        tracer.info(
            "StreamingMemoryUpdater flush started "
            f"group={group_key} reason={reason} request_count={len(requests)} "
            f"input_operations={input_operations}",
            console=self.config.trace_console,
        )
        partitions = await self._partition_mergeable_requests(
            group_key=group_key,
            indexed_requests=list(enumerate(requests)),
        )
        item_outcomes: list[StreamingBatchItemOutcome[StreamingMemoryUpdateResult] | None] = [
            None
        ] * len(requests)
        successful_results: list[StreamingMemoryUpdateResult] = []
        isolation_applied = len(partitions) > 1

        for partition in partitions:
            if partition.exception is not None:
                for index in partition.indexes:
                    item_outcomes[index] = StreamingBatchItemOutcome.failure(partition.exception)
                continue

            assert partition.operations is not None
            try:
                result = await self._apply_merged_batch(
                    group_key=group_key,
                    requests=partition.requests,
                    reason=reason,
                    merged_operations=partition.operations,
                    metadata={
                        "failure_isolation_applied": isolation_applied,
                        "original_batch_request_count": len(requests),
                        "isolated_subbatch_count": len(partitions),
                    },
                )
            except Exception as exc:
                # Applying may already have produced side effects. Never retry or
                # bisect this partition after writes have started.
                for index in partition.indexes:
                    item_outcomes[index] = StreamingBatchItemOutcome.failure(exc)
                continue

            successful_results.append(result)
            for index in partition.indexes:
                item_outcomes[index] = StreamingBatchItemOutcome.success(result)

        if any(outcome is None for outcome in item_outcomes):
            raise RuntimeError("memory batch isolation left an item without an outcome")

        aggregate_result = (
            combine_streaming_memory_results(*successful_results) if successful_results else None
        )
        return StreamingBatchResults(
            item_outcomes=[outcome for outcome in item_outcomes if outcome is not None],
            aggregate_result=aggregate_result,
        )

    async def _partition_mergeable_requests(
        self,
        *,
        group_key: MemoryMergeGroupKey,
        indexed_requests: list[tuple[int, MemoryUpdateRequest]],
    ) -> list[_MergedRequestPartition]:
        """Recursively isolate merge failures without executing any writes."""

        indexes = [index for index, _ in indexed_requests]
        requests = [request for _, request in indexed_requests]
        try:
            operations = await self._merge_requests(requests)
        except Exception as exc:
            if len(indexed_requests) == 1:
                logger.warning(
                    "StreamingMemoryUpdater isolated failing merge request "
                    "group=%s request_index=%s error=%s",
                    group_key,
                    indexes[0],
                    exc,
                )
                return [
                    _MergedRequestPartition(
                        indexes=indexes,
                        requests=requests,
                        exception=exc,
                    )
                ]

            midpoint = len(indexed_requests) // 2
            tracer.info(
                "StreamingMemoryUpdater bisecting failed merge batch "
                f"group={group_key} request_count={len(indexed_requests)} "
                f"left_count={midpoint} right_count={len(indexed_requests) - midpoint} "
                f"error={exc}",
                console=self.config.trace_console,
            )
            left = await self._partition_mergeable_requests(
                group_key=group_key,
                indexed_requests=indexed_requests[:midpoint],
            )
            right = await self._partition_mergeable_requests(
                group_key=group_key,
                indexed_requests=indexed_requests[midpoint:],
            )
            return [*left, *right]

        return [
            _MergedRequestPartition(
                indexes=indexes,
                requests=requests,
                operations=operations,
            )
        ]

    async def _apply_merged_batch(
        self,
        *,
        group_key: MemoryMergeGroupKey,
        requests: list[MemoryUpdateRequest],
        reason: str,
        merged_operations: ResolvedOperations,
        metadata: dict[str, Any] | None = None,
    ) -> StreamingMemoryUpdateResult:
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
                **(metadata or {}),
            },
        )
        self._last_result = result
        tracer.info(
            "StreamingMemoryUpdater flush finished "
            f"group={group_key} reason={reason} request_count={len(requests)} "
            f"written_uris={apply_result.written_uris} "
            f"edited_uris={apply_result.edited_uris} "
            f"deleted_uris={apply_result.deleted_uris} "
            f"skipped_reason_codes={_skipped_reason_codes(apply_result)} "
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
        extract_context = ExtractContext(messages)
        isolation_handler = _make_isolation_handler(request, extract_context)
        # Links embedded in upsert fields must follow the same publication rule
        # as resolved_links, never bypass it through field merging.
        deferred_links = _extract_case_experience_links(operations)
        original_uris = [
            (operation, list(operation.uris)) for operation in operations.upsert_operations
        ]
        async with self._apply_lock:
            viking_fs = safe_get_viking_fs()
            MemoryUpdater._convert_experience_deletes_to_archives(operations)
            lease = await _acquire_stable_operation_lease(
                operations,
                viking_fs,
                request.ctx,
            )
            updater = MemoryUpdater(
                registry=self.registry,
                vikingdb=self.vikingdb,
                transaction_handle=lease,
                defer_archived_vector_cleanup=True,
            )
            try:
                operations.resolved_links = [
                    link for link in operations.resolved_links if not _is_case_experience_link(link)
                ]
                apply_result = await updater.apply_operations(
                    operations,
                    request.ctx,
                    extract_context=extract_context,
                    isolation_handler=isolation_handler,
                )
            finally:
                if lease is not None:
                    await viking_fs._async_agfs.pathlock_release(lease)
            await updater._remove_archived_vectors(apply_result, request.ctx)
        if deferred_links and not operations.has_errors():
            # Acquire a fresh endpoint lease for the actual files; never reuse
            # the old URI's lease or publish from an operation preview.
            allocated_uris = {}
            for operation, previous_uris in original_uris:
                for previous_uri, allocated_uri in zip(previous_uris, operation.uris, strict=True):
                    allocated_uris.setdefault(previous_uri, allocated_uri)
            await self._apply_post_group_links(
                clone_memory_update_request(
                    request,
                    operations=operations.model_copy(
                        update={
                            "resolved_links": _remap_allocated_links_once(
                                deferred_links, allocated_uris
                            )
                        }
                    ),
                ),
                StreamingMemoryUpdateResult(
                    operations=operations, apply_result=apply_result, request_count=1
                ),
            )
        return apply_result

    async def _merge_requests(self, requests: list[MemoryUpdateRequest]) -> ResolvedOperations:
        all_ops = _combine_resolved_operations(request.operations for request in requests)
        if all_ops.has_errors():
            return all_ops

        requests_by_kind: dict[str, list[MemoryUpdateRequest]] = {
            "add": [],
            "update": [],
            "delete": [],
        }
        for request in requests:
            adds = [
                op
                for op in request.operations.upsert_operations
                if op.old_memory_file_content is None
            ]
            updates = [
                op
                for op in request.operations.upsert_operations
                if op.old_memory_file_content is not None
            ]
            for kind, upserts, deletes in (
                ("add", adds, []),
                ("update", updates, []),
                ("delete", [], list(request.operations.delete_file_contents or [])),
            ):
                if not upserts and not deletes:
                    continue
                requests_by_kind[kind].append(
                    clone_memory_update_request(
                        request,
                        operations=ResolvedOperations(
                            upsert_operations=upserts,
                            delete_file_contents=deletes,
                            errors=[],
                            resolved_links=[],
                            delete_replacements={
                                file.uri: replacement_uri
                                for file in deletes
                                if file.uri
                                if (
                                    replacement_uri := request.operations.delete_replacements.get(
                                        file.uri
                                    )
                                )
                            },
                            link_replacements=dict(
                                getattr(request.operations, "link_replacements", {}) or {}
                            ),
                        ),
                    )
                )

        async def merge_kind(kind_requests: list[MemoryUpdateRequest]) -> ResolvedOperations:
            operations = _combine_resolved_operations(
                request.operations for request in kind_requests
            )
            spans_sessions = _requests_span_sessions(kind_requests)
            if spans_sessions:
                return await merge_memory_operations(
                    operations=operations,
                    messages=_combined_request_messages(kind_requests),
                    ctx=kind_requests[0].ctx,
                    registry=self.registry or create_default_registry(),
                    strict_extract_errors=any(
                        request.strict_extract_errors for request in kind_requests
                    ),
                    trace_console=self.config.trace_console,
                    force_merge=True,
                )

            case_upserts = [
                operation
                for operation in operations.upsert_operations
                if operation.memory_type == CASE_MEMORY_TYPE
            ]
            if not case_upserts:
                return operations

            # Case upserts require system-managed identity, lifecycle, and source
            # fields even for a single session. Keep other memory types on the
            # existing same-session passthrough path.
            case_result = await merge_memory_operations(
                operations=ResolvedOperations(
                    upsert_operations=case_upserts,
                    delete_file_contents=[],
                    errors=[],
                    resolved_links=[],
                    delete_replacements={},
                    link_replacements={},
                ),
                messages=_combined_request_messages(kind_requests),
                ctx=kind_requests[0].ctx,
                registry=self.registry or create_default_registry(),
                strict_extract_errors=any(
                    request.strict_extract_errors for request in kind_requests
                ),
                trace_console=self.config.trace_console,
                force_merge=False,
            )
            passthrough = operations.model_copy(
                update={
                    "upsert_operations": [
                        operation
                        for operation in operations.upsert_operations
                        if operation.memory_type != CASE_MEMORY_TYPE
                    ]
                }
            )
            return _combine_resolved_operations([passthrough, case_result])

        kind_batches = [
            (kind, kind_requests)
            for kind, kind_requests in requests_by_kind.items()
            if kind_requests
        ]
        kind_results = await asyncio.gather(
            *(merge_kind(kind_requests) for _, kind_requests in kind_batches)
        )
        uri_kinds: dict[str, str] = {}
        conflicting_uris: set[str] = set()
        for (kind, _), operations in zip(kind_batches, kind_results, strict=True):
            for uri in _operation_uri_set(operations):
                previous_kind = uri_kinds.setdefault(uri, kind)
                if previous_kind != kind:
                    conflicting_uris.add(uri)
        if conflicting_uris:
            return ResolvedOperations(
                upsert_operations=[],
                delete_file_contents=[],
                errors=[
                    "Conflicting add/update/delete results for URIs: "
                    + ", ".join(sorted(conflicting_uris))
                ],
                resolved_links=[],
                delete_replacements={},
                link_replacements={},
            )
        merged = _combine_resolved_operations(kind_results)
        merged.resolved_links = merge_link_lists(
            list(all_ops.resolved_links or []),
            list(merged.resolved_links or []),
        )
        merged.link_replacements.update(all_ops.link_replacements)
        return merged


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
        # Unresolved upserts keep their original standalone passthrough group.
        # Deletes remain in their normal peer/type groups, including replacement
        # metadata, so diagnostics cannot change write/delete ordering.
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


def split_memory_update_request_by_operation_limit(
    request: MemoryUpdateRequest,
    limit: int,
) -> list[MemoryUpdateRequest]:
    """Split one merge-group request so a single batch item never exceeds the hard limit."""

    if limit <= 0:
        raise ValueError("limit must be > 0")
    operations = request.operations
    ordered_items: list[tuple[str, ResolvedOperation | MemoryFile]] = [
        ("upsert", op) for op in list(operations.upsert_operations or [])
    ]
    ordered_items.extend(
        ("delete", memory_file) for memory_file in list(operations.delete_file_contents or [])
    )
    if len(ordered_items) <= limit:
        return [request]

    chunks: list[MemoryUpdateRequest] = []
    for offset in range(0, len(ordered_items), limit):
        chunk_items = ordered_items[offset : offset + limit]
        upserts = [
            item
            for kind, item in chunk_items
            if kind == "upsert" and isinstance(item, ResolvedOperation)
        ]
        deletes = [
            item for kind, item in chunk_items if kind == "delete" and isinstance(item, MemoryFile)
        ]
        delete_uris = {memory_file.uri for memory_file in deletes if memory_file.uri}
        chunks.append(
            clone_memory_update_request(
                request,
                operations=ResolvedOperations(
                    upsert_operations=upserts,
                    delete_file_contents=deletes,
                    errors=list(operations.errors or []),
                    resolved_links=[],
                    delete_replacements={
                        deleted_uri: replacement_uri
                        for deleted_uri, replacement_uri in dict(
                            getattr(operations, "delete_replacements", {}) or {}
                        ).items()
                        if deleted_uri in delete_uris
                    },
                ),
            )
        )
    return chunks


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
    force_merge: bool = False,
) -> ResolvedOperations:
    """Merge resolved memory operations by memory type/URI using patch context."""

    del strict_extract_errors
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
    merged_link_replacements: dict[str, str] = {}
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
                force_merge=force_merge,
            )
            for (peer_id, memory_type) in all_group_keys
        ]
    )

    for (peer_id, memory_type), merge_result in zip(all_group_keys, merge_results, strict=True):
        group_key = (peer_id, memory_type)
        ops_list = upsert_groups.get(group_key, [])
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
        merged_delete_replacements.update(dict(getattr(merged, "delete_replacements", {}) or {}))
        merged_link_replacements.update(dict(getattr(merged, "link_replacements", {}) or {}))
        merged_links = merge_link_lists(
            merged_links,
            list(getattr(merged, "resolved_links", []) or []),
        )

    final_delete_uris = {file.uri for file in merged_deletes if file.uri}
    for deleted_uri, replacement_uri in dict(
        getattr(operations, "delete_replacements", {}) or {}
    ).items():
        if deleted_uri in final_delete_uris:
            merged_delete_replacements.setdefault(deleted_uri, replacement_uri)

    merged_links = await filter_valid_links(
        merged_links,
        upsert_operations=merged_upserts,
        delete_file_contents=merged_deletes,
        ctx=ctx,
        trace_console=trace_console,
        defer_case_experience_links=True,
    )
    return ResolvedOperations(
        upsert_operations=merged_upserts,
        delete_file_contents=merged_deletes,
        errors=list(operations.errors),
        resolved_links=merged_links,
        delete_replacements=merged_delete_replacements,
        link_replacements=merged_link_replacements,
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
    force_merge: bool = False,
) -> ResolvedOperations:
    registry = registry or create_default_registry()
    schema = registry.get(memory_type)
    if memory_type == CASE_MEMORY_TYPE:
        operations = [prepare_case_operation(operation) for operation in operations]
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
    if not force_merge and not operations and delete_files:
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
            delete_file_contents=list(delete_files),
            errors=[],
            resolved_links=[],
            delete_replacements={},
        )

    if force_merge:
        fast_path, fast_path_reason = False, "cross_session_batch"
    else:
        fast_path, fast_path_reason = await classify_memory_merge_mode(operations, schema=schema)
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
    proposals = await build_memory_merge_proposals(
        operations=operations,
        delete_files=delete_files,
        schema=schema,
        extract_context=extract_context,
    )
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
    provider = PatchMergeContextProvider(
        memory_type=memory_type,
        required_file_uris=required_file_uris,
        patches=[proposal.patch for proposal in proposals],
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
    candidate_files_by_id = provider.candidate_files_by_id
    if memory_type == CASE_MEMORY_TYPE:
        candidate_files_by_id = {
            **build_case_target_candidate_files(operations),
            **candidate_files_by_id,
        }
    candidate_proposals = build_candidate_merge_proposals(candidate_files_by_id)
    all_proposals = {
        proposal.proposal_id: proposal for proposal in [*proposals, *candidate_proposals]
    }
    plan_model = create_memory_merge_plan_model(schema)
    plan_schema = json.dumps(plan_model.model_json_schema(), ensure_ascii=False)
    vlm = get_openviking_config().vlm.get_vlm_instance()
    tracer.info(
        "[streaming_memory_updater] llm merge input "
        f"memory_type={memory_type} required_file_count={len(required_file_uris)} "
        f"required_files={required_file_uris} patch_count={len(proposals)} "
        f"candidate_count={len(candidate_proposals)} "
        f"target_count={target_count}",
        console=trace_console,
    )
    merge_messages = [
        {
            "role": "system",
            "content": (
                f"{provider.instruction()}\n\n"
                "The required final-output schema is named MERGE_PLAN. "
                "Return an instance, not the schema definition.\n"
                f"{plan_schema}"
            ),
        },
        *prefetch_messages,
    ]
    response = await vlm.get_completion_async(
        messages=merge_messages,
        tools=None,
        thinking=False,
    )
    finish_reason = str(getattr(response, "finish_reason", "") or "").lower()
    if finish_reason in {"length", "max_tokens"} and memory_type == CASE_MEMORY_TYPE:
        tracer.info(
            "[streaming_memory_updater] retrying truncated Case merge with concise output",
            console=trace_console,
        )
        response = await vlm.get_completion_async(
            messages=[
                *merge_messages,
                {
                    "role": "user",
                    "content": (
                        "The previous MERGE_PLAN exceeded the output limit. Return the same "
                        "required JSON schema much more concisely. Keep every required "
                        "case_comparison, but do not explain labels. For a group that does not "
                        "require full Case compaction, use an empty field_operations object. "
                        "When compaction is required, keep each replacement field concise and "
                        "use at most three observable rubric criteria. Output JSON only."
                    ),
                },
            ],
            tools=None,
            thinking=False,
        )
        finish_reason = str(getattr(response, "finish_reason", "") or "").lower()
    if finish_reason in {"length", "max_tokens"}:
        raise MemoryMergePlanError(f"LLM merge output truncated: finish_reason={finish_reason}")

    def completion_content(value: Any) -> str:
        if value is None:
            return ""
        if isinstance(value, str):
            return value
        if getattr(value, "has_tool_calls", False):
            raise MemoryMergePlanError("LLM merge returned tool calls")
        return str(getattr(value, "content", "") or "")

    def parse_merge_plan(content: str) -> BaseModel:
        raw_plan, parse_error = parse_json_strict(content)
        if parse_error is not None:
            raise MemoryMergePlanParseError(parse_error)
        if memory_type == CASE_MEMORY_TYPE:
            raw_plan = sanitize_case_merge_plan_payload(raw_plan, schema=schema)
        try:
            plan = plan_model.model_validate(raw_plan, strict=True)
        except ValidationError as exc:
            raise MemoryMergePlanError(f"Invalid merge plan schema: {exc}") from exc
        if memory_type == CASE_MEMORY_TYPE:
            plan = normalize_case_merge_plan(
                plan,
                required_proposals=proposals,
                all_proposals=all_proposals,
            )
        return plan

    async def resolve_parsed_merge_plan(plan: BaseModel) -> ResolvedOperations:
        if memory_type == CASE_MEMORY_TYPE:
            validate_case_merge_plan(
                plan,
                required_proposals=proposals,
                all_proposals=all_proposals,
            )
        validate_memory_merge_plan(
            plan,
            required_proposals=proposals,
            all_proposals=all_proposals,
        )
        merged = await reconstruct_memory_operations_from_plan(
            plan,
            required_proposals=proposals,
            all_proposals=all_proposals,
            schema=schema,
        )
        if memory_type == CASE_MEMORY_TYPE:
            merged = finalize_case_merge_operations(
                merged,
                plan=plan,
                required_proposals=proposals,
                all_proposals=all_proposals,
            )
        return merged

    json_repair_attempted = False

    async def parse_merge_plan_with_json_repair(content: str) -> tuple[BaseModel, str]:
        nonlocal json_repair_attempted
        try:
            return parse_merge_plan(content), content
        except MemoryMergePlanParseError as exc:
            if json_repair_attempted:
                raise
            json_repair_attempted = True
            tracer.info(
                "[streaming_memory_updater] repairing malformed merge plan JSON",
                console=trace_console,
            )
            if memory_type == CASE_MEMORY_TYPE:
                repair_messages = [
                    *merge_messages,
                    {
                        "role": "user",
                        "content": (
                            "The previous MERGE_PLAN was malformed or incomplete. Regenerate "
                            "one complete, concise MERGE_PLAN from the original context. Keep "
                            "every required case_comparison, but do not explain labels. For a "
                            "group that does not require full Case compaction, use an empty "
                            "field_operations object. When compaction is required, keep each "
                            "replacement field concise and use at most three observable rubric "
                            "criteria. Output one complete JSON object only."
                        ),
                    },
                ]
            else:
                repair_messages = [
                    {
                        "role": "system",
                        "content": (
                            "Repair JSON syntax only. Preserve every key, value, array item, "
                            "and string from the malformed draft; change only punctuation, "
                            "delimiters, quoting, or escaping required to produce one complete "
                            "JSON object. Do not add, remove, summarize, or reinterpret semantic "
                            "content. Return JSON only. The repaired object must conform to this "
                            f"MERGE_PLAN schema:\n{plan_schema}"
                        ),
                    },
                    {"role": "assistant", "content": content},
                    {
                        "role": "user",
                        "content": (
                            f"The strict JSON parser reported: {exc}. "
                            "Return the complete syntax-repaired JSON object only."
                        ),
                    },
                ]
            repair_response = await vlm.get_completion_async(
                messages=repair_messages,
                tools=None,
                thinking=False,
            )
            repair_finish_reason = str(getattr(repair_response, "finish_reason", "") or "").lower()
            if repair_finish_reason in {"length", "max_tokens"}:
                raise MemoryMergePlanError(
                    f"LLM merge JSON repair output truncated: finish_reason={repair_finish_reason}"
                )
            repaired_content = completion_content(repair_response)
            try:
                repaired_plan = parse_merge_plan(repaired_content)
            except MemoryMergePlanParseError as repair_exc:
                raise MemoryMergePlanError(
                    f"LLM merge JSON repair failed: {repair_exc}"
                ) from repair_exc
            return repaired_plan, repaired_content

    content = completion_content(response)
    plan, content = await parse_merge_plan_with_json_repair(content)
    try:
        merged = await resolve_parsed_merge_plan(plan)
    except MemoryMergePlanError as exc:
        if memory_type == CASE_MEMORY_TYPE and str(exc).startswith(
            "Case comparison coverage mismatch:"
        ):
            missing_pairs = missing_case_comparison_pairs(
                plan,
                required_proposals=proposals,
                all_proposals=all_proposals,
            )
            tracer.info(
                "[streaming_memory_updater] repairing missing Case comparisons "
                f"pair_count={len(missing_pairs)}",
                console=trace_console,
            )
            repaired_comparisons = await repair_missing_case_comparisons(
                vlm=vlm,
                missing_pairs=missing_pairs,
                all_proposals=all_proposals,
                completion_content=completion_content,
            )
            plan = plan.model_copy(
                update={
                    "case_comparisons": [
                        *list(getattr(plan, "case_comparisons", []) or []),
                        *repaired_comparisons,
                    ]
                }
            )
            try:
                merged = await resolve_parsed_merge_plan(plan)
            except MemoryMergePlanError as repaired_exc:
                exc = repaired_exc
            else:
                exc = None
        if exc is None:
            pass
        else:
            retryable_case_errors = (
                "Case groups do not match deterministic assignment:",
                "Case compaction requires complete replacement fields:",
                "Draft Case promotion requires generalized_case_identity",
                "Draft Case promotion generalization failed:",
            )
            if memory_type != CASE_MEMORY_TYPE or not str(exc).startswith(retryable_case_errors):
                raise
            tracer.info(
                "[streaming_memory_updater] retrying Case merge after semantic validation "
                f"error={exc}",
                console=trace_console,
            )
            correction_prompt = (
                "The previous MERGE_PLAN failed server validation: "
                f"{exc}. Return one corrected complete MERGE_PLAN JSON object. "
                "Preserve valid case_comparisons and use the exact deterministic group "
                "assignment stated by the server error. For every group that requires "
                "compaction, replace task_signature, input, situation, rubric, and "
                "evidence together. When promoting a draft, also provide a "
                "generalized_case_identity derived from the semantic intersection of all "
                "independent sources. Remove exact one-run values. Output JSON only."
            )
            response = await vlm.get_completion_async(
                messages=[
                    *merge_messages,
                    {"role": "assistant", "content": content},
                    {"role": "user", "content": correction_prompt},
                ],
                tools=None,
                thinking=False,
            )
            retry_finish_reason = str(getattr(response, "finish_reason", "") or "").lower()
            if retry_finish_reason in {"length", "max_tokens"}:
                tracer.info(
                    "[streaming_memory_updater] retrying truncated corrected Case merge "
                    "with ultra-concise output",
                    console=trace_console,
                )
                response = await vlm.get_completion_async(
                    messages=[
                        *merge_messages,
                        {
                            "role": "user",
                            "content": (
                                f"{correction_prompt} The corrected output was truncated. "
                                "Recreate it without quoting the previous output. Use no "
                                "explanations. Limit task_signature to 40 words, situation to "
                                "45 words, evidence to 60 words, and each rubric criterion "
                                "description to 20 words; use at most three rubric criteria. "
                                "Keep compact JSON strings on one line."
                            ),
                        },
                    ],
                    tools=None,
                    thinking=False,
                )
                retry_finish_reason = str(getattr(response, "finish_reason", "") or "").lower()
            if retry_finish_reason in {"length", "max_tokens"}:
                raise MemoryMergePlanError(
                    "LLM corrected Case merge output truncated after concise retry: "
                    f"finish_reason={retry_finish_reason}"
                )
            corrected_content = completion_content(response)
            corrected_plan, _ = await parse_merge_plan_with_json_repair(corrected_content)
            merged = await resolve_parsed_merge_plan(corrected_plan)
    tracer.info(
        "[streaming_memory_updater] llm merge output "
        f"memory_type={memory_type} upserts={len(merged.upsert_operations)} "
        f"deletes={len(merged.delete_file_contents)} errors={len(merged.errors)}",
        console=trace_console,
    )
    return merged


def expected_case_comparison_pairs(
    *,
    required_proposals: list[MemoryMergeProposal],
    all_proposals: dict[str, MemoryMergeProposal],
) -> set[tuple[str, str]]:
    required_upserts = [
        proposal for proposal in required_proposals if proposal.operation is not None
    ]
    candidate_ids = sorted(
        proposal_id for proposal_id, proposal in all_proposals.items() if proposal.is_candidate
    )
    expected_pairs: set[tuple[str, str]] = set()
    previous_proposal_ids: list[str] = []
    for proposal in required_upserts:
        for candidate_id in [*candidate_ids, *previous_proposal_ids]:
            if candidate_id != proposal.proposal_id:
                expected_pairs.add((proposal.proposal_id, candidate_id))
        previous_proposal_ids.append(proposal.proposal_id)
    return expected_pairs


def normalized_case_comparison_map(
    comparisons: list[CaseIdentityComparison],
    *,
    expected_pairs: set[tuple[str, str]],
) -> dict[tuple[str, str], CaseIdentityComparison]:
    comparison_by_pair: dict[tuple[str, str], CaseIdentityComparison] = {}
    for comparison in comparisons:
        pair = (comparison.proposal_id, comparison.candidate_id)
        reversed_pair = (comparison.candidate_id, comparison.proposal_id)
        if pair not in expected_pairs and reversed_pair in expected_pairs:
            pair = reversed_pair
            comparison = comparison.model_copy(
                update={
                    "proposal_id": pair[0],
                    "candidate_id": pair[1],
                }
            )
        elif pair not in expected_pairs:
            continue
        if pair in comparison_by_pair:
            raise MemoryMergePlanError(f"Duplicate Case comparison: {pair}")
        comparison_by_pair[pair] = comparison
    return comparison_by_pair


def missing_case_comparison_pairs(
    plan: BaseModel,
    *,
    required_proposals: list[MemoryMergeProposal],
    all_proposals: dict[str, MemoryMergeProposal],
) -> list[tuple[str, str]]:
    expected_pairs = expected_case_comparison_pairs(
        required_proposals=required_proposals,
        all_proposals=all_proposals,
    )
    comparison_by_pair = normalized_case_comparison_map(
        list(getattr(plan, "case_comparisons", []) or []),
        expected_pairs=expected_pairs,
    )
    return sorted(expected_pairs - set(comparison_by_pair))


async def repair_missing_case_comparisons(
    *,
    vlm: Any,
    missing_pairs: list[tuple[str, str]],
    all_proposals: dict[str, MemoryMergeProposal],
    completion_content: Any,
    max_attempts: int = 3,
) -> list[CaseIdentityComparison]:
    if not missing_pairs:
        return []
    expected_pairs = set(missing_pairs)
    remaining_pairs = list(missing_pairs)
    comparison_by_pair: dict[tuple[str, str], CaseIdentityComparison] = {}
    proposal_ids = sorted({proposal_id for pair in missing_pairs for proposal_id in pair})
    identities = {
        proposal_id: _compact_case_proposal_context(all_proposals[proposal_id])
        for proposal_id in proposal_ids
    }
    last_error = ""
    for _attempt in range(max(1, max_attempts)):
        response = await vlm.get_completion_async(
            messages=[
                {
                    "role": "system",
                    "content": (
                        "Classify only the requested Case identity pairs. For each pair, label "
                        "goal, subject, action_pattern, success_boundary, and "
                        "context_constraints as MATCH, COMPATIBLE, UNKNOWN, or CONFLICT. "
                        'Return JSON only with shape {"case_comparisons":[...]}; include '
                        "every requested pair exactly once and no other pairs."
                    ),
                },
                {
                    "role": "user",
                    "content": json.dumps(
                        {
                            "pairs": [
                                {"proposal_id": left, "candidate_id": right}
                                for left, right in remaining_pairs
                            ],
                            "identities": identities,
                        },
                        ensure_ascii=False,
                        separators=(",", ":"),
                    ),
                },
            ],
            tools=None,
            thinking=False,
        )
        content = completion_content(response)
        payload, parse_error = parse_json_strict(content)
        if parse_error is not None:
            last_error = f"Invalid Case comparison repair JSON: {parse_error}"
            continue
        try:
            repaired = _CaseComparisonRepairResponse.model_validate(payload, strict=True)
        except ValidationError as exc:
            last_error = f"Invalid Case comparison repair schema: {exc}"
            continue
        accepted = normalized_case_comparison_map(
            repaired.case_comparisons,
            expected_pairs=set(remaining_pairs),
        )
        comparison_by_pair.update(accepted)
        remaining_pairs = sorted(expected_pairs - set(comparison_by_pair))
        if not remaining_pairs:
            break
        last_error = f"Case comparison repair coverage mismatch: missing={remaining_pairs}"
    if remaining_pairs:
        raise MemoryMergePlanError(last_error)
    return [comparison_by_pair[pair] for pair in missing_pairs]


def _compact_case_proposal_context(proposal: MemoryMergeProposal) -> dict[str, Any]:
    if proposal.operation is not None:
        fields = dict(proposal.operation.memory_fields or {})
        identity = (
            parse_case_identity(fields.get(PROPOSED_CASE_IDENTITY_FIELD))
            or parse_case_identity(fields.get(CASE_IDENTITY_FIELD))
            or fallback_case_identity(fields)
        )
    else:
        memory_file = proposal.patch.before_file or proposal.patch.after_file
        fields = dict(memory_file.extra_fields or {})
        # Stored candidates only have a canonical identity. Ignore legacy
        # operation-scoped proposals that may have leaked into older files.
        identity = parse_case_identity(fields.get(CASE_IDENTITY_FIELD)) or fallback_case_identity(
            fields
        )
    return {
        "case_name": str(fields.get("case_name") or ""),
        "case_identity": identity.model_dump(mode="json"),
        "task_signature": str(fields.get("task_signature") or "")[:500],
    }


async def build_memory_merge_proposals(
    *,
    operations: list[ResolvedOperation],
    delete_files: list[MemoryFile],
    schema: MemoryTypeSchema,
    extract_context: ExtractContext,
) -> list[MemoryMergeProposal]:
    proposals: list[MemoryMergeProposal] = []
    for index, operation in enumerate(operations):
        proposal_id = _operation_proposal_id(operation, index)
        patch = await operation_to_patch(
            operation,
            schema=schema,
            extract_context=extract_context,
        )
        patch.proposal_id = proposal_id
        proposals.append(
            MemoryMergeProposal(
                proposal_id=proposal_id,
                patch=patch,
                operation=operation,
            )
        )
    offset = len(operations)
    for index, memory_file in enumerate(delete_files, start=offset):
        proposal_id = _memory_file_proposal_id(memory_file, index)
        patch = memory_file_to_delete_patch(
            memory_file,
            schema=schema,
            extract_context=extract_context,
        )
        patch.proposal_id = proposal_id
        proposals.append(
            MemoryMergeProposal(
                proposal_id=proposal_id,
                patch=patch,
                delete_file=memory_file,
            )
        )
    proposal_ids = [proposal.proposal_id for proposal in proposals]
    if len(set(proposal_ids)) != len(proposal_ids):
        raise MemoryMergePlanError(f"Duplicate generated proposal_id: {proposal_ids}")
    return proposals


def build_candidate_merge_proposals(
    candidate_files_by_id: dict[str, MemoryFile],
) -> list[MemoryMergeProposal]:
    return [
        MemoryMergeProposal(
            proposal_id=candidate_id,
            patch=PatchMergePatch(
                before_file=memory_file,
                after_file=memory_file.model_copy(deep=True),
                proposal_id=candidate_id,
            ),
            is_candidate=True,
        )
        for candidate_id, memory_file in candidate_files_by_id.items()
    ]


def build_case_target_candidate_files(
    operations: list[ResolvedOperation],
) -> dict[str, MemoryFile]:
    """Expose directly targeted stored Cases as explicit identity candidates."""

    result: dict[str, MemoryFile] = {}
    for operation in operations:
        old_file = operation.old_memory_file_content
        if old_file is None or not old_file.uri:
            continue
        result[candidate_id_for_uri(old_file.uri)] = old_file
    return result


def create_memory_merge_plan_model(schema: MemoryTypeSchema) -> type[BaseModel]:
    field_definitions: dict[str, tuple[Any, Any]] = {}
    for memory_field in schema.fields:
        if memory_field.merge_op == MergeOp.IMMUTABLE or memory_field.system_managed:
            continue
        base_type = get_python_type_for_field(memory_field.field_type)
        patch_type = MergeOpFactory.from_field(memory_field).get_output_schema_type(
            memory_field.field_type
        )
        output_type = Union[base_type, patch_type]
        field_definitions[memory_field.name] = (
            Optional[output_type],
            Field(
                default=None,
                description=(
                    f"Optional {memory_field.merge_op.value} payload for "
                    f"{memory_field.name}: {memory_field.description}"
                ),
            ),
        )

    model_prefix = re.sub(r"[^A-Za-z0-9]+", "_", schema.memory_type).title()
    field_operations_model = create_model(
        f"{model_prefix}MergeFieldOperations",
        __base__=_MergePlanFieldOperationsBase,
        **field_definitions,
    )
    group_fields: dict[str, tuple[Any, Any]] = {
        "proposal_ids": (
            list[str],
            Field(
                ...,
                min_length=1,
                description=(
                    "Input proposal_ids in this semantic group. Optional candidate_ids "
                    "may also be included."
                ),
            ),
        ),
        "canonical_proposal_id": (
            str,
            Field(
                ...,
                description=(
                    "Surviving proposal_id or candidate_id; it must also occur in proposal_ids."
                ),
            ),
        ),
        "field_operations": (
            field_operations_model,
            Field(...),
        ),
    }
    if schema.memory_type == CASE_MEMORY_TYPE:
        group_fields["generalized_case_identity"] = (
            Optional[CaseIdentity],
            Field(
                default=None,
                description=(
                    "Replacement Case identity used only when a draft Case, either stored or "
                    "formed in the current batch, is compacted from at least two independent "
                    "sources and promoted. Derive the semantic intersection of the source "
                    "identities, generalize one-run values into input.variable_types, and omit "
                    "exact IDs, names, dates, amounts, percentages, counts, paths, and filenames."
                ),
            ),
        )
    group_model = create_model(
        f"{model_prefix}MergePlanGroup",
        __base__=_MergePlanGroupBase,
        **group_fields,
    )
    plan_fields: dict[str, tuple[Any, Any]] = {
        "groups": (list[group_model], Field(...)),
        "delete_proposal_ids": (
            list[str],
            Field(
                ...,
                description="Explicit deletion proposal_ids that should remain deletes.",
            ),
        ),
    }
    if schema.memory_type == CASE_MEMORY_TYPE:
        plan_fields["case_comparisons"] = (
            list[CaseIdentityComparison],
            Field(
                ...,
                description=(
                    "Five-dimensional identity classifications for every current Case proposal "
                    "against every listed candidate available to it."
                ),
            ),
        )
    return create_model(
        f"{model_prefix}MergePlan",
        __base__=_MergePlanBase,
        **plan_fields,
    )


def sanitize_case_merge_plan_payload(
    payload: Any,
    *,
    schema: MemoryTypeSchema,
) -> Any:
    """Remove model-authored Case fields that are not writable merge fields."""

    if not isinstance(payload, dict):
        return payload
    allowed_fields = {
        field.name
        for field in schema.fields
        if field.merge_op != MergeOp.IMMUTABLE and not field.system_managed
    }
    groups = payload.get("groups")
    if not isinstance(groups, list):
        return payload
    sanitized = dict(payload)
    sanitized_groups: list[Any] = []
    for group in groups:
        if not isinstance(group, dict):
            sanitized_groups.append(group)
            continue
        proposal_ids = group.get("proposal_ids")
        if isinstance(proposal_ids, list) and not proposal_ids:
            continue
        sanitized_group = dict(group)
        field_operations = group.get("field_operations")
        if isinstance(field_operations, dict):
            sanitized_group["field_operations"] = {
                name: value for name, value in field_operations.items() if name in allowed_fields
            }
        sanitized_groups.append(sanitized_group)
    sanitized["groups"] = sanitized_groups
    return sanitized


def normalize_case_merge_plan(
    plan: BaseModel,
    *,
    required_proposals: list[MemoryMergeProposal],
    all_proposals: dict[str, MemoryMergeProposal],
) -> BaseModel:
    """Drop read-only groups and make canonical selection deterministic."""

    required_ids = {
        proposal.proposal_id for proposal in required_proposals if proposal.operation is not None
    }
    required_order = {
        proposal.proposal_id: index
        for index, proposal in enumerate(required_proposals)
        if proposal.operation is not None
    }
    groups = list(getattr(plan, "groups", []) or [])
    normalized_groups = [group for group in groups if required_ids & set(group.proposal_ids)]
    canonicalized_groups = []
    for group in normalized_groups:
        member_ids = set(group.proposal_ids)
        stored_candidates = sorted(
            proposal_id
            for proposal_id in member_ids
            if proposal_id in all_proposals and all_proposals[proposal_id].is_candidate
        )
        required_members = sorted(
            member_ids & required_ids,
            key=lambda proposal_id: required_order[proposal_id],
        )
        canonical_id = (
            stored_candidates[0]
            if len(stored_candidates) == 1
            else required_members[0]
            if not stored_candidates and required_members
            else group.canonical_proposal_id
        )
        canonicalized_groups.append(
            group
            if canonical_id == group.canonical_proposal_id
            else group.model_copy(update={"canonical_proposal_id": canonical_id})
        )
    return plan.model_copy(update={"groups": canonicalized_groups})


def validate_case_merge_plan(
    plan: BaseModel,
    *,
    required_proposals: list[MemoryMergeProposal],
    all_proposals: dict[str, MemoryMergeProposal],
) -> None:
    """Validate Case grouping against deterministic identity scoring."""

    required_upserts = [
        proposal for proposal in required_proposals if proposal.operation is not None
    ]
    candidate_ids = sorted(
        proposal_id for proposal_id, proposal in all_proposals.items() if proposal.is_candidate
    )
    comparisons = list(getattr(plan, "case_comparisons", []) or [])
    expected_pairs = expected_case_comparison_pairs(
        required_proposals=required_proposals,
        all_proposals=all_proposals,
    )
    comparison_by_pair = normalized_case_comparison_map(
        comparisons,
        expected_pairs=expected_pairs,
    )
    actual_pairs = set(comparison_by_pair)
    if actual_pairs != expected_pairs:
        missing = sorted(expected_pairs - actual_pairs)
        extra = sorted(actual_pairs - expected_pairs)
        raise MemoryMergePlanError(
            f"Case comparison coverage mismatch: missing={missing} extra={extra}"
        )

    parent: dict[str, str] = {
        proposal.proposal_id: proposal.proposal_id for proposal in required_upserts
    }
    selected_candidates: set[str] = set()

    def find(item: str) -> str:
        parent.setdefault(item, item)
        while parent[item] != item:
            parent[item] = parent[parent[item]]
            item = parent[item]
        return item

    def union(left: str, right: str) -> None:
        left_root = find(left)
        right_root = find(right)
        if left_root != right_root:
            parent[right_root] = left_root

    previous_proposal_ids = []
    for proposal in required_upserts:
        eligible_comparisons = [
            comparison_by_pair[(proposal.proposal_id, candidate_id)]
            for candidate_id in [*candidate_ids, *previous_proposal_ids]
            if (proposal.proposal_id, candidate_id) in comparison_by_pair
        ]
        primary_id, _ = select_case_primary(eligible_comparisons)
        if primary_id:
            union(proposal.proposal_id, primary_id)
            if primary_id in candidate_ids:
                selected_candidates.add(primary_id)
        previous_proposal_ids.append(proposal.proposal_id)

    components: dict[str, set[str]] = {}
    for proposal in required_upserts:
        components.setdefault(find(proposal.proposal_id), set()).add(proposal.proposal_id)
    for candidate_id in selected_candidates:
        components.setdefault(find(candidate_id), set()).add(candidate_id)

    required_order = {
        proposal.proposal_id: index for index, proposal in enumerate(required_upserts)
    }
    expected_groups: dict[frozenset[str], str] = {}
    for member_ids in components.values():
        stored_candidates = sorted(member_ids & set(candidate_ids))
        if len(stored_candidates) > 1:
            raise MemoryMergePlanError(
                f"Case assignment selected multiple stored primaries: {stored_candidates}"
            )
        canonical_id = (
            stored_candidates[0]
            if stored_candidates
            else min(member_ids, key=lambda item: required_order[item])
        )
        expected_groups[frozenset(member_ids)] = canonical_id

    actual_groups = {
        frozenset(group.proposal_ids): group.canonical_proposal_id
        for group in list(getattr(plan, "groups", []) or [])
    }
    if actual_groups != expected_groups:
        raise MemoryMergePlanError(
            f"Case groups do not match deterministic assignment: "
            f"expected={expected_groups} actual={actual_groups}"
        )


def validate_memory_merge_plan(
    plan: BaseModel,
    *,
    required_proposals: list[MemoryMergeProposal],
    all_proposals: dict[str, MemoryMergeProposal],
) -> None:
    required_by_id = {proposal.proposal_id: proposal for proposal in required_proposals}
    required_ids = set(required_by_id)
    known_ids = set(all_proposals)
    counts: dict[str, int] = {}

    groups = list(getattr(plan, "groups", []) or [])
    if any(proposal.operation is not None for proposal in required_proposals) and not groups:
        raise MemoryMergePlanError("Non-empty upsert input requires at least one merge group")

    for group_index, group in enumerate(groups):
        proposal_ids = list(group.proposal_ids)
        if len(set(proposal_ids)) != len(proposal_ids):
            raise MemoryMergePlanError(f"Merge group {group_index} contains duplicate proposal_ids")
        unknown_ids = set(proposal_ids) - known_ids
        if unknown_ids:
            raise MemoryMergePlanError(
                f"Merge group {group_index} contains unknown proposal_ids: {sorted(unknown_ids)}"
            )
        if group.canonical_proposal_id not in proposal_ids:
            raise MemoryMergePlanError(
                f"Merge group {group_index} canonical_proposal_id is not in proposal_ids"
            )
        canonical = all_proposals[group.canonical_proposal_id]
        if canonical.is_explicit_delete:
            raise MemoryMergePlanError(
                f"Merge group {group_index} uses a deletion proposal as canonical"
            )
        if not (set(proposal_ids) & required_ids):
            raise MemoryMergePlanError(
                f"Merge group {group_index} does not contain an input proposal"
            )
        for proposal_id in proposal_ids:
            counts[proposal_id] = counts.get(proposal_id, 0) + 1

    delete_proposal_ids = list(getattr(plan, "delete_proposal_ids", []) or [])
    if len(set(delete_proposal_ids)) != len(delete_proposal_ids):
        raise MemoryMergePlanError("delete_proposal_ids contains duplicates")
    for proposal_id in delete_proposal_ids:
        proposal = required_by_id.get(proposal_id)
        if proposal is None:
            raise MemoryMergePlanError(
                f"delete_proposal_ids contains unknown input proposal_id: {proposal_id}"
            )
        if not proposal.is_explicit_delete:
            raise MemoryMergePlanError(
                f"delete_proposal_ids contains non-deletion proposal_id: {proposal_id}"
            )
        counts[proposal_id] = counts.get(proposal_id, 0) + 1

    missing = sorted(proposal_id for proposal_id in required_ids if counts.get(proposal_id) != 1)
    if missing:
        raise MemoryMergePlanError(
            f"Every input proposal_id must appear exactly once; invalid ids: {missing}"
        )
    repeated_candidates = sorted(
        proposal_id
        for proposal_id, count in counts.items()
        if proposal_id not in required_ids and count != 1
    )
    if repeated_candidates:
        raise MemoryMergePlanError(f"Candidate ids may appear at most once: {repeated_candidates}")


def finalize_case_merge_operations(
    merged: ResolvedOperations,
    *,
    plan: BaseModel,
    required_proposals: list[MemoryMergeProposal],
    all_proposals: dict[str, MemoryMergeProposal],
) -> ResolvedOperations:
    """Apply Case source accounting, lifecycle state, and compaction policy."""

    groups = list(getattr(plan, "groups", []) or [])
    if len(groups) != len(merged.upsert_operations):
        raise MemoryMergePlanError("Case merge output/group count mismatch")

    for group, operation in zip(groups, merged.upsert_operations, strict=True):
        canonical = all_proposals[group.canonical_proposal_id]
        grouped = [all_proposals[proposal_id] for proposal_id in group.proposal_ids]
        grouped_operations = [
            proposal.operation for proposal in grouped if proposal.operation is not None
        ]
        existing_file = (
            canonical.patch.before_file
            if canonical.is_candidate
            else canonical.operation.old_memory_file_content
            if canonical.operation is not None
            else None
        )
        target_candidate_id = (
            candidate_id_for_uri(existing_file.uri)
            if existing_file is not None and existing_file.uri
            else None
        )
        split_from_target = (
            existing_file is not None
            and target_candidate_id is not None
            and target_candidate_id not in set(group.proposal_ids)
        )
        if split_from_target:
            original_uri = operation.uris[0] if operation.uris else None
            _retarget_case_variant(operation, canonical.proposal_id)
            if original_uri and operation.uris:
                merged.link_replacements[original_uri] = operation.uris[0]
            existing_file = None
            operation.old_memory_file_content = None

        source_ids, source_count = merged_case_source_state(
            grouped_operations=grouped_operations,
            existing_file=existing_file,
        )
        pending_sources = merged_case_pending_sources(
            grouped_operations=grouped_operations,
            existing_file=existing_file,
        )
        owned_source_ids = set(source_ids)
        pending_sources = [
            item for item in pending_sources if str(item.get("source_id") or "") in owned_source_ids
        ]
        existing_fields = dict(existing_file.extra_fields or {}) if existing_file else {}
        last_compacted_source_count = int(existing_fields.get("last_compacted_source_count") or 0)
        compact = should_compact_case(
            source_count=source_count,
            last_compacted_source_count=last_compacted_source_count,
            existing_file=existing_file,
        )
        field_operations = set(group.field_operations.model_fields_set)
        if compact:
            missing_dynamic_fields = sorted(
                field_name
                for field_name in CASE_DYNAMIC_FIELDS
                if field_name not in field_operations
                or getattr(group.field_operations, field_name, None) is None
            )
            if missing_dynamic_fields:
                raise MemoryMergePlanError(
                    "Case compaction requires complete replacement fields: "
                    f"{missing_dynamic_fields}"
                )

        fields = dict(operation.memory_fields or {})
        existing_status = normalize_case_status(existing_fields.get("case_status"))
        promoting_draft = compact and source_count >= 2 and existing_status == "draft"
        generalized_identity = parse_case_identity(
            getattr(group, "generalized_case_identity", None)
        )
        if promoting_draft:
            if generalized_identity is None:
                raise MemoryMergePlanError(
                    "Draft Case promotion requires generalized_case_identity"
                )
            generalized_identity, generalized_input = generalize_case_year_literals(
                generalized_identity,
                fields.get("input"),
            )
            if generalized_input is not None:
                fields["input"] = generalized_input
            identity_violations = case_identity_generalization_violations(generalized_identity)
            input_violations = case_input_generalization_violations(fields.get("input"))
            if identity_violations or input_violations:
                raise MemoryMergePlanError(
                    "Draft Case promotion generalization failed: "
                    + "; ".join([*identity_violations, *input_violations])
                )
            fields[CASE_IDENTITY_FIELD] = generalized_identity.compact_json()
        elif existing_file is not None:
            stored_identity = parse_case_identity(
                existing_fields.get(CASE_IDENTITY_FIELD)
            ) or fallback_case_identity(existing_fields)
            fields[CASE_IDENTITY_FIELD] = stored_identity.compact_json()
        else:
            proposed_identity = (
                parse_case_identity(fields.get(PROPOSED_CASE_IDENTITY_FIELD))
                or parse_case_identity(fields.get(CASE_IDENTITY_FIELD))
                or fallback_case_identity(fields)
            )
            fields[CASE_IDENTITY_FIELD] = proposed_identity.compact_json()

        if existing_file is not None and not compact:
            for field_name in CASE_DYNAMIC_FIELDS:
                fields.pop(field_name, None)

        fields.pop(PROPOSED_CASE_IDENTITY_FIELD, None)
        if source_ids:
            fields[CASE_SOURCE_IDS_FIELD] = source_ids
        fields[CASE_PENDING_SOURCES_FIELD] = [] if compact else pending_sources
        fields["source_count"] = source_count
        fields["case_status"] = "promoted" if promoting_draft else existing_status
        if compact:
            fields["last_compacted_source_count"] = source_count
            fields["last_compacted_version"] = int(existing_fields.get("version") or 0) + 1
        else:
            fields["last_compacted_source_count"] = last_compacted_source_count
            fields["last_compacted_version"] = int(
                existing_fields.get("last_compacted_version") or 0
            )
        operation.memory_fields = fields

    return merged


def _retarget_case_variant(operation: ResolvedOperation, proposal_id: str) -> None:
    fields = dict(operation.memory_fields or {})
    base_name = str(fields.get("case_name") or "case")
    suffix = hashlib.sha256(proposal_id.encode("utf-8")).hexdigest()[:6]
    variant_name = f"{base_name}_variant_{suffix}"
    fields["case_name"] = variant_name
    operation.memory_fields = fields
    if operation.uris:
        directory = operation.uris[0].rsplit("/", 1)[0]
        operation.uris = [f"{directory}/{variant_name}.md"]


async def reconstruct_memory_operations_from_plan(
    plan: BaseModel,
    *,
    required_proposals: list[MemoryMergeProposal],
    all_proposals: dict[str, MemoryMergeProposal],
    schema: MemoryTypeSchema,
) -> ResolvedOperations:
    upserts: list[ResolvedOperation] = []
    delete_by_uri: dict[str, MemoryFile] = {}
    delete_replacements: dict[str, str] = {}
    canonical_uris: set[str] = set()

    for group in list(getattr(plan, "groups", []) or []):
        canonical = all_proposals[group.canonical_proposal_id]
        field_operations = {
            field_name: getattr(group.field_operations, field_name)
            for field_name in group.field_operations.model_fields_set
        }
        resolved_operation = await _reconstruct_canonical_operation(
            canonical=canonical,
            grouped_proposals=[all_proposals[proposal_id] for proposal_id in group.proposal_ids],
            field_operations=field_operations,
            schema=schema,
        )
        canonical_uri = _first_uri(resolved_operation.uris)
        if not canonical_uri:
            raise MemoryMergePlanError(
                f"Canonical proposal has no URI: {group.canonical_proposal_id}"
            )
        if canonical_uri in canonical_uris:
            raise MemoryMergePlanError(f"Duplicate canonical URI in merge plan: {canonical_uri}")
        canonical_uris.add(canonical_uri)
        upserts.append(resolved_operation)

        for proposal_id in group.proposal_ids:
            if proposal_id == group.canonical_proposal_id:
                continue
            loser = all_proposals[proposal_id]
            existing_file = loser.patch.before_file
            loser_uri = existing_file.uri if existing_file is not None else None
            if not loser_uri or loser_uri == canonical_uri:
                continue
            delete_by_uri[loser_uri] = existing_file
            delete_replacements[loser_uri] = canonical_uri

    required_by_id = {proposal.proposal_id: proposal for proposal in required_proposals}
    for proposal_id in list(getattr(plan, "delete_proposal_ids", []) or []):
        proposal = required_by_id[proposal_id]
        if proposal.delete_file is None or not proposal.delete_file.uri:
            raise MemoryMergePlanError(f"Deletion proposal has no existing file: {proposal_id}")
        delete_by_uri[proposal.delete_file.uri] = proposal.delete_file

    for canonical_uri in canonical_uris:
        delete_by_uri.pop(canonical_uri, None)
        delete_replacements.pop(canonical_uri, None)

    return ResolvedOperations(
        upsert_operations=upserts,
        delete_file_contents=list(delete_by_uri.values()),
        errors=[],
        resolved_links=[],
        delete_replacements=delete_replacements,
    )


async def _reconstruct_canonical_operation(
    *,
    canonical: MemoryMergeProposal,
    grouped_proposals: list[MemoryMergeProposal],
    field_operations: dict[str, Any],
    schema: MemoryTypeSchema,
) -> ResolvedOperation:
    base_file = canonical.patch.after_file.model_copy(deep=True)
    final_fields = dict(base_file.extra_fields or {})
    schema_by_name = {memory_field.name: memory_field for memory_field in schema.fields}
    if "content" in schema_by_name:
        final_fields["content"] = base_file.plain_content()

    for field_name, patch_value in field_operations.items():
        memory_field = schema_by_name.get(field_name)
        if memory_field is None:
            raise MemoryMergePlanError(f"Unknown field operation: {field_name}")
        current_value = final_fields.get(field_name)
        try:
            final_fields[field_name] = await MergeOpFactory.from_field(memory_field).apply(
                current_value,
                patch_value,
            )
        except Exception as exc:
            raise MemoryMergePlanError(
                f"Failed to apply merge_op for field {field_name}: {exc}"
            ) from exc

    old_file = (
        canonical.patch.before_file
        if canonical.patch.before_file is not None
        else (base_file if canonical.is_candidate else None)
    )
    operation_fields: dict[str, Any] = {
        key: value
        for key, value in final_fields.items()
        if key != "version" and key not in schema_by_name
    }
    for memory_field in schema.fields:
        if memory_field.name not in final_fields:
            continue
        final_value = final_fields[memory_field.name]
        current_value = _memory_file_field_value(old_file, memory_field.name)
        if (
            memory_field.merge_op == MergeOp.SUM
            and current_value is not None
            and final_value is not None
        ):
            try:
                operation_fields[memory_field.name] = final_value - current_value
            except TypeError as exc:
                raise MemoryMergePlanError(
                    f"Cannot reconstruct sum merge_op for field {memory_field.name}"
                ) from exc
        else:
            operation_fields[memory_field.name] = final_value

    source_ids = sorted(
        {
            source_id
            for proposal in grouped_proposals
            if proposal.operation is not None
            for source_id in _operation_source_extraction_ids(proposal.operation)
        }
    )
    operation_fields.pop("source_extraction_id", None)
    operation_fields.pop("source_extraction_ids", None)
    if len(source_ids) == 1:
        operation_fields["source_extraction_id"] = source_ids[0]
    elif source_ids:
        operation_fields["source_extraction_ids"] = source_ids

    source = canonical.operation.source if canonical.operation is not None else None
    if source is None:
        source = next(
            (
                proposal.operation.source
                for proposal in grouped_proposals
                if proposal.operation is not None and proposal.operation.source is not None
            ),
            None,
        )
    uri = base_file.uri or canonical.patch.target_uri
    if not uri:
        raise MemoryMergePlanError(f"Canonical proposal has no target URI: {canonical.proposal_id}")
    return ResolvedOperation(
        old_memory_file_content=old_file,
        memory_fields=operation_fields,
        memory_type=schema.memory_type,
        uris=[uri],
        source=source,
    )


def _memory_file_field_value(memory_file: MemoryFile | None, field_name: str) -> Any:
    if memory_file is None:
        return None
    if field_name == "content":
        return memory_file.plain_content()
    return dict(memory_file.extra_fields or {}).get(field_name)


def _operation_proposal_id(operation: ResolvedOperation, index: int) -> str:
    source_id = source_extraction_id_for_operation(operation) or "batch"
    return f"{source_id}:{index}"


def _memory_file_proposal_id(memory_file: MemoryFile, index: int) -> str:
    fields = dict(memory_file.extra_fields or {})
    source_id = fields.get("source_extraction_id") or "batch"
    return f"{source_id}:delete:{index}"


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
    in delete_proposal_ids.
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


async def operation_to_patch(
    op: ResolvedOperation,
    *,
    schema: MemoryTypeSchema,
    extract_context: ExtractContext,
) -> PatchMergePatch:
    old_file = getattr(op, "old_memory_file_content", None)
    after_file = await render_operation_after_file(
        op,
        schema=schema,
        extract_context=extract_context,
    )
    return PatchMergePatch(
        before_file=old_file,
        after_file=after_file,
    )


async def classify_memory_merge_mode(
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
    if schema is not None and schema.memory_type == CASE_MEMORY_TYPE:
        return False, "case_identity_assignment"
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
            after_content = await render_operation_after_file_content(
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


def _is_case_experience_link(link: StoredLink) -> bool:
    return "/memories/cases/" in str(link.from_uri or "") and "/memories/experiences/" in str(
        link.to_uri or ""
    )


def _remap_allocated_links_once(
    links: list[StoredLink], uri_remap: dict[str, str]
) -> list[StoredLink]:
    """Allocation renames are simultaneous, not transitive logical aliases.

    If A -> A_2 and A_2 -> A_3, a link to the first proposal must end at A_2,
    not follow the second proposal's rename to A_3.
    """
    return merge_link_lists(
        [
            link.model_copy(
                update={
                    "from_uri": uri_remap.get(link.from_uri, link.from_uri),
                    "to_uri": uri_remap.get(link.to_uri, link.to_uri),
                }
            )
            for link in links
        ]
    )


def _extract_case_experience_links(operations: ResolvedOperations) -> list[StoredLink]:
    """Collect deferred links and remove copies embedded in incoming fields."""
    links = [link for link in operations.resolved_links if _is_case_experience_link(link)]
    for operation in operations.upsert_operations:
        for field_name in ("links", "backlinks"):
            values = operation.memory_fields.get(field_name)
            if not isinstance(values, list):
                continue
            retained = []
            for value in values:
                try:
                    link = StoredLink.model_validate(value)
                except (ValueError, TypeError):
                    retained.append(value)
                    continue
                if _is_case_experience_link(link):
                    links.append(link)
                else:
                    retained.append(value)
            operation.memory_fields[field_name] = retained
    # Include embedded links in the pre-apply lock coverage as well.
    operations.resolved_links = merge_link_lists(operations.resolved_links, links)
    return merge_link_lists(links)


async def filter_valid_links(
    links: list[StoredLink],
    *,
    upsert_operations: list[ResolvedOperation],
    delete_file_contents: list[MemoryFile],
    ctx: RequestContext,
    trace_console: bool = False,
    defer_case_experience_links: bool = False,
) -> list[StoredLink]:
    """Check endpoints, using pending upserts only for pre-apply validation.

    Mutation callers preserve Case/EXP candidates for post-apply validation.
    Post-apply callers must pass no upserts so existence and EXP visibility come
    from storage, not a stale operation preview.
    """

    if not links:
        return []
    upsert_by_uri = {uri: op for op in upsert_operations for uri in (op.uris or []) if uri}
    upsert_uris = set(upsert_by_uri)
    deleted_uris = {file.uri for file in delete_file_contents if getattr(file, "uri", None)}
    viking_fs = safe_get_viking_fs()
    endpoint_content_cache: dict[str, str | None] = {}

    async def _endpoint_exists(uri: str) -> bool:
        if not uri or uri in deleted_uris:
            return False
        if uri in upsert_uris:
            return True
        if uri in endpoint_content_cache:
            return endpoint_content_cache[uri] is not None
        if viking_fs is None:
            endpoint_content_cache[uri] = None
            return False
        try:
            content = await viking_fs.read_file(uri, ctx=ctx)
            endpoint_content_cache[uri] = content if content else None
        except Exception:
            endpoint_content_cache[uri] = None
        return endpoint_content_cache[uri] is not None

    async def _case_experience_link_is_hidden(link: StoredLink) -> bool:
        if not _is_case_experience_link(link):
            return False
        operation = upsert_by_uri.get(link.to_uri)
        if operation is not None:
            if operation.lifecycle_action == "archive":
                return True
            fields = dict(getattr(operation.old_memory_file_content, "extra_fields", {}) or {})
            fields.update(operation.memory_fields)
            return not experience_is_case_linkable(fields.get("status"))
        if not await _endpoint_exists(link.to_uri):
            return False
        raw = endpoint_content_cache.get(link.to_uri)
        try:
            memory_file = MemoryFileUtils.read(raw or "", uri=link.to_uri)
        except Exception:
            return True
        return not experience_is_case_linkable(memory_file.extra_fields.get("status"))

    valid_links: list[StoredLink] = []
    dropped = 0
    for link in merge_link_lists(links):
        if defer_case_experience_links and _is_case_experience_link(link):
            valid_links.append(link)
            continue
        if (
            await _endpoint_exists(link.from_uri)
            and await _endpoint_exists(link.to_uri)
            and not await _case_experience_link_is_hidden(link)
        ):
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
        scope=scope,
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
        link_replacements={
            str(original_uri): str(replacement_uri)
            for original_uri, replacement_uri in dict(
                getattr(operations, "link_replacements", {}) or {}
            ).items()
            if (
                str(original_uri) in kept_uris
                or str(replacement_uri) in kept_uris
                or str(original_uri) in request_uris
            )
        },
    )


def _scope_apply_result_to_uris(
    apply_result: MemoryUpdateResult,
    *,
    scoped_uris: set[str],
    scope: _MemorySubmitterScope,
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
    scoped.skipped_operations = [
        operation
        for operation in list(getattr(apply_result, "skipped_operations", []) or [])
        if _skipped_operation_matches_scope(
            operation,
            scope=scope,
            scoped_uris=scoped_uris,
        )
    ]
    return scoped


def _skipped_operation_matches_scope(
    operation: SkippedMemoryOperation,
    *,
    scope: _MemorySubmitterScope,
    scoped_uris: set[str],
) -> bool:
    source = getattr(operation, "source", None)
    source_extraction_id = _optional_str(getattr(source, "extraction_id", None))
    if scope.extraction_id and source_extraction_id:
        return source_extraction_id == scope.extraction_id

    source_archive_uri = _optional_str(getattr(source, "archive_uri", None))
    if scope.archive_uri and source_archive_uri:
        return source_archive_uri == scope.archive_uri

    source_session_id = _optional_str(getattr(source, "session_id", None))
    if scope.session_id and source_session_id:
        return source_session_id == scope.session_id

    uri = str(getattr(operation, "uri", None) or "")
    return bool(uri and uri in scoped_uris)


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
        link_replacements={},
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
        combined_operations.link_replacements.update(
            dict(getattr(result.operations, "link_replacements", {}) or {})
        )
        combined_apply_result.written_uris.extend(result.apply_result.written_uris)
        combined_apply_result.edited_uris.extend(result.apply_result.edited_uris)
        combined_apply_result.deleted_uris.extend(result.apply_result.deleted_uris)
        combined_apply_result.skipped_operations.extend(result.apply_result.skipped_operations)
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


def _combine_resolved_operations(
    items: Iterable[ResolvedOperations],
) -> ResolvedOperations:
    combined = ResolvedOperations(
        upsert_operations=[],
        delete_file_contents=[],
        errors=[],
        resolved_links=[],
        delete_replacements={},
        link_replacements={},
    )
    for operations in items:
        combined.upsert_operations.extend(list(operations.upsert_operations or []))
        combined.delete_file_contents.extend(list(operations.delete_file_contents or []))
        combined.errors.extend(list(operations.errors or []))
        combined.resolved_links = merge_link_lists(
            combined.resolved_links,
            list(getattr(operations, "resolved_links", []) or []),
        )
        combined.delete_replacements.update(
            dict(getattr(operations, "delete_replacements", {}) or {})
        )
        combined.link_replacements.update(dict(getattr(operations, "link_replacements", {}) or {}))
    return combined


def _requests_span_sessions(items: list[MemoryUpdateRequest]) -> bool:
    """Return whether a kind batch cannot be proven to come from one session."""

    if len(items) < 2:
        return False
    session_ids: set[str] = set()
    for request in items:
        session_id = str((request.metadata or {}).get("session_id") or "").strip()
        if not session_id:
            return True
        session_ids.add(session_id)
    return len(session_ids) > 1


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
        peer_memory_enabled=options.get("peer_memory_enabled"),
    )


def _operation_count(operations: ResolvedOperations) -> int:
    return len(operations.upsert_operations or []) + len(operations.delete_file_contents or [])


def _skipped_reason_codes(result: MemoryUpdateResult) -> list[str]:
    return [
        operation.reason_code.value
        for operation in list(getattr(result, "skipped_operations", []) or [])
    ]


def _operation_lock_paths(
    operations: ResolvedOperations,
    viking_fs: Any | None,
    ctx: RequestContext,
) -> list[str]:
    operation_uris = _operation_uri_set(operations)
    uris = set(operation_uris)
    for uri in operation_uris:
        normalized_uri = str(uri).rstrip("/")
        directory, separator, _ = normalized_uri.rpartition("/")
        if separator and directory:
            uris.add(f"{directory}/.overview.md")
    uris.update(_link_endpoint_uri_set(list(operations.resolved_links or [])))
    for deleted_uri, replacement_uri in dict(operations.delete_replacements or {}).items():
        if deleted_uri:
            uris.add(str(deleted_uri))
        if replacement_uri:
            uris.add(str(replacement_uri))
    for operation in operations.upsert_operations:
        if operation.lifecycle_action != "archive":
            continue
        for experience_uri in operation.uris:
            old_file = operation.old_memory_file_content
            if old_file is not None:
                uris.update(
                    experience_case_link_uris(
                        old_file.backlinks,
                        experience_uri=experience_uri,
                    )
                )
                archived_case_uris = old_file.extra_fields.get("archived_case_uris", [])
                if isinstance(archived_case_uris, (list, tuple, set)):
                    uris.update(str(case_uri) for case_uri in archived_case_uris if case_uri)
            if operation.archive_replacement_uri:
                uris.add(operation.archive_replacement_uri)
    for memory_file in operations.delete_file_contents or []:
        for link in list(memory_file.links or []) + list(memory_file.backlinks or []):
            if isinstance(link, dict):
                from_uri = link.get("from_uri")
                to_uri = link.get("to_uri")
            else:
                from_uri = getattr(link, "from_uri", None)
                to_uri = getattr(link, "to_uri", None)
            if from_uri:
                uris.add(str(from_uri))
            if to_uri:
                uris.add(str(to_uri))
    return _uri_lock_paths(uris, viking_fs, ctx)


async def _persisted_operation_relation_uris(
    operations: ResolvedOperations,
    viking_fs: Any,
    ctx: RequestContext,
) -> set[str]:
    uris: set[str] = set()
    archive_operations_by_uri = {
        uri: operation
        for operation in operations.upsert_operations
        if operation.lifecycle_action == "archive"
        for uri in operation.uris
    }
    inspected_uris = set(operations.delete_replacements or {}) | set(archive_operations_by_uri)
    for deleted_uri in inspected_uris:
        try:
            content = await viking_fs.read_file(deleted_uri, ctx=ctx)
        except (FileNotFoundError, NotFoundError):
            operation = archive_operations_by_uri.get(deleted_uri)
            if operation is not None:
                operation.precondition_files[deleted_uri] = None
            continue
        if not content:
            operation = archive_operations_by_uri.get(deleted_uri)
            if operation is not None:
                operation.precondition_files[deleted_uri] = None
            continue
        memory_file = MemoryFileUtils.read(content, uri=deleted_uri)
        operation = archive_operations_by_uri.get(deleted_uri)
        if operation is not None:
            operation.precondition_files[deleted_uri] = memory_file
            uris.update(
                experience_case_link_uris(
                    memory_file.backlinks,
                    experience_uri=deleted_uri,
                )
            )
            archived_case_uris = memory_file.extra_fields.get("archived_case_uris", [])
            if isinstance(archived_case_uris, (list, tuple, set)):
                uris.update(str(case_uri) for case_uri in archived_case_uris if case_uri)
            continue
        for link in list(memory_file.links or []) + list(memory_file.backlinks or []):
            if isinstance(link, dict):
                from_uri = link.get("from_uri")
                to_uri = link.get("to_uri")
            else:
                from_uri = link.from_uri
                to_uri = link.to_uri
            if from_uri:
                uris.add(str(from_uri))
            if to_uri:
                uris.add(str(to_uri))
    return uris


async def _acquire_stable_operation_lease(
    operations: ResolvedOperations,
    viking_fs: Any | None,
    ctx: RequestContext,
) -> Any | None:
    lock_paths = _operation_lock_paths(operations, viking_fs, ctx)
    if not lock_paths:
        return None

    required_paths = set(lock_paths)
    for acquisition in range(1, _MEMORY_APPLY_LOCK_MAX_ACQUISITIONS + 1):
        lease = await viking_fs._async_agfs.pathlock_acquire_exact_batch(
            sorted(required_paths),
            timeout_secs=_MEMORY_APPLY_LOCK_TIMEOUT_SECONDS,
        )
        try:
            relation_uris = await _persisted_operation_relation_uris(
                operations,
                viking_fs,
                ctx,
            )
            expanded_paths = required_paths | set(_uri_lock_paths(relation_uris, viking_fs, ctx))
        except BaseException:
            await viking_fs._async_agfs.pathlock_release(lease)
            raise

        if expanded_paths == required_paths:
            return lease

        await viking_fs._async_agfs.pathlock_release(lease)
        required_paths = expanded_paths
        if acquisition == _MEMORY_APPLY_LOCK_MAX_ACQUISITIONS:
            raise RuntimeError(
                "Unable to stabilize memory apply lock coverage after "
                f"{_MEMORY_APPLY_LOCK_MAX_ACQUISITIONS} acquisitions"
            )

    raise AssertionError("unreachable")


def _uri_lock_paths(
    uris: set[str],
    viking_fs: Any | None,
    ctx: RequestContext,
) -> list[str]:
    if viking_fs is None or not hasattr(viking_fs, "_async_agfs"):
        return []
    uri_to_path = getattr(viking_fs, "_uri_to_path", None)
    if not callable(uri_to_path):
        return []
    return sorted(uri_to_path(uri, ctx=ctx) for uri in uris if uri)


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
            # Redo recovery can create the process-global updater before the
            # service compressor is available, using ``vikingdb=None``.  Do
            # not let that degraded first caller permanently disable
            # vectorization for later normal commits with the same user key.
            if vikingdb is not None and existing.vikingdb is not vikingdb:
                existing.vikingdb = vikingdb
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
