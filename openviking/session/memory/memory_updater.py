# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0
"""
Memory updater - applies MemoryOperations directly.

This is the system executor that applies LLM's final output (MemoryOperations)
to the storage system.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any, Dict, List, Optional, Tuple


if TYPE_CHECKING:
    from openviking.session.memory.memory_isolation_handler import MemoryIsolationHandler

from openviking.core.namespace import agent_space_fragment, user_space_fragment
from openviking.message import Message
from openviking.server.identity import RequestContext
from openviking.session.memory.dataclass import MemoryField, MemoryFileContent, ResolvedOperations, ResolvedOperation
from openviking.session.memory.memory_type_registry import MemoryTypeRegistry
from openviking.session.memory.merge_op import MergeOpFactory
from openviking.session.memory.utils import (
    deserialize_full,
    flat_model_to_dict,
    parse_memory_file_with_fields,
    serialize_with_metadata,
)
from openviking.session.memory.utils.uri import supplement_operation_uris, render_template
from openviking.storage.viking_fs import get_viking_fs
from openviking.telemetry import tracer
from openviking.telemetry.request_wait_tracker import get_request_wait_tracker
from openviking.utils.time_utils import parse_iso_datetime
from openviking_cli.exceptions import NotFoundError
from openviking_cli.utils import get_logger

logger = get_logger(__name__)


class ExtractContext:
    """Extract context for template rendering."""

    def __init__(self, messages: List[Message]):
        self.messages = messages

    def get_first_message_time_from_ranges(self, ranges_str: str) -> str | None:
        """根据 ranges 字符串获取第一条消息的时间（YAML 日期格式）"""
        if not ranges_str:
            return None
        msg_range = self.read_message_ranges(ranges_str)
        return msg_range._first_message_time()

    def get_first_message_time_with_weekday_from_ranges(self, ranges_str: str) -> str | None:
        """根据 ranges 字符串获取第一条消息的时间，带周几"""
        if not ranges_str:
            return None
        msg_range = self.read_message_ranges(ranges_str)
        return msg_range._first_message_time_with_weekday()

    def get_year(self, ranges_str: str) -> str | None:
        """根据 ranges 字符串获取第一条消息的年份"""
        if not ranges_str:
            return None
        msg_range = self.read_message_ranges(ranges_str)
        first_time = msg_range._first_message_time()
        return first_time.split("-")[0] if first_time else None

    def get_month(self, ranges_str: str) -> str | None:
        """根据 ranges 字符串获取第一条消息的月份"""
        if not ranges_str:
            return None
        msg_range = self.read_message_ranges(ranges_str)
        first_time = msg_range._first_message_time()
        return first_time.split("-")[1] if first_time else None

    def get_day(self, ranges_str: str) -> str | None:
        """根据 ranges 字符串获取第一条消息的日期"""
        if not ranges_str:
            return None
        msg_range = self.read_message_ranges(ranges_str)
        first_time = msg_range._first_message_time()
        return first_time.split("-")[2] if first_time else None

    def get_timestamp_from_ranges(self, ranges_str: str) -> str:
        """根据 ranges 获取第一条消息的紧凑时间戳（YYYYMMDDHHMMSS），用于文件名去重。

        Fallback 到 datetime.now() 以保证总是返回非空字符串。
        """
        from datetime import datetime

        msg_range = self.read_message_ranges(ranges_str) if ranges_str else None
        if msg_range:
            for elem in msg_range.elements:
                if isinstance(elem, str):
                    continue
                created_at = getattr(elem, "created_at", None)
                if created_at:
                    try:
                        return datetime.fromisoformat(created_at).strftime("%Y%m%d%H%M%S")
                    except (ValueError, TypeError):
                        continue
        return datetime.now().strftime("%Y%m%d%H%M%S")

    def get_event_content(self, ranges_str: str, summary: str, ratio_threshold: float = 0.2) -> str:
        """根据原始消息与 summary 的字符数比例，决定返回原始消息还是摘要。"""
        if not ranges_str or not summary:
            return summary or ""
        msg_range = self.read_message_ranges(ranges_str)
        original = msg_range.pretty_print()
        if not original:
            return summary
        if len(summary) / len(original) >= ratio_threshold:
            return original
        return summary

    def read_message_ranges(self, ranges_str: str) -> "MessageRange":
        """Parse ranges string like "0-10,50-60" or "7,9,11,13" and return combined MessageRange.

        If there's a gap between ranges (e.g., 0-10 and 50-60), add "..." as separator.
        Supports:
        - "0-10,50-60" - ranges
        - "7,9,11,13" - single indices
        - "0-10,15,20-25" - mixed
        """
        if not ranges_str:
            return MessageRange([])

        # 解析所有范围/索引
        ranges = []
        for part in ranges_str.split(","):
            part = part.strip()
            if not part:
                continue
            if "-" in part:
                start, end = part.split("-")
                ranges.append((int(start), int(end)))
            else:
                # 单个索引转为相同起止范围
                idx = int(part)
                ranges.append((idx, idx))

        if not ranges:
            return MessageRange([])

        # 按 start 排序
        ranges.sort(key=lambda x: x[0])

        # 合并连续/重叠的范围
        merged = [ranges[0]]
        for start, end in ranges[1:]:
            prev_start, prev_end = merged[-1]
            if start <= prev_end + 1:
                merged[-1] = (prev_start, max(prev_end, end))
            else:
                merged.append((start, end))

        # elements 是 List[List[Message]] - 每段连续消息是一个列表
        elements: List[List[Message]] = []
        for start, end in merged:
            # 兼容 LLM 提取的 range 越界情况
            if start < 0:
                start = 0
            if end >= len(self.messages):
                end = len(self.messages) - 1
            if start > end:
                continue
            range_msgs = self.messages[start : end + 1]
            elements.append(range_msgs)

        return MessageRange(elements)


class MessageRange:
    """Represents a range of messages for formatting."""

    def __init__(self, elements: List[List[Message]]):
        self.elements = elements

    def pretty_print(self) -> str:
        """Pretty print the message range with '...' separator between non-contiguous ranges."""
        result = []
        for i, msg_group in enumerate(self.elements):
            for msg in msg_group:
                role_id = msg.role_id if msg.role_id else msg.role
                result.append(f"[{role_id}]: {msg.content}")
            # Add "..." separator between non-contiguous message groups
            if i < len(self.elements) - 1:
                result.append("...")
        return "\n".join(result)

    def _first_message_time(self) -> str | None:
        """获取第一条消息的时间（内部方法）"""
        for msg_group in self.elements:
            for msg in msg_group:
                if hasattr(msg, "created_at") and msg.created_at:
                    dt = parse_iso_datetime(msg.created_at)
                    return dt.strftime("%Y-%m-%d")
        return None

    def _first_message_time_with_weekday(self) -> str | None:
        """获取第一条消息的时间，带周几"""
        weekday_en = [
            "Monday",
            "Tuesday",
            "Wednesday",
            "Thursday",
            "Friday",
            "Saturday",
            "Sunday",
        ]
        for msg_group in self.elements:
            for msg in msg_group:
                if hasattr(msg, "created_at") and msg.created_at:
                    dt = parse_iso_datetime(msg.created_at)
                    weekday = weekday_en[dt.weekday()]
                    return f"{dt.strftime('%Y-%m-%d')} ({weekday})"
        return None


class MemoryUpdateResult:
    """Result of memory update operation."""

    def __init__(self):
        self.written_uris: List[str] = []
        self.edited_uris: List[str] = []
        self.deleted_uris: List[str] = []
        self.errors: List[Tuple[str, Exception]] = []

    def add_written(self, uri: str) -> None:
        self.written_uris.append(uri)

    def add_edited(self, uri: str) -> None:
        self.edited_uris.append(uri)

    def add_deleted(self, uri: str) -> None:
        self.deleted_uris.append(uri)

    def add_error(self, uri: str, error: Exception) -> None:
        self.errors.append((uri, error))

    def has_changes(self) -> bool:
        return len(self.written_uris) > 0 or len(self.edited_uris) > 0 or len(self.deleted_uris) > 0

    def summary(self) -> str:
        return (
            f"Written: {len(self.written_uris)}, "
            f"Edited: {len(self.edited_uris)}, "
            f"Deleted: {len(self.deleted_uris)}, "
            f"Errors: {len(self.errors)}"
        )


class MemoryUpdater:
    """
    Applies MemoryOperations to storage.

    This is the system executor that directly applies the LLM's final output.
    No function calls are used for write/edit/delete - these are executed directly.
    """

    def __init__(
        self, registry: Optional[MemoryTypeRegistry] = None, vikingdb=None, transaction_handle=None
    ):
        self._viking_fs = None
        self._registry = registry
        self._vikingdb = vikingdb
        self._transaction_handle = transaction_handle

    def set_registry(self, registry: MemoryTypeRegistry) -> None:
        """Set the memory type registry for URI resolution."""
        self._registry = registry

    def _get_viking_fs(self):
        """Get or create VikingFS instance."""
        if self._viking_fs is None:
            self._viking_fs = get_viking_fs()
        return self._viking_fs

    @tracer()
    async def apply_operations(
        self,
        operations: ResolvedOperations,
        ctx: RequestContext,
        extract_context: ExtractContext = None,
        isolation_handler: MemoryIsolationHandler = None,
    ) -> MemoryUpdateResult:

        result = MemoryUpdateResult()
        viking_fs = self._get_viking_fs()

        if not viking_fs:
            logger.warning("VikingFS not available, skipping memory operations")
            return result

        # Use provided registry or fall back to self._registry

        if not self._registry:
            raise ValueError("MemoryTypeRegistry is required for URI resolution")

        # Resolve all URIs first (pass extract_context for template rendering)
        logger.info(f"[MemoryUpdater] applying operations, isolation_handler={isolation_handler}")

        if operations.has_errors():
            for error in operations.errors:
                result.add_error("unknown", ValueError(error))
            return result

        # 为每个upsert operation填充需要更新的uri列表
        supplement_operation_uris(
            operations,
            self._registry,
            extract_context=extract_context,
            isolation_handler=isolation_handler,
        )

        # Apply unified operations - _apply_edit returns True if edited, False if written
        for resolved_op in operations.upsert_operations:
            try:
                await self._apply_upsert(
                    resolved_op,
                    ctx,
                    extract_context=extract_context,
                )
                # Add all uris to result (uris is List[str])
                if resolved_op.is_edit():
                    for uri in resolved_op.uris:
                        result.add_edited(uri)
                else:
                    for uri in resolved_op.uris:
                        result.add_written(uri)
            except Exception as e:
                tracer.error(
                    f"Failed to apply operation: op_type={type(resolved_op).__name__}, uris={resolved_op.uris}",
                    e,
                )
                for uri in resolved_op.uris:
                    result.add_error(uri, e)

        # Apply delete operations (delete_file_contents is List[MemoryFileContent])
        for file_content in operations.delete_file_contents:
            try:
                await self._apply_delete(file_content.uri, ctx)
                result.add_deleted(file_content.uri)
            except Exception as e:
                tracer.error(f"Failed to delete memory {file_content.uri}", e)
                result.add_error(file_content.uri, e)

        # Vectorize written and edited memories
        await self._vectorize_memories(result, ctx)

        tracer.info(f"Memory operations applied: {result.summary()}")

        # Collect directories that need overview generation
        # uri is now a string, so extract directory using os.path
        dirs = dict()
        for operation in operations.upsert_operations:
            for uri_str in operation.uris:
                dir_path = "/".join(uri_str.split("/")[:-1])
                dirs[dir_path] = operation.memory_type
        for file_content in operations.delete_file_contents:
            dir_path = "/".join(file_content.uri.split("/")[:-1])
            dirs[dir_path] = file_content.memory_fields.get("memory_type", "unknown")

        for dir, memory_type in dirs.items():
            logger.info(f"[apply_operations] Generating overview for {memory_type} at {dir}")
            await self.generate_overview(memory_type, dir, ctx, extract_context)

        return result

    async def _apply_upsert(
        self, resolved_op: ResolvedOperation, ctx: RequestContext, extract_context: Any = None
    ):
        """Apply upsert operation from a flat model."""
        viking_fs = self._get_viking_fs()

        memory_type = resolved_op.memory_type
        schema = self._registry.get(memory_type)
        # Process each URI independently
        for uri in resolved_op.uris:
            old_content = resolved_op.old_memory_file_content
            if old_content is None:
                # If no pre-fetched content, try to read it now
                try:
                    content = await viking_fs.read_file(uri, ctx=ctx)
                    if content:
                        parsed = parse_memory_file_with_fields(content)
                        old_content = MemoryFileContent(
                            uri=uri,
                            plain_content=parsed.get("content", ""),
                            memory_fields=parsed
                        )
                except Exception:
                    # File doesn't exist yet, that's okay
                    pass

            metadata: Dict[str, Any] = dict(resolved_op.memory_fields)
            # Process fields defined in schema (apply merge_op)
            for field in schema.fields:
                if field.name in resolved_op.memory_fields:
                    patch_value = resolved_op.memory_fields[field.name]
                    # Get current value for this URI
                    if old_content is None:
                        current_value = None
                    else:
                        if field.name == "content":
                            current_value = old_content.plain_content
                        else:
                            current_value = old_content.memory_fields.get(
                                field.name
                            )
                    # Use merge_op to process field value
                    merge_op = MergeOpFactory.from_field(field)
                    new_value = merge_op.apply(current_value, patch_value)
                    metadata[field.name] = new_value

            # serialize_with_metadata modifies metadata dict, so pass a copy
            new_full_content = serialize_with_metadata(
                metadata.copy(),
                content_template=schema.content_template,
                extract_context=extract_context,
            )
            await viking_fs.write_file(uri, new_full_content, ctx=ctx)

    async def _apply_delete(self, uri: str, ctx: RequestContext) -> None:
        """Apply delete operation (uri is already a string)."""
        viking_fs = self._get_viking_fs()

        # Delete from VikingFS
        # VikingFS automatically handles vector index cleanup
        # Pass transaction_handle so rm() reuses the compressor's subtree lock
        # instead of trying to acquire a new lock (which would conflict).
        try:
            await viking_fs.rm(uri, recursive=False, ctx=ctx, lock_handle=self._transaction_handle)
        except NotFoundError:
            tracer.error(f"Memory not found for delete: {uri}")
            # Idempotent - deleting non-existent file succeeds

    async def _vectorize_memories(
        self,
        result: MemoryUpdateResult,
        ctx: RequestContext,
    ) -> None:
        """Vectorize written and edited memory files.

        Args:
            result: MemoryUpdateResult with written_uris and edited_uris
            ctx: Request context
        """
        if not self._vikingdb:
            logger.debug("VikingDB not available, skipping vectorization")
            return

        viking_fs = self._get_viking_fs()
        request_wait_tracker = get_request_wait_tracker()

        # Collect all URIs to vectorize (skip .overview.md and .abstract.md - they are handled separately)
        # Also skip URIs that were deleted in the same batch
        uris_to_vectorize = []
        deleted_set = set(result.deleted_uris)
        for uri in result.written_uris + result.edited_uris:
            if uri in deleted_set:
                continue
            if not uri.endswith("/.overview.md") and not uri.endswith("/.abstract.md"):
                uris_to_vectorize.append(uri)

        if not uris_to_vectorize:
            logger.debug("No memory files to vectorize")
            return

        for uri in uris_to_vectorize:
            try:
                # Read the memory file to get content
                content = await viking_fs.read_file(uri, ctx=ctx) or ""

                # Use parse_memory_file_with_fields to strip MEMORY_FIELDS comment
                parsed = parse_memory_file_with_fields(content)
                abstract = parsed.get("content", "")

                # Get parent URI
                from openviking_cli.utils.uri import VikingURI

                parent_uri = VikingURI(uri).parent.uri

                # Create Context for vectorization
                from openviking.core.context import Context, ContextLevel, Vectorize
                from openviking.storage.queuefs.embedding_msg_converter import EmbeddingMsgConverter

                memory_context = Context(
                    uri=uri,
                    parent_uri=parent_uri,
                    is_leaf=True,
                    abstract=abstract,
                    context_type="memory",
                    level=ContextLevel.DETAIL,
                    user=ctx.user,
                    account_id=ctx.account_id,
                )
                memory_context.set_vectorize(Vectorize(text=content))

                # Convert to embedding msg and enqueue
                embedding_msg = EmbeddingMsgConverter.from_context(memory_context)
                if embedding_msg:
                    enqueued = await self._vikingdb.enqueue_embedding_msg(embedding_msg)
                    if enqueued and embedding_msg.telemetry_id:
                        request_wait_tracker.register_embedding_root(
                            embedding_msg.telemetry_id, embedding_msg.id
                        )
                    logger.debug(f"Enqueued memory for vectorization: {uri}")

            except Exception as e:
                logger.warning(f"Failed to vectorize memory {uri}: {e}")

    async def generate_overview(
        self,
        memory_type: str,
        directory: str,
        ctx: RequestContext,
        extract_context: Any = None,
    ) -> None:
        """
        Generate .overview.md file for a directory based on overview_template.

        Args:
            memory_type: Memory type name (e.g., 'events')
            directory: Directory path containing memory files
            ctx: Request context
        """
        from openviking.session.memory.utils.messages import parse_memory_file_with_fields

        # Get the schema for this memory type
        registry = self._registry
        schema = registry.get(memory_type)

        if not schema or not schema.overview_template:
            logger.debug(f"No overview_template for memory type: {memory_type}")
            return

        viking_fs = self._get_viking_fs()

        # List direct .md files in the directory (excluding .overview.md and .abstract.md)
        try:
            # Use ls to list direct children
            entries = await viking_fs.ls(directory, show_all_hidden=True, ctx=ctx)

            # Extract file paths from ls entries
            md_files = []
            base_uri = directory.rstrip("/")
            for entry in entries:
                name = entry.get("name", "")
                if (
                    name.endswith(".md")
                    and not name.endswith(".overview.md")
                    and not name.endswith(".abstract.md")
                ):
                    md_files.append(f"{base_uri}/{name}")

        except Exception as e:
            logger.warning(f"Failed to list files in {directory}: {e}")
            return

        # If no memory files, delete the .overview.md and the directory if empty
        if not md_files:
            overview_path = f"{directory.rstrip('/')}/.overview.md"
            try:
                await viking_fs.delete_file(overview_path, ctx=ctx)
                tracer.info(f"[generate_overview] Removed orphaned overview: {overview_path}")
            except Exception:
                pass
            # Try to delete empty directory
            try:
                await viking_fs.delete_file(directory, ctx=ctx)
                tracer.info(f"[generate_overview] Removed empty directory: {directory}")
            except Exception:
                pass
            return

        # Parse each file and collect items
        items = []
        for file_path in md_files:
            try:
                content = await viking_fs.read_file(file_path, ctx=ctx)
                parsed = parse_memory_file_with_fields(content)

                # Extract filename from path
                filename = file_path.split("/")[-1]

                items.append(
                    {
                        "file_name": filename,
                        "file_content": parsed,
                    }
                )
            except Exception as e:
                logger.warning(f"Failed to parse {file_path}: {e}")
                continue

        if not items:
            logger.debug(f"No valid memory files parsed in {directory}")
            return

        # Render the template
        try:
            rendered = render_template(
                schema.overview_template,
                {
                    "memory_type": memory_type,
                    "items": items,
                },
                extract_context=extract_context,
            )
        except Exception as e:
            logger.error(f"Failed to render overview template for {memory_type}: {e}")
            return

        # Write .overview.md to the directory
        overview_path = f"{directory.rstrip('/')}/.overview.md"
        try:
            await viking_fs.write_file(overview_path, rendered, ctx=ctx)
            tracer.info(f"[generate_overview] Generated overview: {overview_path}")
        except Exception as e:
            logger.error(f"Failed to write overview {overview_path}: {e}")
