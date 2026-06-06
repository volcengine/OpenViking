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
import contextlib
import threading
import time
from dataclasses import dataclass, field
from typing import Any, Hashable

from openviking.message import Message
from openviking.server.identity import RequestContext
from openviking.session.memory.dataclass import (
    MemoryFile,
    MemoryTypeSchema,
    ResolvedOperation,
    ResolvedOperations,
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
from openviking.storage.viking_fs import get_viking_fs
from openviking.telemetry import tracer
from openviking_cli.utils import get_logger
from openviking_cli.utils.config import get_openviking_config

logger = get_logger(__name__)


@dataclass(slots=True)
class StreamingMemoryUpdaterConfig:
    """Configuration for automatic streaming ordinary-memory updates."""

    max_operations_per_update: int = 8
    max_wait_seconds: float = 30.0
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
    _buffer: list[_BufferedMemoryUpdate] = field(init=False, repr=False)
    _buffer_lock: asyncio.Lock = field(init=False, repr=False)
    _flush_lock: asyncio.Lock = field(init=False, repr=False)
    _timer_task: asyncio.Task[Any] | None = field(init=False, default=None, repr=False)
    _last_result: StreamingMemoryUpdateResult | None = field(init=False, default=None, repr=False)
    _closed: bool = field(init=False, default=False, repr=False)

    def __post_init__(self) -> None:
        self.registry = self.registry or create_default_registry()
        self._buffer = []
        self._buffer_lock = asyncio.Lock()
        self._flush_lock = asyncio.Lock()
        self._timer_task = None
        self._last_result = None
        self._closed = False

    @property
    def closed(self) -> bool:
        return self._closed

    @property
    def last_result(self) -> StreamingMemoryUpdateResult | None:
        return self._last_result

    async def get_buffered_operation_count(self) -> int:
        async with self._buffer_lock:
            return sum(_operation_count(item.request.operations) for item in self._buffer)

    async def close(self) -> StreamingMemoryUpdateResult | None:
        if self._closed:
            return None
        self._closed = True
        await self._stop_timer_task()
        return await self._flush_ready_batch(reason="close")

    @tracer("memory.streaming_updater.submit", ignore_result=True, ignore_args=True)
    async def submit(self, request: MemoryUpdateRequest) -> StreamingMemoryUpdateResult:
        """Submit one resolved update request.

        For consistency with session.commit semantics, submit always returns an
        applied result.  It still batches concurrent requests: if another flush
        is already in progress, or if multiple submits arrive before the flush
        lock runs, they are merged and applied together.
        """

        if self._closed:
            raise RuntimeError("StreamingMemoryUpdater is closed")
        if request.ctx is None:
            raise ValueError("MemoryUpdateRequest.ctx is required")
        self._ensure_timer_task()
        async with self._buffer_lock:
            self._buffer.append(
                _BufferedMemoryUpdate(request=request, submitted_at=time.monotonic())
            )
            buffered_ops = sum(_operation_count(item.request.operations) for item in self._buffer)
            tracer.info(
                "StreamingMemoryUpdater buffered request "
                f"new_operations={_operation_count(request.operations)} "
                f"buffered_operations={buffered_ops}",
                console=self.config.trace_console,
            )
        return await self._flush_ready_batch(reason="submit")

    def _ensure_timer_task(self) -> None:
        if self._timer_task is not None and not self._timer_task.done():
            return
        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:
            logger.warning("[StreamingMemoryUpdater] timer loop not started: no running event loop")
            self._timer_task = None
            return
        self._timer_task = loop.create_task(
            self._run_timer_loop(),
            name="openviking-streaming-memory-updater-flush-loop",
        )

    async def _stop_timer_task(self) -> None:
        task = self._timer_task
        if task is None:
            return
        self._timer_task = None
        if task.done():
            with contextlib.suppress(asyncio.CancelledError):
                await task
            return
        task.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await task

    async def _run_timer_loop(self) -> None:
        while True:
            try:
                await asyncio.sleep(self.config.timer_check_interval_seconds)
                if await self._should_flush_by_time_or_count():
                    await self._flush_ready_batch(reason="timer")
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                logger.warning(f"[StreamingMemoryUpdater] timer flush loop iteration failed: {exc}")

    async def _should_flush_by_time_or_count(self) -> bool:
        async with self._buffer_lock:
            if not self._buffer:
                return False
            op_count = sum(_operation_count(item.request.operations) for item in self._buffer)
            if op_count >= self.config.max_operations_per_update:
                return True
            oldest = min(item.submitted_at for item in self._buffer)
            return (time.monotonic() - oldest) >= self.config.max_wait_seconds

    async def _flush_ready_batch(self, *, reason: str) -> StreamingMemoryUpdateResult:
        async with self._flush_lock:
            async with self._buffer_lock:
                if not self._buffer:
                    if self._last_result is not None:
                        return self._last_result
                    empty_result = StreamingMemoryUpdateResult(
                        operations=ResolvedOperations(
                            upsert_operations=[],
                            delete_file_contents=[],
                            errors=[],
                        ),
                        apply_result=MemoryUpdateResult(),
                        request_count=0,
                        metadata={"flush_reason": reason, "empty": True},
                    )
                    self._last_result = empty_result
                    return empty_result
                items = self._buffer
                self._buffer = []

            try:
                merged_operations = await self._merge_items(items)
                first_request = items[0].request
                updater = MemoryUpdater(
                    registry=self.registry,
                    vikingdb=self.vikingdb,
                    transaction_handle=None,
                )
                extract_context = ExtractContext(_combined_messages(items))
                isolation_handler = _make_isolation_handler(first_request, extract_context)
                apply_result = await updater.apply_operations(
                    merged_operations,
                    first_request.ctx,
                    extract_context=extract_context,
                    isolation_handler=isolation_handler,
                )
            except Exception:
                await self._restore_front(items)
                raise

            result = StreamingMemoryUpdateResult(
                operations=merged_operations,
                apply_result=apply_result,
                request_count=len(items),
                metadata={
                    "flush_reason": reason,
                    "operation_count": _operation_count(merged_operations),
                },
            )
            self._last_result = result
            tracer.info(
                "StreamingMemoryUpdater flush finished "
                f"reason={reason} request_count={len(items)} "
                f"written_uris={apply_result.written_uris} "
                f"edited_uris={apply_result.edited_uris} "
                f"deleted_uris={apply_result.deleted_uris} "
                f"errors={apply_result.errors}",
                console=self.config.trace_console,
            )
            return result

    async def _restore_front(self, items: list["_BufferedMemoryUpdate"]) -> None:
        async with self._buffer_lock:
            self._buffer = [*items, *self._buffer]

    async def _merge_items(self, items: list["_BufferedMemoryUpdate"]) -> ResolvedOperations:
        all_ops = ResolvedOperations(
            upsert_operations=[],
            delete_file_contents=[],
            errors=[],
            resolved_links=[],
        )
        for item in items:
            ops = item.request.operations
            all_ops.upsert_operations.extend(list(ops.upsert_operations or []))
            all_ops.delete_file_contents.extend(list(ops.delete_file_contents or []))
            all_ops.errors.extend(list(ops.errors or []))
            all_ops.resolved_links.extend(list(getattr(ops, "resolved_links", []) or []))
        return await merge_memory_operations(
            operations=all_ops,
            messages=_combined_messages(items),
            ctx=items[0].request.ctx,
            registry=self.registry or create_default_registry(),
            strict_extract_errors=any(item.request.strict_extract_errors for item in items),
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

    merged_upserts = list(passthrough_upserts)
    merged_deletes = list(operations.delete_file_contents)
    registry = registry or create_default_registry()
    for memory_type, memory_ops in groups.items():
        try:
            merged = await merge_one_memory_type_operations(
                memory_type=memory_type,
                operations=memory_ops,
                messages=messages,
                ctx=ctx,
                registry=registry,
                trace_console=trace_console,
            )
            merged_upserts.extend(merged.upsert_operations)
            merged_deletes.extend(merged.delete_file_contents)
        except Exception as exc:
            logger.warning("[streaming_memory_updater] merge failed for %s: %s", memory_type, exc)
            if strict_extract_errors:
                raise
            merged_upserts.extend(memory_ops)

    return ResolvedOperations(
        upsert_operations=merged_upserts,
        delete_file_contents=merged_deletes,
        errors=list(operations.errors),
        resolved_links=list(getattr(operations, "resolved_links", []) or []),
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
    if can_fast_path_memory_operations(operations):
        return ResolvedOperations(
            upsert_operations=list(operations), delete_file_contents=[], errors=[]
        )

    registry = registry or create_default_registry()
    schema = registry.get(memory_type)
    if schema is None:
        raise ValueError(f"Memory schema not found: {memory_type}")

    extract_context = ExtractContext(messages)
    original_file_uris = list(
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
        original_file_uris=original_file_uris,
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
        "[streaming_memory_updater] merge input "
        f"memory_type={memory_type} original_files={original_file_uris} patch_count={len(patches)}",
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
        "[streaming_memory_updater] merge output "
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
        },
        deep=True,
    )


def operation_to_patch(
    op: ResolvedOperation,
    *,
    schema: MemoryTypeSchema,
    extract_context: ExtractContext,
) -> PatchMergePatch:
    uri = _first_uri(getattr(op, "uris", []) or [])
    old_file = getattr(op, "old_memory_file_content", None)
    after_content = render_operation_after_file_content(
        op, schema=schema, extract_context=extract_context
    )
    target_name = str(
        (getattr(op, "memory_fields", {}) or {}).get("name")
        or (getattr(op, "memory_fields", {}) or {}).get(f"{op.memory_type.rstrip('s')}_name")
        or (uri or "").rstrip("/").split("/")[-1].removesuffix(".md")
        or op.memory_type
    )
    return PatchMergePatch(
        target_name=target_name,
        target_uri=uri,
        before_content=old_file.plain_content() if old_file is not None else None,
        after_content=after_content,
        metadata={
            "memory_type": op.memory_type,
            "memory_fields": dict(getattr(op, "memory_fields", {}) or {}),
        },
    )


def render_operation_after_file_content(
    op: ResolvedOperation,
    *,
    schema: MemoryTypeSchema,
    extract_context: ExtractContext,
) -> str:
    old_content = getattr(op, "old_memory_file_content", None)
    metadata: dict[str, Any] = dict(getattr(op, "memory_fields", {}) or {})
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
        except Exception:
            logger.debug(
                "Failed to preview memory patch field: memory_type=%s field=%s",
                op.memory_type,
                field_def.name,
                exc_info=True,
            )

    if old_content and old_content.extra_fields:
        schema_field_names = {field.name for field in schema.fields} | {"content", "memory_type"}
        for key, value in old_content.extra_fields.items():
            if key not in schema_field_names and key not in metadata and value is not None:
                metadata[key] = value
    metadata.setdefault("memory_type", op.memory_type)
    mf = MemoryFile.from_parsed(uri=_first_uri(op.uris), parsed=dict(metadata))
    try:
        return MemoryFileUtils.write(
            mf,
            content_template=schema.content_template,
            extract_context=extract_context,
        )
    except Exception:
        return operation_after_content(op)


def operation_after_content(op: ResolvedOperation) -> str:
    import json

    fields = dict(getattr(op, "memory_fields", {}) or {})
    if fields.get("content") is not None:
        return str(fields.get("content") or "")
    return json.dumps(fields, ensure_ascii=False, indent=2, sort_keys=True)


def can_fast_path_memory_operations(operations: list[ResolvedOperation]) -> bool:
    if not operations:
        return True
    if all(getattr(op, "old_memory_file_content", None) is None for op in operations):
        uris = [_first_uri(op.uris) for op in operations]
        return len(uris) == len(set(uris))
    if len(operations) != 1:
        return False
    op = operations[0]
    old_file = getattr(op, "old_memory_file_content", None)
    if old_file is None:
        return True
    fields = dict(getattr(op, "memory_fields", {}) or {})
    if "content" not in fields:
        return False
    return old_file.plain_content().strip() == str(fields.get("content") or "").strip()


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


@dataclass(slots=True)
class _BufferedMemoryUpdate:
    request: MemoryUpdateRequest
    submitted_at: float


def _combined_messages(items: list[_BufferedMemoryUpdate]) -> list[Message]:
    messages: list[Message] = []
    for item in items:
        messages.extend(item.request.messages)
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
