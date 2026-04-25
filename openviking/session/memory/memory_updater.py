# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0
"""
Memory updater - applies MemoryOperations directly.

This is the system executor that applies LLM's final output (MemoryOperations)
to the storage system.
"""

from typing import Any, Dict, List, Optional, Tuple

import jinja2

from openviking.core.namespace import agent_space_fragment, user_space_fragment
from openviking.message import Message
from openviking.server.identity import RequestContext
from openviking.session.memory.dataclass import MemoryField
from openviking.session.memory.memory_type_registry import MemoryTypeRegistry
from openviking.session.memory.merge_op import MergeOpFactory
from openviking.session.memory.utils import (
    deserialize_full,
    flat_model_to_dict,
    parse_memory_file_with_fields,
    resolve_all_operations,
    serialize_with_metadata,
)
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

        # elements 可以是 Message 或 str ("...")
        elements: List[Message | str] = []
        for i, (start, end) in enumerate(ranges):
            # 兼容 LLM 提取的 range 越界情况
            if start < 0:
                start = 0
            if end >= len(self.messages):
                end = len(self.messages) - 1
            if start > end:
                continue
            range_msgs = self.messages[start : end + 1]

            if i > 0:
                prev_end = ranges[i - 1][1]
                # 如果有间隔，加 ...
                if start > prev_end + 1:
                    elements.append("...")
            elements.extend(range_msgs)

        return MessageRange(elements)


class MessageRange:
    """Represents a range of messages for formatting."""

    def __init__(self, elements: List[Message | str]):
        self.elements = elements

    def pretty_print(self) -> str:
        """Pretty print the message range."""
        result = []
        for elem in self.elements:
            if isinstance(elem, str):
                result.append(elem)
            else:
                result.append(f"[{elem.role}]: {elem.content}")
        return "\n".join(result)

    def _first_message_time(self) -> str | None:
        """获取第一条消息的时间（内部方法）"""
        for elem in self.elements:
            if isinstance(elem, str):
                continue
            if hasattr(elem, "created_at") and elem.created_at:
                dt = parse_iso_datetime(elem.created_at)
                return dt.strftime("%Y-%m-%d")
        return None

    def _first_message_time_with_weekday(self) -> str | None:
        """获取第一条消息的时间，带周几（内部方法）"""
        for elem in self.elements:
            if isinstance(elem, str):
                continue
            if hasattr(elem, "created_at") and elem.created_at:
                # 获取周几的英文全称
                weekday_en = [
                    "Monday",
                    "Tuesday",
                    "Wednesday",
                    "Thursday",
                    "Friday",
                    "Saturday",
                    "Sunday",
                ]
                dt = parse_iso_datetime(elem.created_at)
                weekday = weekday_en[dt.weekday()]
                return f"{dt.strftime('%Y-%m-%d')} ({weekday})"
        return None


class MemoryDiffEntry:
    """A single memory diff entry tracking before/after content."""

    def __init__(self, uri: str, before: str = "", after: str = ""):
        self.uri = uri
        self.before = before
        self.after = after


class MemoryDeleteEntry:
    """A single memory delete entry tracking deleted content."""

    def __init__(self, uri: str, deleted_content: str = ""):
        self.uri = uri
        self.deleted_content = deleted_content


class MemoryUpdateResult:
    """Result of memory update operation."""

    def __init__(self):
        self.written_uris: List[str] = []
        self.edited_uris: List[str] = []
        self.deleted_uris: List[str] = []
        self.errors: List[Tuple[str, Exception]] = []
        self.adds: List[MemoryDiffEntry] = []
        self.updates: List[MemoryDiffEntry] = []
        self.deletes: List[MemoryDeleteEntry] = []

    def add_written(self, uri: str, before: str = "", after: str = "") -> None:
        self.written_uris.append(uri)
        self.adds.append(MemoryDiffEntry(uri, before=before, after=after))

    def add_edited(self, uri: str, before: str = "", after: str = "") -> None:
        self.edited_uris.append(uri)
        self.updates.append(MemoryDiffEntry(uri, before=before, after=after))

    def add_deleted(self, uri: str, deleted_content: str = "") -> None:
        self.deleted_uris.append(uri)
        self.deletes.append(MemoryDeleteEntry(uri, deleted_content=deleted_content))

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
        operations: Any,
        ctx: RequestContext,
        extract_context: Any = None,
    ) -> MemoryUpdateResult:
        """
        Apply MemoryOperations directly using the flat model format.

        This is the system executor - no LLM involved at this stage.

        Args:
            operations: StructuredMemoryOperations from LLM with per-memory_type fields (e.g., soul, identity)
            ctx: Request context
            extract_context: Optional context for template rendering

        Returns:
            MemoryUpdateResult with changes made
        """
        result = MemoryUpdateResult()
        viking_fs = self._get_viking_fs()

        if not viking_fs:
            logger.warning("VikingFS not available, skipping memory operations")
            return result

        if not self._registry:
            raise ValueError("MemoryTypeRegistry is required for URI resolution")

        # Get actual user/agent space from ctx
        user_space = user_space_fragment(ctx) if ctx and ctx.user else "default"
        agent_space = agent_space_fragment(ctx) if ctx and ctx.user else "default"

        # Resolve all URIs first (pass extract_context for template rendering)
        resolved_ops = resolve_all_operations(
            operations,
            self._registry,
            user_space=user_space,
            agent_space=agent_space,
            extract_context=extract_context,
        )

        if resolved_ops.has_errors():
            for error in resolved_ops.errors:
                result.add_error("unknown", ValueError(error))
            return result

        # Apply unified operations - _apply_edit returns (is_edited, before_content, after_content)
        for resolved_op in resolved_ops.operations:
            try:
                is_edited, before_content, after_content = await self._apply_edit(
                    resolved_op.model,
                    resolved_op.uri,
                    ctx,
                    extract_context=extract_context,
                    memory_type=resolved_op.memory_type,
                )
                if is_edited:
                    result.add_edited(
                        resolved_op.uri,
                        before=before_content,
                        after=after_content,
                    )
                else:
                    result.add_written(
                        resolved_op.uri,
                        after=after_content,
                    )
            except Exception as e:
                tracer.error(
                    f"Failed to apply operation: {e}, op={resolved_op.model}, op type={type(resolved_op.model)}",
                    e,
                )
                if hasattr(resolved_op.model, "model_dump"):
                    tracer.info(f"Op dump: {resolved_op.model.model_dump()}")
                result.add_error(resolved_op.uri, e)

        # Apply delete operations
        for _uri_str, uri in resolved_ops.delete_operations:
            try:
                deleted_content = await self._apply_delete(uri, ctx)
                result.add_deleted(uri, deleted_content=deleted_content)
            except Exception as e:
                tracer.error(f"Failed to delete memory {uri}", e)
                result.add_error(uri, e)

        # Vectorize written and edited memories
        await self._vectorize_memories(result, ctx)

        tracer.info(f"Memory operations applied: {result.summary()}")

        # Generate overview files for directories that have modified files
        # Include deleted_uris to handle cleanup when files are removed
        all_modified_uris = result.written_uris + result.edited_uris + result.deleted_uris
        if all_modified_uris:
            # Collect unique directories with their memory types
            dir_to_memory_type = {}
            for uri in all_modified_uris:
                # Extract directory path (remove the filename)
                if "/" in uri:
                    dir_path = "/".join(uri.split("/")[:-1])
                    # Find which memory type this directory belongs to
                    for schema in self._registry.list_all():
                        if schema.overview_template and schema.directory:
                            env = jinja2.Environment(autoescape=False)
                            base_dir = env.from_string(schema.directory).render(
                                user_space=user_space,
                                agent_space=agent_space,
                            )
                            # Check if this uri belongs to this memory type's directory
                            if dir_path.startswith(base_dir.rstrip("/")):
                                # Use the directory containing the file directly
                                if dir_path not in dir_to_memory_type:
                                    dir_to_memory_type[dir_path] = schema.memory_type

            # Generate overview for each unique directory
            for directory, memory_type in dir_to_memory_type.items():
                logger.info(
                    f"[apply_operations] Generating overview for {memory_type} at {directory}"
                )
                await self.generate_overview(memory_type, directory, ctx, extract_context)

        return result

    async def _apply_edit(
        self,
        flat_model: Any,
        uri: str,
        ctx: RequestContext,
        extract_context: Any = None,
        memory_type: str = None,
    ) -> Tuple[bool, str, str]:
        """Apply edit operation from a flat model.

        Returns:
            Tuple of (file_existed, before_content, after_content).
            before_content is the previous plain content (empty for new files).
            after_content is the new plain content.
        """
        viking_fs = self._get_viking_fs()

        # Convert flat model to dict first (needed for checking content type)
        model_dict = flat_model_to_dict(flat_model)

        # Get memory type schema - use parameter first, then fallback to model_dict
        memory_type_str = memory_type or model_dict.get("memory_type")

        # Read current memory (or use empty if not found)
        current_full_content = ""
        before_plain_content = ""
        file_existed = True
        try:
            current_full_content = await viking_fs.read_file(uri, ctx=ctx) or ""
            before_plain_content, _ = deserialize_full(current_full_content)
        except NotFoundError:
            file_existed = False

        # Deserialize content and metadata
        current_plain_content, current_metadata = deserialize_full(current_full_content)
        metadata = current_metadata or {}

        # Get schema
        field_schema_map: Dict[str, MemoryField] = {}
        if self._registry and memory_type_str:
            schema = self._registry.get(memory_type_str)
            if schema:
                field_schema_map = {f.name: f for f in schema.fields}

        # Build metadata by applying merge_op to each field
        # (merge_op.apply handles current_value=None case for new files)
        metadata: Dict[str, Any] = {}
        for field_name, field_schema in field_schema_map.items():
            if field_name in model_dict:
                patch_value = model_dict[field_name]
                # Get current value
                if field_name == "content":
                    current_value = current_plain_content
                else:
                    current_value = metadata.get(field_name)
                # Use merge_op to process field value
                merge_op = MergeOpFactory.from_field(field_schema)
                new_value = merge_op.apply(current_value, patch_value)
                metadata[field_name] = new_value

        # Serialize and write (template rendering is handled inside serialize_with_metadata)
        content_template = None
        if self._registry and memory_type_str:
            schema = self._registry.get(memory_type_str)
            if schema:
                content_template = schema.content_template

        # serialize_with_metadata modifies metadata dict, so pass a copy
        new_full_content = serialize_with_metadata(
            metadata.copy(),
            content_template=content_template,
            extract_context=extract_context,
        )

        if file_existed:
            self._print_diff(uri, current_plain_content, new_full_content)

        await viking_fs.write_file(uri, new_full_content, ctx=ctx)

        # Extract after plain content for memory_diff
        after_plain_content, _ = deserialize_full(new_full_content)
        return file_existed, before_plain_content, after_plain_content

    async def _apply_delete(self, uri: str, ctx: RequestContext) -> str:
        """Apply delete operation (uri is already a string).

        Returns:
            The plain content that was deleted (empty string if not found).
        """
        viking_fs = self._get_viking_fs()

        # Read content before deletion for memory_diff
        deleted_plain_content = ""
        try:
            raw_content = await viking_fs.read_file(uri, ctx=ctx) or ""
            deleted_plain_content, _ = deserialize_full(raw_content)
        except NotFoundError:
            pass
        except Exception:
            pass

        # Delete from VikingFS
        # VikingFS automatically handles vector index cleanup
        try:
            await viking_fs.rm(uri, recursive=False, ctx=ctx)
        except NotFoundError:
            tracer.error(f"Memory not found for delete: {uri}")
            # Idempotent - deleting non-existent file succeeds

        return deleted_plain_content

    def _print_diff(self, uri: str, old_content: str, new_content: str) -> None:
        """Print a diff of the memory edit using diff_match_patch."""
        try:
            from diff_match_patch import diff_match_patch

            dmp = diff_match_patch()

            # Compute character-level diff
            diffs = dmp.diff_main(old_content, new_content)
            dmp.diff_cleanupSemantic(diffs)

            # Build formatted output
            lines = []
            lines.append(f"\n{'=' * 60}")
            lines.append(f"MEMORY EDIT: {uri}")
            lines.append(f"{'=' * 60}")

            # ANSI styles
            STYLE_DELETE = "\033[9m\033[31m"  # 删除线 + 红色
            STYLE_INSERT = "\033[32m"  # 绿色
            STYLE_RESET = "\033[0m"

            for op, text in diffs:
                if op == 0:  # Equal - 正常显示
                    lines.append(text)
                elif op == -1:  # Delete - 红色删除线
                    lines.append(f"{STYLE_DELETE}{text}{STYLE_RESET}")
                elif op == 1:  # Insert - 绿色高亮
                    lines.append(f"{STYLE_INSERT}{text}{STYLE_RESET}")

            lines.append(f"{'=' * 60}\n")

            # Print directly
            tracer.info("diff=" + "\n".join(lines))
        except ImportError:
            # Fallback: just show file name
            tracer.error(f"diff_match_patch not available, skipping diff for {uri}")
        except Exception as e:
            tracer.error(f"Failed to print diff for {uri}: {e}")

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
        uris_to_vectorize = []
        for uri in result.written_uris + result.edited_uris:
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

        tracer.info(
            f"[generate_overview] Called with memory_type={memory_type}, directory={directory}"
        )

        # Get the schema for this memory type
        registry = self._registry
        tracer.info(f"[generate_overview] registry={registry}")
        schema = registry.get(memory_type)
        tracer.info(
            f"[generate_overview] schema={schema}, overview_template={schema.overview_template if schema else None}"
        )
        if not schema or not schema.overview_template:
            logger.debug(f"No overview_template for memory type: {memory_type}")
            return

        viking_fs = self._get_viking_fs()

        # List direct .md files in the directory (excluding .overview.md and .abstract.md)
        try:
            # Use ls to list direct children
            entries = await viking_fs.ls(directory, show_all_hidden=True, ctx=ctx)
            tracer.info(f"[generate_overview] LS entries in {directory}: {entries}")

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

            tracer.info(f"[generate_overview] Filtered md_files: {md_files}")
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
                tracer.info(
                    f"[generate_overview] Parsed {file_path}: {parsed.keys() if parsed else None}"
                )

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

        tracer.info(f"[generate_overview] Total items: {len(items)}")
        if not items:
            logger.debug(f"No valid memory files parsed in {directory}")
            return

        # Render the template
        try:
            env = jinja2.Environment(autoescape=False)
            template = env.from_string(schema.overview_template)
            rendered = template.render(
                memory_type=memory_type,
                items=items,
                extract_context=extract_context,
            )
            tracer.info(f"[generate_overview] Rendered overview length: {len(rendered)}")
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
