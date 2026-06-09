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

from openviking.message import Message
from openviking.server.identity import RequestContext
from openviking.session.memory.dataclass import (
    MemoryFile,
    ResolvedOperation,
    ResolvedOperations,
    StoredLink,
)
from openviking.session.memory.memory_type_registry import MemoryTypeRegistry
from openviking.session.memory.merge_op import MergeOpFactory
from openviking.session.memory.page_id_map import PageIdMap
from openviking.session.memory.utils.memory_file_utils import MemoryFileUtils
from openviking.session.memory.utils.template_utils import TemplateUtils
from openviking.session.memory.utils.uri import render_template
from openviking.storage.viking_fs import get_viking_fs
from openviking.telemetry import tracer
from openviking.telemetry.request_wait_tracker import get_request_wait_tracker
from openviking.utils.time_utils import parse_iso_datetime
from openviking_cli.exceptions import NotFoundError
from openviking_cli.utils import get_logger

logger = get_logger(__name__)


async def write_stored_links(
    links: List[StoredLink],
    ctx: RequestContext,
    viking_fs: Any,
    skip_uris: Optional[set] = None,
) -> None:
    """Write StoredLinks to their endpoint files' links/backlinks fields.

    For each link: from_uri's ``links`` receives the forward link;
    to_uri's ``backlinks`` receives the reverse reference.
    Files listed in skip_uris are skipped (caller handles them in the same write).
    """
    from openviking.session.memory.merge_op.link_merge import merge_links

    skip = skip_uris or set()
    file_links: Dict[str, Dict[str, List[StoredLink]]] = {}
    for link in links:
        if link.from_uri not in skip:
            file_links.setdefault(link.from_uri, {"links": [], "backlinks": []})
            file_links[link.from_uri]["links"].append(link)
        if link.to_uri not in skip:
            file_links.setdefault(link.to_uri, {"links": [], "backlinks": []})
            file_links[link.to_uri]["backlinks"].append(link)

    for uri, link_groups in file_links.items():
        try:
            content = await viking_fs.read_file(uri, ctx=ctx)
            if not content:
                continue
            mf = MemoryFileUtils.read(content, uri=uri)
            if link_groups["links"]:
                mf.links = merge_links(mf.links, [l.model_dump() for l in link_groups["links"]])
            if link_groups["backlinks"]:
                mf.backlinks = merge_links(
                    mf.backlinks, [l.model_dump() for l in link_groups["backlinks"]]
                )
            await viking_fs.write_file(uri, MemoryFileUtils.write(mf), ctx=ctx)
        except Exception as e:
            tracer.error(f"Failed to apply links to {uri}: {e}")


class ExtractContext:
    """Extract context for template rendering."""

    def __init__(self, messages: List[Message]):
        self.messages = messages
        self.page_id_map = PageIdMap()

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

    def get_session_timestamp(self) -> str:
        """取对话第一条消息的时间戳（YYYYMMDDHHMMSS），用于文件名唯一化。

        Fallback 到 datetime.now() 以保证总是返回非空字符串。
        """
        from datetime import datetime

        for msg in self.messages:
            created_at = getattr(msg, "created_at", None)
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
                speaker = msg.peer_id or msg.role
                result.append(f"[{speaker}]: {msg.content}")
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
            tracer.error("VikingFS not available, skipping memory operations")
            return result

        # Use provided registry or fall back to self._registry

        if not self._registry:
            raise ValueError("MemoryTypeRegistry is required for URI resolution")

        # Resolve all URIs first (pass extract_context for template rendering)
        tracer.info(f"[MemoryUpdater] applying operations, isolation_handler={isolation_handler}")

        if operations.has_errors():
            for error in operations.errors:
                result.add_error("unknown", ValueError(error))
            return result

        unresolved_ops = [
            resolved_op for resolved_op in operations.upsert_operations if not resolved_op.uris
        ]
        if unresolved_ops:
            missing = [
                f"{resolved_op.memory_type}(page_id={resolved_op.page_id})"
                for resolved_op in unresolved_ops
            ]
            raise ValueError(
                f"Cannot apply operations: missing resolved URIs for {', '.join(missing)}"
            )

        # Distribute resolved_links to corresponding upsert operations
        self._distribute_links_to_operations(operations)

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

        # Apply delete operations (delete_file_contents is List[MemoryFile])
        # Skip deletes whose URI was just written in the same batch — this happens when the
        # LLM issues a Replace with the same experience_name (delete old + create same-name new),
        # which is semantically an Update. Executing the delete would remove the just-written file.
        upserted_uris = set(result.written_uris + result.edited_uris)
        for file_content in operations.delete_file_contents:
            if file_content.uri in upserted_uris:
                tracer.info(
                    f"[apply_operations] skipping delete for {file_content.uri}: "
                    "URI was upserted in the same batch (Replace-with-same-name treated as Update)"
                )
                continue
            try:
                await self._apply_delete(file_content.uri, ctx)
                result.add_deleted(file_content.uri)
            except Exception as e:
                tracer.error(f"Failed to delete memory {file_content.uri}", e)
                result.add_error(file_content.uri, e)

        # Vectorize written and edited memories
        uri_memory_type_map = {}
        for op in operations.upsert_operations:
            for uri in op.uris:
                uri_memory_type_map[uri] = op.memory_type
        await self._vectorize_memories(
            result,
            ctx,
            extract_context=extract_context,
            uri_memory_type_map=uri_memory_type_map,
        )

        # Apply links to endpoint files not covered by upsert_operations
        if operations.resolved_links:
            await self._apply_links_to_existing_files(
                operations.resolved_links,
                result,
                ctx,
                deleted_uris=set(result.deleted_uris),
            )

        tracer.info(f"Memory operations applied: {result.summary()}")

        # Collect directories that need overview generation
        # uri is now a string, so extract directory using os.path
        dirs = {}
        for operation in operations.upsert_operations:
            for uri_str in operation.uris:
                dir_path = "/".join(uri_str.split("/")[:-1])
                dirs[dir_path] = operation.memory_type
        for file_content in operations.delete_file_contents:
            dir_path = "/".join(file_content.uri.split("/")[:-1])
            dirs[dir_path] = (
                file_content.extra_fields.get("memory_type")
                or file_content.memory_type
                or "unknown"
            )

        for dir, memory_type in dirs.items():
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
            # Always read from disk first to get the latest content,
            # so consecutive patches to the same URI see each other's changes.
            old_content: Optional[MemoryFile] = None
            try:
                content = await viking_fs.read_file(uri, ctx=ctx)
                if content:
                    old_content = MemoryFileUtils.read(content, uri=uri)
            except Exception:
                # File doesn't exist yet, that's okay
                pass
            # Fall back to pre-fetched content if disk read failed
            if old_content is None:
                old_content = resolved_op.old_memory_file_content

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
                            current_value = old_content.plain_content()
                        else:
                            current_value = old_content.extra_fields.get(field.name)
                    # Use merge_op to process field value
                    merge_op = MergeOpFactory.from_field(field)
                    try:
                        new_value = merge_op.apply(current_value, patch_value)
                    except Exception as e:
                        tracer.info(
                            f"[memory_updater] Skipping field update after merge_op failure: uri={uri}, field={field.name}, error={e}"
                        )
                        if current_value is None:
                            metadata.pop(field.name, None)
                        else:
                            metadata[field.name] = current_value
                        continue
                    metadata[field.name] = new_value

            # Preserve system-managed metadata from the old file that is not
            # covered by the schema. These fields are written by the system,
            # never by the LLM, so they would be silently dropped on every
            # Update without this copy.
            if old_content and old_content.extra_fields:
                schema_field_names = {f.name for f in schema.fields} | {"content", "memory_type"}
                for key, val in old_content.extra_fields.items():
                    if key not in schema_field_names and key not in metadata and val is not None:
                        metadata[key] = val

            # Handle links/backlinks fields: merge with existing
            incoming_links_by_uri = getattr(resolved_op, "_incoming_links_by_uri", {})
            incoming_backlinks_by_uri = getattr(resolved_op, "_incoming_backlinks_by_uri", {})
            incoming_links = incoming_links_by_uri.get(uri, [])
            incoming_backlinks = incoming_backlinks_by_uri.get(uri, [])
            has_existing_links = old_content is not None
            if (
                incoming_links
                or incoming_backlinks
                or (has_existing_links and old_content.links)
                or (has_existing_links and old_content.backlinks)
            ):
                from openviking.session.memory.merge_op.link_merge import merge_links

                # Merge links
                existing_links = old_content.links if has_existing_links else []
                if incoming_links:
                    merged_links = merge_links(
                        existing_links,
                        [link.model_dump() for link in incoming_links],
                    )
                    metadata["links"] = merged_links
                elif existing_links:
                    metadata["links"] = existing_links

                # Merge backlinks
                existing_backlinks = old_content.backlinks if has_existing_links else []
                if incoming_backlinks:
                    merged_backlinks = merge_links(
                        existing_backlinks,
                        [link.model_dump() for link in incoming_backlinks],
                    )
                    metadata["backlinks"] = merged_backlinks
                elif existing_backlinks:
                    metadata["backlinks"] = existing_backlinks

            mf = MemoryFile.from_parsed(uri=uri, parsed=metadata)
            new_full_content = MemoryFileUtils.write(
                mf,
                content_template=schema.content_template,
                extract_context=extract_context,
            )
            await viking_fs.write_file(uri, new_full_content, ctx=ctx)

    def _distribute_links_to_operations(self, operations: ResolvedOperations) -> None:
        """Distribute resolved_links to corresponding upsert operations by URI.

        Links go into from_uri's "links" field; backlinks go into to_uri's "backlinks" field.
        """
        # Collect all URIs that will be upserted
        upserted_uris = set()
        for op in operations.upsert_operations:
            op._incoming_links_by_uri = {uri: [] for uri in op.uris}
            op._incoming_backlinks_by_uri = {uri: [] for uri in op.uris}
            for uri in op.uris:
                upserted_uris.add(uri)

        # Attach links to their corresponding upsert operations
        for link in operations.resolved_links:
            # Forward link -> stored in from_uri's "links"
            if link.from_uri in upserted_uris:
                for op in operations.upsert_operations:
                    if link.from_uri in op.uris:
                        op._incoming_links_by_uri[link.from_uri].append(link)
                        break
            # Backlink -> stored in to_uri's "backlinks"
            if link.to_uri in upserted_uris:
                for op in operations.upsert_operations:
                    if link.to_uri in op.uris:
                        op._incoming_backlinks_by_uri[link.to_uri].append(link)
                        break

    async def _apply_links_to_existing_files(
        self,
        resolved_links: List[StoredLink],
        result: MemoryUpdateResult,
        ctx: RequestContext,
        deleted_uris: Optional[set[str]] = None,
    ) -> None:
        """Apply links to endpoint files that are NOT in the current upsert batch."""
        viking_fs = self._get_viking_fs()
        if not viking_fs:
            return
        upserted_uris = set(result.written_uris + result.edited_uris)
        skip = upserted_uris | (deleted_uris or set())
        await write_stored_links(resolved_links, ctx, viking_fs, skip_uris=skip)

    async def _apply_delete(self, uri: str, ctx: RequestContext) -> None:
        """Apply delete operation (uri is already a string)."""
        viking_fs = self._get_viking_fs()

        # Delete from VikingFS
        # VikingFS automatically handles vector index cleanup
        # Pass transaction_handle so rm() reuses the compressor's tree lock
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
        extract_context: Any = None,
        uri_memory_type_map: Dict[str, str] = None,
    ) -> None:
        """Vectorize written and edited memory files.

        Args:
            result: MemoryUpdateResult with written_uris and edited_uris
            ctx: Request context
            extract_context: Extract context for embedding template rendering
            uri_memory_type_map: Mapping from URI to memory_type
        """
        if not self._vikingdb:
            logger.debug("VikingDB not available, skipping vectorization")
            return

        uri_memory_type_map = uri_memory_type_map or {}
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

                mf = MemoryFileUtils.read(content, uri=uri)
                abstract = mf.plain_content()
                embedding_text = abstract

                memory_type = uri_memory_type_map.get(uri)
                if memory_type and self._registry:
                    schema = self._registry.get(memory_type)
                    if schema and schema.embedding_template:
                        template_vars = dict(mf.extra_fields)
                        template_vars["content"] = abstract
                        missing_vars = TemplateUtils.find_missing_variables(
                            schema.embedding_template,
                            template_vars,
                        )
                        if missing_vars:
                            logger.warning(
                                f"Missing embedding template variables for {uri}, falling back to plain content: {sorted(missing_vars)}"
                            )
                        else:
                            try:
                                embedding_text = render_template(
                                    schema.embedding_template,
                                    template_vars,
                                    extract_context=extract_context,
                                )
                            except Exception as e:
                                logger.warning(
                                    f"Failed to render embedding template for {uri}, falling back to plain content: {e}"
                                )

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
                memory_context.set_vectorize(Vectorize(text=embedding_text))

                # Convert to embedding msg and enqueue
                embedding_msg = EmbeddingMsgConverter.from_context(memory_context)
                if embedding_msg:
                    if embedding_msg.telemetry_id:
                        request_wait_tracker.register_embedding_root(
                            embedding_msg.telemetry_id, embedding_msg.id
                        )
                    enqueued = await self._vikingdb.enqueue_embedding_msg(embedding_msg)
                    if not enqueued and embedding_msg.telemetry_id:
                        request_wait_tracker.mark_embedding_failed(
                            embedding_msg.telemetry_id,
                            embedding_msg.id,
                            "embedding enqueue returned false",
                        )
                    logger.debug(f"Enqueued memory for vectorization: {uri}")

            except Exception as e:
                tracer.error(f"Failed to vectorize memory {uri}: {e}")

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
        from openviking.session.memory.utils.memory_file_utils import MemoryFileUtils

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
            tracer.error(f"Failed to list files in {directory}: {e}")
            return

        # If no memory files, delete the .overview.md and the directory if empty
        if not md_files:
            overview_path = f"{directory.rstrip('/')}/.overview.md"
            try:
                await viking_fs.delete_file(overview_path, ctx=ctx)
            except Exception:
                pass
            # Try to delete empty directory
            try:
                await viking_fs.delete_file(directory, ctx=ctx)
            except Exception:
                pass
            return

        # Parse each file and collect items
        items = []
        for file_path in md_files:
            try:
                content = await viking_fs.read_file(file_path, ctx=ctx)
                mf = MemoryFileUtils.read(content, uri=file_path)

                # Extract filename from path
                filename = file_path.split("/")[-1]

                items.append(
                    {
                        "file_name": filename,
                        "file_content": mf.to_metadata(),
                    }
                )
            except Exception as e:
                tracer.error(f"Failed to parse {file_path}: {e}")
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
            tracer.error(f"Failed to render overview template for {memory_type}: {e}")
            return

        # Write .overview.md to the directory
        overview_path = f"{directory.rstrip('/')}/.overview.md"
        try:
            await viking_fs.write_file(overview_path, rendered, ctx=ctx)
        except Exception as e:
            tracer.error(f"Failed to write overview {overview_path}: {e}")
