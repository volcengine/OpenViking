# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0
"""
Memory updater - applies MemoryOperations directly.

This is the system executor that applies LLM's final output (MemoryOperations)
to the storage system.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any, Dict, List, Optional, Tuple

if TYPE_CHECKING:
    from openviking.session.memory.memory_isolation_handler import MemoryIsolationHandler

from openviking.message import Message
from openviking.message.part import TextPart
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
from openviking.session.memory.utils.resource_refs import (
    RESOURCE_REF_SOURCE_SESSION_COMMIT,
    sync_memory_resource_refs,
)
from openviking.session.memory.utils.template_utils import TemplateUtils
from openviking.session.memory.utils.uri import render_template
from openviking.storage.viking_fs import get_viking_fs
from openviking.telemetry import tracer
from openviking.telemetry.request_wait_tracker import get_request_wait_tracker
from openviking.utils.time_utils import parse_iso_datetime
from openviking_cli.exceptions import NotFoundError
from openviking_cli.utils import VikingURI, get_logger

logger = get_logger(__name__)

_EXTRACTION_CHUNK_MIN_CHARS = 100
_EXTRACTION_CHUNK_BOUNDARY_RE = re.compile(r"(\n+|[。！？；!?;]+|(?<!\d)\.(?!\d))")
_RESOURCE_ADDITION_FIELD_RE = re.compile(
    r"^(Resource URI|Source name|Added at|Resource abstract|User reason):\s*(.*)$",
    re.MULTILINE,
)
_RESOURCE_URI_MARKER_RE = re.compile(
    r"[，,；;：:\s]*(?:资源\s*URI\s*为|资源\s*URI|Resource\s+URI)\s*[:：为]?\s*",
    re.IGNORECASE,
)


@dataclass(frozen=True)
class ChunkMeta:
    """Metadata for a derived extraction chunk message."""

    source_message_id: str
    chunk_index: int
    chunk_count: int


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

    def __init__(self, messages: List[Message], chunk_meta: Optional[Dict[int, ChunkMeta]] = None):
        if chunk_meta is None:
            self.messages, self.chunk_meta = self._build_extraction_messages(messages)
        else:
            self.messages = messages
            self.chunk_meta = chunk_meta
        self.page_id_map = PageIdMap()

    @classmethod
    def _build_extraction_messages(
        cls, messages: List[Message]
    ) -> Tuple[List[Message], Dict[int, ChunkMeta]]:
        """Build messages used by memory extraction.

        Long text-only messages are split into derived chunks so event `ranges`
        can point to a narrower source span without relying on brittle text
        matching. The original session messages are not modified.
        """
        extraction_messages: List[Message] = []
        chunk_meta: Dict[int, ChunkMeta] = {}
        for message in messages:
            for extraction_message, meta in cls._split_message_for_extraction(message):
                extraction_messages.append(extraction_message)
                if meta is not None:
                    chunk_meta[id(extraction_message)] = meta
        return extraction_messages, chunk_meta

    @classmethod
    def _split_message_for_extraction(
        cls, message: Message
    ) -> List[Tuple[Message, Optional[ChunkMeta]]]:
        parts = getattr(message, "parts", [])
        if not parts or not all(isinstance(part, TextPart) for part in parts):
            return [(message, None)]

        text = "".join(part.text for part in parts)
        chunks = cls._split_text_for_extraction(text)
        if len(chunks) <= 1:
            return [(message, None)]

        chunk_messages = []
        for idx, chunk in enumerate(chunks):
            chunk_message = Message(
                id=f"{message.id}#chunk_{idx}",
                role=message.role,
                peer_id=getattr(message, "peer_id", None),
                parts=[TextPart(chunk)],
                created_at=message.created_at,
            )
            chunk_messages.append(
                (
                    chunk_message,
                    ChunkMeta(
                        source_message_id=message.id,
                        chunk_index=idx,
                        chunk_count=len(chunks),
                    ),
                )
            )
        return chunk_messages

    @classmethod
    def _split_text_for_extraction(cls, text: str) -> List[str]:
        return cls._pack_text_units(cls._split_text_units(text)) or [text]

    @staticmethod
    def _pack_text_units(units: List[str]) -> List[str]:
        chunks: List[str] = []
        current = ""
        for unit in units:
            current += unit
            if len(current) < _EXTRACTION_CHUNK_MIN_CHARS:
                continue
            chunks.append(current)
            current = ""

        if current:
            if chunks:
                chunks[-1] += current
            else:
                chunks.append(current)
        return chunks

    @staticmethod
    def _split_text_units(text: str) -> List[str]:
        pieces = _EXTRACTION_CHUNK_BOUNDARY_RE.split(text)
        units: List[str] = []
        current = ""
        for piece in pieces:
            if not piece:
                continue
            current += piece
            if _EXTRACTION_CHUNK_BOUNDARY_RE.fullmatch(piece):
                units.append(current)
                current = ""
        if current:
            units.append(current)
        return units or [text]

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

    def get_resource_event_content(self, ranges_str: str, summary: str) -> str:
        """Return a user-readable event body for add-resource derived events."""
        if not ranges_str:
            return ""
        additions = self._resource_additions_from_ranges(ranges_str)
        if not additions:
            return ""
        addition = additions[0]
        resource_uri = addition.get("Resource URI", "")
        if not resource_uri:
            return ""
        return self._link_resource_summary(summary or "", resource_uri, addition).strip()

    def _resource_additions_from_ranges(self, ranges_str: str) -> List[Dict[str, str]]:
        msg_range = self.read_message_ranges(ranges_str)
        additions: List[Dict[str, str]] = []
        for msg_group in msg_range.elements:
            for msg in msg_group:
                text = self._message_text(msg)
                if "## Resource Addition" not in text:
                    continue
                fields = {
                    match.group(1): match.group(2).strip()
                    for match in _RESOURCE_ADDITION_FIELD_RE.finditer(text)
                }
                if fields.get("Resource URI"):
                    additions.append(fields)
        return additions

    @staticmethod
    def _message_text(message: Message) -> str:
        parts = getattr(message, "parts", [])
        texts = [part.text for part in parts if isinstance(part, TextPart) and part.text]
        if texts:
            return "\n".join(texts)
        return message.content or ""

    @classmethod
    def _link_resource_summary(
        cls,
        summary: str,
        resource_uri: str,
        addition: Dict[str, str],
    ) -> str:
        text = (summary or "").strip()
        if not text:
            return cls._resource_addition_fallback_sentence(resource_uri, addition)
        if f"]({resource_uri})" in text:
            return text
        if resource_uri in text:
            return cls._replace_bare_resource_uri(text, resource_uri, addition)
        label = cls._resource_label_from_addition(addition)
        return cls._finish_sentence(f"{text.rstrip('。.!')}，关联资源为[{label}]({resource_uri})")

    @classmethod
    def _replace_bare_resource_uri(
        cls,
        text: str,
        resource_uri: str,
        addition: Dict[str, str],
    ) -> str:
        uri_start = text.find(resource_uri)
        if uri_start < 0:
            return text
        prefix = text[:uri_start]
        suffix = text[uri_start + len(resource_uri) :]
        marker = _RESOURCE_URI_MARKER_RE.search(prefix)
        if marker:
            visible_prefix = prefix[: marker.start()].rstrip("，,；;：: ")
            label = cls._resource_clause_from_summary_prefix(visible_prefix)
            if not label:
                label = cls._resource_label_from_addition(addition)
            if label and visible_prefix.endswith(label):
                visible_prefix = visible_prefix[: -len(label)] + f"[{label}]({resource_uri})"
            else:
                visible_prefix = f"{visible_prefix}[{label}]({resource_uri})"
            return cls._finish_sentence(visible_prefix)

        label = cls._resource_label_from_addition(addition)
        return cls._finish_sentence(f"{prefix.rstrip()}[{label}]({resource_uri}){suffix.strip()}")

    @staticmethod
    def _resource_clause_from_summary_prefix(prefix: str) -> str:
        text = prefix.strip("，,；;：: ")
        tail = re.split(r"[，,；;。.!?？]", text)[-1].strip()
        return tail if 0 < len(tail) <= 120 else ""

    @classmethod
    def _resource_label_from_addition(cls, addition: Dict[str, str]) -> str:
        reason = addition.get("User reason", "").strip()
        for prefix in ("这是一张", "这是一个", "该资源是", "这个是", "这是"):
            if reason.startswith(prefix):
                reason = reason[len(prefix) :].strip()
                break
        reason = reason.strip("。.!！ ")
        if reason:
            return reason[:80]
        source_name = addition.get("Source name", "").strip()
        return source_name or "相关资源"

    @classmethod
    def _resource_addition_fallback_sentence(
        cls,
        resource_uri: str,
        addition: Dict[str, str],
    ) -> str:
        label = cls._resource_label_from_addition(addition)
        return f"用户保存了[{label}]({resource_uri})。"

    @staticmethod
    def _finish_sentence(text: str) -> str:
        text = text.strip("，,；;：: ")
        if text.endswith(("。", ".", "！", "!", "？", "?")):
            return text
        return text + "。"

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

        return MessageRange(elements, chunk_meta=self.chunk_meta)


class MessageRange:
    """Represents a range of messages for formatting."""

    def __init__(
        self,
        elements: List[List[Message]],
        chunk_meta: Optional[Dict[int, ChunkMeta]] = None,
    ):
        self.elements = elements
        self.chunk_meta = chunk_meta or {}

    def pretty_print(self) -> str:
        """Pretty print the message range with '...' separator between non-contiguous ranges."""
        result = []
        for i, msg_group in enumerate(self.elements):
            result.extend(self._format_contiguous_group(msg_group))
            # Add "..." separator between non-contiguous message groups
            if i < len(self.elements) - 1:
                result.append("...")
        return "\n".join(result)

    def _format_contiguous_group(self, msg_group: List[Message]) -> List[str]:
        formatted = []
        current_messages: List[Message] = []

        def flush_current() -> None:
            nonlocal current_messages
            if not current_messages:
                return
            content = self._format_merged_content(current_messages)
            formatted.append(f"[{self._speaker_for(current_messages[0])}]: {content}")
            current_messages = []

        for msg in msg_group:
            if current_messages and not self._can_merge_messages(current_messages[-1], msg):
                flush_current()
            current_messages.append(msg)

        flush_current()
        return formatted

    @staticmethod
    def _speaker_for(message: Message) -> str:
        return getattr(message, "peer_id", None) or message.role

    def _can_merge_messages(self, previous: Message, current: Message) -> bool:
        previous_meta = self._chunk_meta_for(previous)
        current_meta = self._chunk_meta_for(current)
        if previous_meta is None or current_meta is None:
            return False
        if self._speaker_for(previous) != self._speaker_for(current):
            return False
        return (
            previous_meta.source_message_id == current_meta.source_message_id
            and current_meta.chunk_index == previous_meta.chunk_index + 1
        )

    def _format_merged_content(self, messages: List[Message]) -> str:
        content = "".join((msg.content or "") for msg in messages)
        if not messages or not self._contains_chunk_message(messages):
            return content

        first_chunk = self._chunk_meta_for(messages[0])
        if first_chunk is not None and first_chunk.chunk_index > 0:
            content = "..." + content.lstrip()
        last_chunk = self._chunk_meta_for(messages[-1])
        if last_chunk is not None and last_chunk.chunk_index < last_chunk.chunk_count - 1:
            content = content.rstrip() + "..."
        return content

    def _contains_chunk_message(self, messages: List[Message]) -> bool:
        return any(self._chunk_meta_for(msg) is not None for msg in messages)

    def _chunk_meta_for(self, message: Message) -> Optional[ChunkMeta]:
        return self.chunk_meta.get(id(message))

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

    @classmethod
    async def refresh_schema_overview(
        cls,
        *,
        viking_fs: Any,
        directory_uri: str,
        ctx: RequestContext,
    ) -> None:
        memory_type = cls.memory_type_from_uri(directory_uri)
        if not memory_type:
            return
        try:
            from openviking.session.memory.memory_type_registry import create_default_registry

            updater = cls(registry=create_default_registry())
            updater._viking_fs = viking_fs
            await updater.generate_overview(memory_type, directory_uri, ctx)
        except Exception:
            logger.warning(
                "Failed to refresh memory overview for %s",
                directory_uri,
                exc_info=True,
            )

    @classmethod
    async def refresh_file_embedding(
        cls,
        *,
        viking_fs: Any,
        vikingdb: Any,
        uri: str,
        memory_type: Optional[str],
        ctx: RequestContext,
    ) -> bool:
        if not vikingdb or not bool(getattr(vikingdb, "has_queue_manager", False)):
            return False
        try:
            from openviking.session.memory.memory_type_registry import create_default_registry

            result = MemoryUpdateResult()
            result.add_written(uri)
            updater = cls(registry=create_default_registry(), vikingdb=vikingdb)
            updater._viking_fs = viking_fs
            attempted = await updater._vectorize_memories(
                result,
                ctx,
                uri_memory_type_map={uri: memory_type} if memory_type else {},
            )
            return attempted > 0
        except Exception:
            logger.warning("Failed to refresh memory embedding for %s", uri, exc_info=True)
            return False

    @staticmethod
    def memory_type_from_uri(uri: str) -> Optional[str]:
        parts = [part for part in VikingURI(uri).full_path.split("/") if part]
        try:
            memories_idx = parts.index("memories")
        except ValueError:
            return None
        if len(parts) <= memories_idx + 1:
            return None
        return parts[memories_idx + 1]

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

        await self._sync_resource_refs_for_result(result, ctx)

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

    async def _sync_resource_refs_for_result(
        self,
        result: MemoryUpdateResult,
        ctx: RequestContext,
    ) -> None:
        """Synchronize resource refs for memory files touched by session extraction."""
        viking_fs = self._get_viking_fs()
        deleted_uris = set(result.deleted_uris)
        for uri in dict.fromkeys(result.written_uris + result.edited_uris):
            if (
                uri in deleted_uris
                or uri.endswith("/.overview.md")
                or uri.endswith("/.abstract.md")
            ):
                continue
            try:
                raw = await viking_fs.read_file(uri, ctx=ctx)
                mf = MemoryFileUtils.read(raw, uri=uri)
                changed = sync_memory_resource_refs(
                    mf,
                    source=RESOURCE_REF_SOURCE_SESSION_COMMIT,
                )
                if changed:
                    await viking_fs.write_file(uri, MemoryFileUtils.write(mf), ctx=ctx)
            except Exception as exc:
                logger.warning("Failed to sync resource refs for %s: %s", uri, exc)

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
        from openviking.core.namespace import context_type_for_uri

        upserted_uris = set(result.written_uris + result.edited_uris)
        non_memory_endpoints = {
            uri
            for link in resolved_links
            for uri in (link.from_uri, link.to_uri)
            if context_type_for_uri(uri) != "memory"
        }
        skip = upserted_uris | (deleted_uris or set()) | non_memory_endpoints
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
    ) -> int:
        """Vectorize written and edited memory files.

        Args:
            result: MemoryUpdateResult with written_uris and edited_uris
            ctx: Request context
            extract_context: Extract context for embedding template rendering
            uri_memory_type_map: Mapping from URI to memory_type
        """
        if not self._vikingdb:
            logger.debug("VikingDB not available, skipping vectorization")
            return 0

        uri_memory_type_map = uri_memory_type_map or {}
        viking_fs = self._get_viking_fs()
        request_wait_tracker = get_request_wait_tracker()
        attempted_count = 0

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
            return 0

        for uri in uris_to_vectorize:
            try:
                # Read the memory file to get content
                content = await viking_fs.read_file(uri, ctx=ctx) or ""

                mf = MemoryFileUtils.read(content, uri=uri)
                from openviking.session.memory.utils.link_renderer import LinkRenderer

                abstract = LinkRenderer.strip_all_links(mf.content or "")
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
                    attempted_count += 1
                    try:
                        enqueued = await self._vikingdb.enqueue_embedding_msg(embedding_msg)
                    except Exception as e:
                        if embedding_msg.telemetry_id:
                            request_wait_tracker.mark_embedding_failed(
                                embedding_msg.telemetry_id,
                                embedding_msg.id,
                                str(e),
                            )
                        raise
                    if not enqueued and embedding_msg.telemetry_id:
                        request_wait_tracker.mark_embedding_failed(
                            embedding_msg.telemetry_id,
                            embedding_msg.id,
                            "embedding enqueue returned false",
                        )
                    logger.debug(f"Enqueued memory for vectorization: {uri}")

            except Exception as e:
                tracer.error(f"Failed to vectorize memory {uri}: {e}")
        return attempted_count

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

        except (NotFoundError, FileNotFoundError):
            logger.debug("Skip overview generation for deleted directory: %s", directory)
            return
        except Exception as e:
            tracer.error(f"Failed to list files in {directory}: {e}")
            return

        # If no memory files, delete the .overview.md and the directory if empty
        if not md_files:
            overview_path = f"{directory.rstrip('/')}/.overview.md"
            can_delete_directory = all(
                entry.get("name", "") in {"", ".overview.md"} for entry in entries
            )
            try:
                await viking_fs.rm(overview_path, recursive=False, ctx=ctx)
            except Exception:
                pass
            # Try to delete empty directory
            if can_delete_directory:
                try:
                    await viking_fs.rm(directory, recursive=True, ctx=ctx)
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
                metadata = mf.to_metadata()

                items.append(
                    {
                        "file_name": filename,
                        "file_content": metadata,
                    }
                )
            except Exception as e:
                tracer.error(f"Failed to parse {file_path}: {e}")
                continue

        if not items:
            logger.debug(f"No valid memory files parsed in {directory}")
            return

        overview_context = {
            "memory_type": memory_type,
            "directory_name": directory.rstrip("/").split("/")[-1],
            "items": items,
        }

        # Render the template
        try:
            rendered = render_template(
                schema.overview_template,
                overview_context,
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
