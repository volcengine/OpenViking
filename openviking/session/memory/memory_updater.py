# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0
"""
Memory updater - applies MemoryOperations directly.

This is the system executor that applies LLM's final output (MemoryOperations)
to the storage system.
"""

from __future__ import annotations

import re
import secrets
import string
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import TYPE_CHECKING, Any, Dict, List, Optional, Tuple

if TYPE_CHECKING:
    from openviking.session.memory.memory_isolation_handler import MemoryIsolationHandler

from openviking.core.context import ContextLevel
from openviking.message import Message
from openviking.message.part import TextPart
from openviking.pyagfs.exceptions import AGFSNotFoundError
from openviking.server.error_mapping import is_not_found_error
from openviking.server.identity import RequestContext
from openviking.session.memory.case_aggregation import (
    CASE_MEMORY_TYPE,
    PROPOSED_CASE_IDENTITY_FIELD,
)
from openviking.session.memory.dataclass import (
    MemoryFile,
    MemoryOperationSkipCode,
    MemoryTypeSchema,
    ResolvedOperation,
    ResolvedOperations,
    SkippedMemoryOperation,
    StoredLink,
)
from openviking.session.memory.experience_lifecycle import (
    experience_case_link_uris,
    experience_file_is_archived,
    experience_is_case_linkable,
)
from openviking.session.memory.memory_type_registry import MemoryTypeRegistry
from openviking.session.memory.merge_op import MergeOpFactory
from openviking.session.memory.page_id_map import PageIdMap
from openviking.session.memory.utils.memory_file_utils import (
    MemoryFileUtils,
    bump_memory_version,
    memory_version_from_fields,
    next_memory_version,
)
from openviking.session.memory.utils.resource_refs import (
    RESOURCE_REF_SOURCE_SESSION_COMMIT,
    sync_memory_resource_refs,
)
from openviking.session.memory.utils.template_utils import TemplateUtils
from openviking.session.memory.utils.uri import numbered_uri, render_template
from openviking.storage.abstract_overview import freshness_metadata, render_abstract_overview
from openviking.storage.viking_fs import get_viking_fs
from openviking.telemetry import tracer
from openviking.telemetry.request_wait_tracker import get_request_wait_tracker
from openviking.telemetry.tracer import get_trace_id
from openviking.utils.time_utils import parse_iso_datetime
from openviking_cli.exceptions import NotFoundError
from openviking_cli.utils import VikingURI, get_logger

logger = get_logger(__name__)

_MEMORY_ABSTRACT_MAX_BYTES = 50_000
_RANDOM_SUFFIX_ALPHABET = string.digits + string.ascii_letters
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


class MemoryVersionConflictError(RuntimeError):
    """A storage write was planned against a stale or unexpected version."""

    def __init__(
        self,
        uri: str,
        *,
        expected_version: int | None,
        actual_version: int | None,
        expected_absent: bool = False,
    ) -> None:
        expected = "absent" if expected_absent else str(expected_version)
        actual = "absent" if actual_version is None else str(actual_version)
        super().__init__(f"memory version conflict for {uri}: expected={expected}, actual={actual}")
        self.uri = uri
        self.expected_version = expected_version
        self.actual_version = actual_version
        self.expected_absent = expected_absent


@dataclass(frozen=True)
class ChunkMeta:
    """Metadata for a derived extraction chunk message."""

    source_message_id: str
    chunk_index: int
    chunk_count: int


def _collect_search_tags_by_uri(
    operations: "ResolvedOperations",
    caller_map: Optional[Dict[str, List[str]]] = None,
) -> Dict[str, List[str]]:
    """Build the per-URI transient search-tag map for vectorization.

    Combines an explicit caller-supplied map (e.g. trajectory outcome tags)
    with per-operation ``search_tags`` (e.g. event-memory custom scalars).
    The caller map wins on conflicts; per-op tags fill any remaining URIs.
    Operations with ``search_tags`` of ``None`` or ``[]`` contribute nothing.
    """
    result: Dict[str, List[str]] = {}
    for op in getattr(operations, "upsert_operations", []) or []:
        tags = getattr(op, "search_tags", None)
        if not tags:
            continue
        for uri in op.uris or []:
            if uri and uri not in result:
                result[uri] = list(tags)
    if caller_map:
        for uri, tags in caller_map.items():
            if uri:
                result[uri] = list(tags)
    return result


async def write_stored_links(
    links: List[StoredLink],
    ctx: RequestContext,
    viking_fs: Any,
    skip_uris: Optional[set] = None,
    lease_ref: Any = None,
    prefetched_files: Optional[Dict[str, MemoryFile]] = None,
) -> List[str]:
    """Write StoredLinks to their endpoint files' links/backlinks fields.

    For each link: from_uri's ``links`` receives the forward link;
    to_uri's ``backlinks`` receives the reverse reference.
    Files listed in skip_uris are skipped (caller handles them in the same write).
    Returns the endpoint URIs that were successfully rewritten.  Callers can
    use this to avoid reporting link-only edits for files that failed the
    read/modify/write step.
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

    updated_uris: List[str] = []
    for uri, link_groups in file_links.items():
        try:
            prefetched = (prefetched_files or {}).get(uri)
            if prefetched is not None:
                mf = prefetched.model_copy(deep=True)
            else:
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
            current_trace_id = get_trace_id()
            if current_trace_id:
                mf.extra_fields["last_update_trace_id"] = current_trace_id
            bump_memory_version(mf)
            await viking_fs.write_file(
                uri,
                MemoryFileUtils.write(mf),
                ctx=ctx,
                lease_ref=lease_ref,
            )
            updated_uris.append(uri)
        except Exception as e:
            tracer.error(f"Failed to apply links to {uri}: {e}")
    return updated_uris


def _remap_link_dict(link: Dict[str, Any], uri_remap: Dict[str, str]) -> Dict[str, Any]:
    remapped = dict(link or {})
    remapped["from_uri"] = _resolve_replacement_uri(remapped.get("from_uri"), uri_remap)
    remapped["to_uri"] = _resolve_replacement_uri(remapped.get("to_uri"), uri_remap)
    return remapped


def _resolve_replacement_uri(uri: str | None, uri_remap: Dict[str, str]) -> str | None:
    if not uri:
        return uri
    original_uri = uri
    seen: set[str] = set()
    while uri in uri_remap:
        if uri in seen:
            return original_uri
        seen.add(uri)
        replacement_uri = uri_remap[uri]
        if not replacement_uri:
            return uri
        uri = replacement_uri
    return uri


def remap_stored_links(links: List[StoredLink], uri_remap: Dict[str, str]) -> List[StoredLink]:
    if not links or not uri_remap:
        return list(links or [])
    remapped_links: List[StoredLink] = []
    for link in links:
        from_uri = _resolve_replacement_uri(link.from_uri, uri_remap)
        to_uri = _resolve_replacement_uri(link.to_uri, uri_remap)
        if from_uri == to_uri:
            continue
        remapped_links.append(link.model_copy(update={"from_uri": from_uri, "to_uri": to_uri}))
    return remapped_links


def _operation_trace_id(op: ResolvedOperation) -> str | None:
    source = getattr(op, "source", None)
    trace_id = getattr(source, "trace_id", None) if source else None
    if trace_id:
        return str(trace_id)
    fields = dict(getattr(op, "memory_fields", {}) or {})
    field_value = fields.get("last_update_trace_id") or fields.get("trace_id")
    if field_value:
        return str(field_value)
    current_trace_id = get_trace_id()
    return current_trace_id or None


def _schema_should_persist_content(schema: Any) -> bool:
    return bool(getattr(schema, "content_template", None)) and any(
        getattr(field, "name", None) == "content" for field in getattr(schema, "fields", [])
    )


def _strip_transient_memory_fields(
    metadata: Dict[str, Any],
    *,
    memory_type: str,
) -> None:
    """Remove operation-scoped fields before serializing persistent memory."""
    if memory_type == CASE_MEMORY_TYPE:
        metadata.pop(PROPOSED_CASE_IDENTITY_FIELD, None)


async def resolve_memory_fields(
    fields: Dict[str, Any],
    *,
    schema: MemoryTypeSchema,
    old_file: MemoryFile | None = None,
    uri: str | None = None,
) -> Dict[str, Any]:
    """Apply schema merge operations and preserve fields not changed by a patch."""
    incoming = dict(fields or {})
    resolved = dict(getattr(old_file, "extra_fields", {}) or {})
    schema_field_names = {field.name for field in schema.fields}
    resolved.update(
        {key: value for key, value in incoming.items() if key not in schema_field_names}
    )

    for field in schema.fields:
        if old_file is None:
            current_value = None
        elif field.name == "content":
            current_value = old_file.plain_content()
        else:
            current_value = old_file.extra_fields.get(field.name)

        if field.name not in incoming:
            if current_value is not None:
                resolved[field.name] = current_value
            continue

        try:
            resolved[field.name] = await MergeOpFactory.from_field(field).apply(
                current_value,
                incoming[field.name],
            )
        except Exception as exc:
            if uri is None:
                tracer.info(
                    "[memory_updater] Skipping preview field update after merge_op failure: "
                    f"memory_type={schema.memory_type}, field={field.name}, error={exc}"
                )
            else:
                tracer.info(
                    "[memory_updater] Skipping field update after merge_op failure: "
                    f"uri={uri}, field={field.name}, error={exc}"
                )
            if current_value is None:
                resolved.pop(field.name, None)
            else:
                resolved[field.name] = current_value

    return resolved


async def render_operation_after_file(
    op: ResolvedOperation,
    *,
    schema: MemoryTypeSchema,
    extract_context: Any = None,
) -> MemoryFile:
    """Render the post-operation MemoryFile using the registered schema template."""
    rendered = await render_operation_after_file_content(
        op,
        schema=schema,
        extract_context=extract_context,
    )
    old_file = getattr(op, "old_memory_file_content", None)
    uri = op.uris[0] if op.uris else getattr(old_file, "uri", None)
    return MemoryFileUtils.read(rendered, uri=uri)


async def render_operation_after_file_content(
    op: ResolvedOperation,
    *,
    schema: MemoryTypeSchema,
    extract_context: Any = None,
) -> str:
    """Serialize the post-operation memory file using the registered schema template."""
    old_file = getattr(op, "old_memory_file_content", None)
    metadata = await resolve_memory_fields(
        dict(getattr(op, "memory_fields", {}) or {}),
        schema=schema,
        old_file=old_file,
    )
    _strip_transient_memory_fields(metadata, memory_type=schema.memory_type)
    source = getattr(op, "source", None)
    source_extraction_id = getattr(source, "extraction_id", None) if source else None
    if source_extraction_id:
        metadata["source_extraction_id"] = str(source_extraction_id)
    source_trace_id = _operation_trace_id(op)
    if source_trace_id:
        metadata["last_update_trace_id"] = source_trace_id
    metadata["version"] = next_memory_version(old_file)
    metadata.setdefault("memory_type", op.memory_type)
    if old_file is not None:
        if old_file.links and "links" not in metadata:
            metadata["links"] = list(old_file.links)
        if old_file.backlinks and "backlinks" not in metadata:
            metadata["backlinks"] = list(old_file.backlinks)

    uri = op.uris[0] if op.uris else getattr(old_file, "uri", None)
    memory_file = MemoryFile.from_parsed(uri=uri, parsed=metadata)
    return MemoryFileUtils.write(
        memory_file,
        content_template=schema.content_template,
        extract_context=extract_context,
        persist_content=_schema_should_persist_content(schema),
    )


class ExtractContext:
    """Extract context for template rendering."""

    def __init__(
        self,
        messages: List[Message],
        chunk_meta: Optional[Dict[int, ChunkMeta]] = None,
        *,
        split_long_text_messages: bool = True,
    ):
        if chunk_meta is None:
            if split_long_text_messages:
                self.messages, self.chunk_meta = self._build_extraction_messages(messages)
            else:
                self.messages, self.chunk_meta = list(messages or []), {}
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

    def get_year(self, ranges_str: str) -> str:
        """根据 ranges 字符串获取第一条消息的年份，fallback 到当前年份"""
        from datetime import datetime

        if not ranges_str:
            return str(datetime.now().year)
        msg_range = self.read_message_ranges(ranges_str)
        first_time = msg_range._first_message_time()
        if first_time:
            return first_time.split("-")[0]
        return str(datetime.now().year)

    def get_month(self, ranges_str: str) -> str:
        """根据 ranges 字符串获取第一条消息的月份，fallback 到当前月份"""
        from datetime import datetime

        if not ranges_str:
            return f"{datetime.now().month:02d}"
        msg_range = self.read_message_ranges(ranges_str)
        first_time = msg_range._first_message_time()
        if first_time:
            return first_time.split("-")[1]
        return f"{datetime.now().month:02d}"

    def get_day(self, ranges_str: str) -> str:
        """根据 ranges 字符串获取第一条消息的日期，fallback 到当前日期"""
        from datetime import datetime

        if not ranges_str:
            return f"{datetime.now().day:02d}"
        msg_range = self.read_message_ranges(ranges_str)
        first_time = msg_range._first_message_time()
        if first_time:
            return first_time.split("-")[2]
        return f"{datetime.now().day:02d}"

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

    def _get_session_datetime(self) -> datetime:
        """取对话第一条有效消息的时间，fallback 到当前时间。"""
        for msg in self.messages:
            created_at = getattr(msg, "created_at", None)
            if created_at:
                try:
                    return datetime.fromisoformat(created_at)
                except (ValueError, TypeError):
                    continue
        return datetime.now()

    def get_session_year(self) -> str:
        """取对话第一条有效消息的年份（YYYY）。"""
        return self._get_session_datetime().strftime("%Y")

    def get_session_month(self) -> str:
        """取对话第一条有效消息的月份（MM）。"""
        return self._get_session_datetime().strftime("%m")

    def get_session_day(self) -> str:
        """取对话第一条有效消息的日期（DD）。"""
        return self._get_session_datetime().strftime("%d")

    def get_session_time(self) -> str:
        """取对话第一条有效消息的时间（HHMMSS）。"""
        return self._get_session_datetime().strftime("%H%M%S")

    def get_session_timestamp(self) -> str:
        """取对话第一条有效消息的紧凑时间戳（YYYYMMDDHHMMSS）。"""
        return self._get_session_datetime().strftime("%Y%m%d%H%M%S")

    def get_random_suffix(self, length: int) -> str:
        """Return a random Base62 suffix with the requested length."""
        return "".join(secrets.choice(_RANDOM_SUFFIX_ALPHABET) for _ in range(length))

    def get_event_content(
        self, ranges_str: str, summary: str | None, ratio_threshold: float = 0.2
    ) -> str:
        """根据原始消息与 summary 的字符数比例，决定返回原始消息还是摘要。"""
        if not ranges_str:
            return summary or ""
        msg_range = self.read_message_ranges(ranges_str)
        original = msg_range.pretty_print()
        if not summary or not summary.strip():
            return original or ""
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
            formatted.append(f"**{self._speaker_for(current_messages[0])}**: {content}")
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
        content = "".join((self._message_content(msg) or "") for msg in messages)
        if not messages or not self._contains_chunk_message(messages):
            return content

        first_chunk = self._chunk_meta_for(messages[0])
        if first_chunk is not None and first_chunk.chunk_index > 0:
            content = "..." + content.lstrip()
        last_chunk = self._chunk_meta_for(messages[-1])
        if last_chunk is not None and last_chunk.chunk_index < last_chunk.chunk_count - 1:
            content = content.rstrip() + "..."
        return content

    def _message_content(self, message: Message) -> str:
        texts: List[str] = []
        for part in getattr(message, "parts", []) or []:
            if isinstance(part, TextPart):
                texts.append(part.text or "")
        if texts:
            return "".join(texts)
        return getattr(message, "content", "") or ""

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
        self.archived_uris: List[str] = []
        self.skipped_operations: List[SkippedMemoryOperation] = []
        self.errors: List[Tuple[str, Exception]] = []
        # Parsed post-write files are execution-only.  Reuse them for resource
        # ref sync and vectorization instead of reading the same file again.
        self.files_by_uri: Dict[str, MemoryFile] = {}

    def add_written(self, uri: str) -> None:
        self.written_uris.append(uri)

    def add_edited(self, uri: str) -> None:
        self.edited_uris.append(uri)

    def add_deleted(self, uri: str) -> None:
        self.deleted_uris.append(uri)

    def add_archived(self, uri: str) -> None:
        if uri not in self.archived_uris:
            self.archived_uris.append(uri)

    def cache_file(self, uri: str, memory_file: MemoryFile) -> None:
        self.files_by_uri[uri] = memory_file

    def add_error(self, uri: str, error: Exception) -> None:
        self.errors.append((uri, error))

    def add_skipped(self, operation: SkippedMemoryOperation) -> None:
        self.skipped_operations.append(operation)

    def summary(self) -> str:
        return (
            f"Written: {len(self.written_uris)}, "
            f"Edited: {len(self.edited_uris)}, "
            f"Deleted: {len(self.deleted_uris)}, "
            f"Errors: {len(self.errors)}"
        )


def _same_batch_delete_conflict_key(uri: str) -> str:
    """Return a conservative key for detecting same-batch upsert/delete URI conflicts.

    Some local filesystems are case-insensitive.  Treat case-only URI variants as
    conflicting inside one apply batch so a loser delete cannot remove a winner
    upsert before vectorization.
    """

    return str(uri or "").rstrip("/").casefold()


class MemoryUpdater:
    """
    Applies MemoryOperations to storage.

    This is the system executor that directly applies the LLM's final output.
    No function calls are used for write/edit/delete - these are executed directly.
    """

    def __init__(
        self,
        registry: Optional[MemoryTypeRegistry] = None,
        vikingdb=None,
        transaction_handle: Any = None,
        defer_archived_vector_cleanup: bool = False,
    ):
        """Create a memory updater with an optional pathlock transaction handle."""
        self._viking_fs = None
        self._registry = registry
        self._vikingdb = vikingdb
        self._transaction_handle = transaction_handle
        self._defer_archived_vector_cleanup = defer_archived_vector_cleanup

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
        strict: bool = False,
    ) -> bool:
        memory_type = cls.memory_type_from_uri(directory_uri)
        if not memory_type:
            return False
        try:
            from openviking.session.memory.memory_type_registry import create_default_registry

            updater = cls(registry=create_default_registry())
            updater._viking_fs = viking_fs
            return await updater.generate_overview(memory_type, directory_uri, ctx)
        except Exception:
            logger.warning(
                "Failed to refresh memory overview for %s",
                directory_uri,
                exc_info=True,
            )
            if strict:
                raise
            return False

    @classmethod
    async def refresh_file_embedding(
        cls,
        *,
        viking_fs: Any,
        vikingdb: Any,
        uri: str,
        memory_type: Optional[str],
        ctx: RequestContext,
        strict: bool = False,
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
            if strict:
                raise
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
        search_tags_by_uri: Dict[str, List[str]] = None,
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

        self._convert_experience_deletes_to_archives(operations)

        await self._allocate_add_only_uris(
            operations,
            ctx,
            viking_fs,
        )
        applicable_upserts: List[ResolvedOperation] = []
        has_unresolved_upserts = False
        for resolved_op in operations.upsert_operations:
            if resolved_op.uris:
                applicable_upserts.append(resolved_op)
                continue
            has_unresolved_upserts = True
            error_target = f"{resolved_op.memory_type}(page_id={resolved_op.page_id})"
            resolution_skip = getattr(resolved_op, "resolution_skip", None)
            if resolution_skip is not None:
                # Reporting-only: the operation remains unresolved, preserving
                # the legacy delete-suppression behavior for direct mixed batches.
                skipped = SkippedMemoryOperation(
                    memory_type=resolved_op.memory_type,
                    page_id=resolved_op.page_id,
                    reason_code=resolution_skip.reason_code,
                    reason=resolution_skip.reason,
                    source=resolved_op.source,
                )
                result.add_skipped(skipped)
                message = (
                    "Skipping memory operation by resolution policy: "
                    f"memory_type={resolved_op.memory_type} "
                    f"page_id={resolved_op.page_id} "
                    f"reason_code={resolution_skip.reason_code.value}"
                )
                if resolution_skip.reason_code in {
                    MemoryOperationSkipCode.INVALID_PEER_ID,
                    MemoryOperationSkipCode.INVALID_RANGES,
                }:
                    logger.warning(message)
                else:
                    tracer.info(message)
                continue
            resolution_error = ValueError("Missing resolved URI")
            result.add_error(error_target, resolution_error)
            tracer.error(
                f"Skipping unresolved memory operation: {error_target}: {resolution_error}"
            )
        # Distribute resolved_links to corresponding upsert operations
        self._distribute_links_to_operations(operations)

        # Apply unified operations - _apply_edit returns True if edited, False if written
        for resolved_op in applicable_upserts:
            try:
                allocation_error = getattr(
                    resolved_op,
                    "_add_only_allocation_error",
                    None,
                )
                if allocation_error is not None:
                    raise allocation_error
                written_files = await self._apply_upsert(
                    resolved_op,
                    ctx,
                    extract_context=extract_context,
                    lease_ref=self._transaction_handle,
                )
                if self._transaction_handle is not None or resolved_op.precondition_files:
                    for uri, memory_file in (written_files or {}).items():
                        result.cache_file(uri, memory_file)
                # Add all uris to result (uris is List[str])
                if resolved_op.is_edit():
                    for uri in resolved_op.uris:
                        result.add_edited(uri)
                else:
                    for uri in resolved_op.uris:
                        result.add_written(uri)
                if resolved_op.lifecycle_action == "archive":
                    for uri in resolved_op.uris:
                        result.add_archived(uri)
            except Exception as e:
                tracer.error(
                    f"Failed to apply operation: op_type={type(resolved_op).__name__}, uris={resolved_op.uris}",
                    e,
                )
                for uri in resolved_op.uris:
                    result.add_error(uri, e)

        operations.resolved_links = remap_stored_links(
            list(getattr(operations, "resolved_links", []) or []),
            dict(getattr(operations, "delete_replacements", {}) or {}),
        )
        await self._inherit_deleted_link_relations(
            operations,
            result,
            ctx,
            lease_ref=self._transaction_handle,
        )

        # Apply delete operations (delete_file_contents is List[MemoryFile])
        # Skip deletes whose URI was just written in the same batch — this happens when the
        # LLM issues a Replace with the same experience_name (delete old + create same-name new),
        # which is semantically an Update. Executing the delete would remove the just-written file.
        upserted_uris = set(result.written_uris + result.edited_uris)
        upserted_uri_keys = {_same_batch_delete_conflict_key(uri) for uri in upserted_uris}
        for file_content in operations.delete_file_contents:
            delete_uri = file_content.uri
            if has_unresolved_upserts:
                delete_error = ValueError(
                    "Skipped delete because batch contains unresolved upsert URIs"
                )
                result.add_error(delete_uri, delete_error)
                tracer.error(f"Skipping delete for {delete_uri}: {delete_error}")
                continue
            if delete_uri in upserted_uris:
                tracer.info(
                    f"[apply_operations] skipping delete for {delete_uri}: "
                    "URI was upserted in the same batch (Replace-with-same-name treated as Update)"
                )
                continue
            if _same_batch_delete_conflict_key(delete_uri) in upserted_uri_keys:
                tracer.info(
                    f"[apply_operations] skipping delete for {delete_uri}: "
                    "URI case-conflicts with an upserted URI in the same batch"
                )
                continue
            try:
                await self._apply_delete(delete_uri, ctx, lease_ref=self._transaction_handle)
                result.add_deleted(delete_uri)
            except Exception as e:
                tracer.error(f"Failed to delete memory {delete_uri}", e)
                result.add_error(delete_uri, e)

        await self._sync_resource_refs_for_result(result, ctx, lease_ref=self._transaction_handle)

        # Vectorize written and edited memories
        uri_memory_type_map = {}
        for op in operations.upsert_operations:
            for uri in op.uris:
                uri_memory_type_map[uri] = op.memory_type
        # Merge caller-supplied transient tags with per-operation search_tags
        # (e.g. event-memory custom scalars) so both reach vectorization.
        effective_search_tags_by_uri = _collect_search_tags_by_uri(operations, search_tags_by_uri)
        await self._vectorize_memories(
            result,
            ctx,
            extract_context=extract_context,
            uri_memory_type_map=uri_memory_type_map,
            search_tags_by_uri=effective_search_tags_by_uri,
        )
        if not self._defer_archived_vector_cleanup:
            await self._remove_archived_vectors(result, ctx)

        # Apply links to endpoint files not covered by upsert_operations
        if operations.resolved_links:
            await self._apply_links_to_existing_files(
                operations.resolved_links,
                result,
                ctx,
                deleted_uris=set(result.deleted_uris),
                lease_ref=self._transaction_handle,
            )

        await self._unlink_archived_experience_cases(
            operations,
            result,
            ctx,
            lease_ref=self._transaction_handle,
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
            await self.generate_overview(
                memory_type,
                dir,
                ctx,
                extract_context,
                lease_ref=self._transaction_handle,
            )

        return result

    @staticmethod
    def _convert_experience_deletes_to_archives(operations: ResolvedOperations) -> None:
        """Keep Experience files and turn physical deletes into archive upserts."""

        upserted_uris = {
            uri
            for operation in operations.upsert_operations
            for uri in (operation.uris or [])
            if uri
        }
        remaining_deletes: list[MemoryFile] = []
        for old_file in operations.delete_file_contents:
            uri = str(old_file.uri or "")
            memory_type = str(
                old_file.memory_type
                or old_file.extra_fields.get("memory_type")
                or MemoryUpdater.memory_type_from_uri(uri)
                or ""
            )
            if memory_type != "experiences" or not uri:
                remaining_deletes.append(old_file)
                continue

            if uri in upserted_uris:
                # The existing successful path treats this as "upsert wins".
                # Drop the delete before execution so an upsert failure cannot
                # make an Experience fall through to physical deletion.
                operations.delete_replacements.pop(uri, None)
                continue

            replacement_uri = operations.delete_replacements.pop(uri, None)
            archive_fields = dict(old_file.extra_fields or {})
            archive_fields.update(
                {
                    "memory_type": "experiences",
                    "status": "archived",
                }
            )
            operations.upsert_operations.append(
                ResolvedOperation(
                    old_memory_file_content=old_file.model_copy(deep=True),
                    memory_fields=archive_fields,
                    memory_type="experiences",
                    uris=[uri],
                    expected_version=memory_version_from_fields(old_file.extra_fields),
                    lifecycle_action="archive",
                    archive_replacement_uri=replacement_uri,
                )
            )
            upserted_uris.add(uri)

        operations.delete_file_contents = remaining_deletes

    async def _sync_resource_refs_for_result(
        self,
        result: MemoryUpdateResult,
        ctx: RequestContext,
        lease_ref: Any = None,
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
                had_cached_file = uri in result.files_by_uri
                cached_file = result.files_by_uri.get(uri)
                if cached_file is not None:
                    mf = cached_file.model_copy(deep=True)
                else:
                    raw = await viking_fs.read_file(uri, ctx=ctx)
                    mf = MemoryFileUtils.read(raw, uri=uri)
                changed = sync_memory_resource_refs(
                    mf,
                    source=RESOURCE_REF_SOURCE_SESSION_COMMIT,
                )
                if changed:
                    rendered = MemoryFileUtils.write(mf)
                    await viking_fs.write_file(
                        uri,
                        rendered,
                        ctx=ctx,
                        lease_ref=lease_ref,
                    )
                    if had_cached_file:
                        result.cache_file(uri, MemoryFileUtils.read(rendered, uri=uri))
            except Exception as exc:
                logger.warning("Failed to sync resource refs for %s: %s", uri, exc)

    async def _allocate_add_only_uris(
        self,
        operations: ResolvedOperations,
        ctx: RequestContext,
        viking_fs: Any,
    ) -> None:
        add_only_operations = [
            operation
            for operation in operations.upsert_operations
            if getattr(self._registry.get(operation.memory_type), "operation_mode", None)
            == "add_only"
        ]
        if not add_only_operations:
            return

        batch_reservations: set[str] = set()
        uri_remap: Dict[str, str] = {}
        for operation in add_only_operations:
            allocated_uris: List[str] = []
            allocation_bases: Dict[str, str] = {}
            allocation_error: Exception | None = None
            for candidate_uri in operation.uris:
                canonical_uri = operation.add_only_uri_bases.get(
                    candidate_uri,
                    candidate_uri,
                )
                ordinal = 1
                while True:
                    allocated_uri = numbered_uri(canonical_uri, ordinal)
                    if allocated_uri in batch_reservations:
                        ordinal += 1
                        continue
                    try:
                        await viking_fs.stat(
                            allocated_uri,
                            ctx=ctx,
                            skip_count=True,
                        )
                    except (NotFoundError, FileNotFoundError, AGFSNotFoundError):
                        break
                    except Exception as exc:
                        allocation_error = exc
                        break
                    ordinal += 1
                if allocation_error is not None:
                    break
                batch_reservations.add(allocated_uri)
                allocated_uris.append(allocated_uri)
                allocation_bases[allocated_uri] = canonical_uri

            if allocation_error is not None:
                operation._add_only_allocation_error = allocation_error
                continue

            previous_uris = list(operation.uris)
            operation.uris = allocated_uris
            operation.add_only_uri_bases = allocation_bases
            operation.old_memory_file_content = None
            for previous_uri, allocated_uri in zip(
                previous_uris,
                allocated_uris,
                strict=True,
            ):
                if previous_uri != allocated_uri:
                    uri_remap.setdefault(previous_uri, allocated_uri)

        operations.resolved_links = remap_stored_links(
            list(getattr(operations, "resolved_links", []) or []),
            uri_remap,
        )

    async def _apply_upsert(
        self,
        resolved_op: ResolvedOperation,
        ctx: RequestContext,
        extract_context: Any = None,
        lease_ref: Any = None,
    ) -> Dict[str, MemoryFile]:
        """Apply upsert operation from a flat model."""
        viking_fs = self._get_viking_fs()
        written_files: Dict[str, MemoryFile] = {}

        memory_type = resolved_op.memory_type
        schema = self._registry.get(memory_type)
        # Process each URI independently
        for uri in resolved_op.uris:
            old_content: Optional[MemoryFile] = None
            if schema.operation_mode != "add_only":
                if uri in resolved_op.precondition_files:
                    # The policy updater read and validated this file while
                    # holding the same exact-batch lease.  Reusing it avoids a
                    # second downstream read without weakening CAS.
                    old_content = resolved_op.precondition_files[uri]
                else:
                    try:
                        content = await viking_fs.read_file(uri, ctx=ctx)
                        if content is not None:
                            old_content = MemoryFileUtils.read(content, uri=uri)
                    except Exception as exc:
                        strict_precondition_read = (
                            resolved_op.expected_version is not None
                            or resolved_op.expected_absent
                            or resolved_op.lifecycle_action == "archive"
                        )
                        if strict_precondition_read and not is_not_found_error(exc):
                            raise
                if (
                    old_content is None
                    and resolved_op.expected_version is None
                    and not resolved_op.expected_absent
                ):
                    old_content = resolved_op.old_memory_file_content

            self._validate_operation_precondition(resolved_op, uri, old_content)

            incoming_fields = dict(resolved_op.memory_fields)
            link_old_content = old_content
            if schema.memory_type == "experiences":
                if resolved_op.lifecycle_action == "archive":
                    incoming_fields["status"] = "archived"
                    archived_case_uris = (
                        old_content.extra_fields.get("archived_case_uris")
                        if old_content is not None
                        else []
                    )
                    if not isinstance(archived_case_uris, (list, tuple, set)):
                        archived_case_uris = []
                    existing_case_uris = {
                        str(case_uri) for case_uri in archived_case_uris if str(case_uri)
                    }
                    active_case_uris = experience_case_link_uris(
                        old_content.backlinks if old_content is not None else [],
                        experience_uri=uri,
                    )
                    archive_case_uris = sorted(existing_case_uris | active_case_uris)
                    resolved_op.archive_case_uris_by_uri[uri] = archive_case_uris
                    incoming_fields["archived_case_uris"] = archive_case_uris
                    if old_content is None or not old_content.extra_fields.get("archived_at"):
                        incoming_fields["archived_at"] = datetime.now(timezone.utc).isoformat()
                    incoming_fields["archive_reason"] = str(
                        incoming_fields.get("promotion_reason")
                        or incoming_fields.get("archive_reason")
                        or "superseded_or_obsolete"
                    )
                    if resolved_op.archive_replacement_uri:
                        incoming_fields["archive_replacement_uri"] = (
                            resolved_op.archive_replacement_uri
                        )
                    if old_content is not None:
                        link_old_content = old_content.model_copy(deep=True)
                        link_old_content.backlinks = [
                            link
                            for link in old_content.backlinks
                            if str(link.get("from_uri") or "") not in active_case_uris
                        ]

            metadata = await resolve_memory_fields(
                incoming_fields,
                schema=schema,
                old_file=old_content,
                uri=uri,
            )
            _strip_transient_memory_fields(metadata, memory_type=schema.memory_type)
            if (
                schema.memory_type == "experiences"
                and "trigger_code" not in resolved_op.memory_fields
            ):
                metadata.pop("trigger_code", None)
            source = getattr(resolved_op, "source", None)
            source_extraction_id = getattr(source, "extraction_id", None) if source else None
            if source_extraction_id:
                metadata["source_extraction_id"] = str(source_extraction_id)
            source_trace_id = _operation_trace_id(resolved_op)
            if source_trace_id:
                metadata["last_update_trace_id"] = source_trace_id
            metadata["version"] = next_memory_version(old_content)

            # Handle links/backlinks fields: merge with existing
            incoming_links_by_uri = getattr(resolved_op, "_incoming_links_by_uri", {})
            incoming_backlinks_by_uri = getattr(resolved_op, "_incoming_backlinks_by_uri", {})
            incoming_links = incoming_links_by_uri.get(uri, [])
            incoming_backlinks = incoming_backlinks_by_uri.get(uri, [])
            has_existing_links = link_old_content is not None
            if (
                incoming_links
                or incoming_backlinks
                or (has_existing_links and link_old_content.links)
                or (has_existing_links and link_old_content.backlinks)
            ):
                from openviking.session.memory.merge_op.link_merge import merge_links

                # Merge links
                existing_links = link_old_content.links if has_existing_links else []
                if incoming_links:
                    merged_links = merge_links(
                        existing_links,
                        [link.model_dump() for link in incoming_links],
                    )
                    metadata["links"] = merged_links
                elif existing_links:
                    metadata["links"] = existing_links

                # Merge backlinks
                existing_backlinks = link_old_content.backlinks if has_existing_links else []
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
                persist_content=_schema_should_persist_content(schema),
            )
            await viking_fs.write_file(
                uri,
                new_full_content,
                ctx=ctx,
                lease_ref=lease_ref,
            )
            written_files[uri] = MemoryFileUtils.read(new_full_content, uri=uri)
        return written_files

    @staticmethod
    def _validate_operation_precondition(
        resolved_op: ResolvedOperation,
        uri: str,
        old_content: MemoryFile | None,
    ) -> None:
        if resolved_op.expected_absent:
            if old_content is not None:
                raise MemoryVersionConflictError(
                    uri,
                    expected_version=None,
                    actual_version=memory_version_from_fields(old_content.extra_fields),
                    expected_absent=True,
                )
            return
        if resolved_op.expected_version is None:
            return
        actual_version = (
            memory_version_from_fields(old_content.extra_fields)
            if old_content is not None
            else None
        )
        if actual_version != resolved_op.expected_version:
            raise MemoryVersionConflictError(
                uri,
                expected_version=resolved_op.expected_version,
                actual_version=actual_version,
            )

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
        lease_ref: Any = None,
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
        await write_stored_links(
            resolved_links,
            ctx,
            viking_fs,
            skip_uris=skip,
            lease_ref=lease_ref,
        )

    async def _inherit_deleted_link_relations(
        self,
        operations: ResolvedOperations,
        result: MemoryUpdateResult,
        ctx: RequestContext,
        lease_ref: Any = None,
    ) -> None:
        uri_remap = dict(getattr(operations, "delete_replacements", {}) or {})
        if not uri_remap:
            return
        viking_fs = self._get_viking_fs()
        if not viking_fs:
            return

        from openviking.session.memory.merge_op.link_merge import merge_links

        inherited_by_uri: Dict[str, Dict[str, List[Dict[str, Any]]]] = {}
        for deleted_uri, replacement_uri in uri_remap.items():
            if not deleted_uri or not replacement_uri or deleted_uri == replacement_uri:
                continue
            try:
                content = await viking_fs.read_file(deleted_uri, ctx=ctx)
            except Exception as e:
                tracer.error(
                    f"Failed to read deleted memory links for replacement {deleted_uri}: {e}"
                )
                continue
            if not content:
                continue
            deleted_file = MemoryFileUtils.read(content, uri=deleted_uri)
            for link in list(deleted_file.links or []):
                remapped = _remap_link_dict(link, uri_remap)
                if remapped.get("from_uri") == remapped.get("to_uri"):
                    continue
                target_uri = remapped.get("from_uri")
                if target_uri:
                    inherited_by_uri.setdefault(target_uri, {"links": [], "backlinks": []})[
                        "links"
                    ].append(remapped)
                neighbor_uri = remapped.get("to_uri")
                if neighbor_uri and neighbor_uri not in uri_remap:
                    inherited_by_uri.setdefault(neighbor_uri, {"links": [], "backlinks": []})[
                        "backlinks"
                    ].append(remapped)
            for link in list(deleted_file.backlinks or []):
                remapped = _remap_link_dict(link, uri_remap)
                if remapped.get("from_uri") == remapped.get("to_uri"):
                    continue
                target_uri = remapped.get("to_uri")
                if target_uri:
                    inherited_by_uri.setdefault(target_uri, {"links": [], "backlinks": []})[
                        "backlinks"
                    ].append(remapped)
                neighbor_uri = remapped.get("from_uri")
                if neighbor_uri and neighbor_uri not in uri_remap:
                    inherited_by_uri.setdefault(neighbor_uri, {"links": [], "backlinks": []})[
                        "links"
                    ].append(remapped)

        written_or_edited = set(result.written_uris + result.edited_uris)
        stale_uris = set(uri_remap)
        for uri, link_groups in inherited_by_uri.items():
            if uri in uri_remap:
                continue
            if uri in written_or_edited:
                continue
            try:
                content = await viking_fs.read_file(uri, ctx=ctx)
                if not content:
                    continue
                mf = MemoryFileUtils.read(content, uri=uri)
                # Remapped links have different dedup keys, so remove the old
                # endpoints before merging to avoid retaining dangling aliases.
                mf.links = [
                    link
                    for link in mf.links
                    if link.get("from_uri") not in stale_uris
                    and link.get("to_uri") not in stale_uris
                ]
                mf.backlinks = [
                    link
                    for link in mf.backlinks
                    if link.get("from_uri") not in stale_uris
                    and link.get("to_uri") not in stale_uris
                ]
                if link_groups["links"]:
                    mf.links = merge_links(mf.links, link_groups["links"])
                if link_groups["backlinks"]:
                    mf.backlinks = merge_links(mf.backlinks, link_groups["backlinks"])
                current_trace_id = get_trace_id()
                if current_trace_id:
                    mf.extra_fields["last_update_trace_id"] = current_trace_id
                bump_memory_version(mf)
                await viking_fs.write_file(
                    uri,
                    MemoryFileUtils.write(mf),
                    ctx=ctx,
                    lease_ref=lease_ref,
                )
                result.add_edited(uri)
            except Exception as e:
                tracer.error(f"Failed to inherit deleted memory links for {uri}: {e}")

    async def _apply_delete(
        self,
        uri: str,
        ctx: RequestContext,
        lease_ref: Any = None,
    ) -> None:
        """Apply delete operation (uri is already a string)."""
        viking_fs = self._get_viking_fs()

        # Delete from VikingFS
        # VikingFS automatically handles vector index cleanup.
        try:
            await viking_fs.rm(uri, recursive=False, ctx=ctx, lease_ref=lease_ref)
        except NotFoundError:
            tracer.error(f"Memory not found for delete: {uri}")
            # Idempotent - deleting non-existent file succeeds

    async def _remove_archived_vectors(
        self,
        result: MemoryUpdateResult,
        ctx: RequestContext,
    ) -> None:
        """Remove archived Experiences from recall in one batched index call."""

        uris = list(dict.fromkeys(result.archived_uris))
        if not uris:
            return
        delete_vectors = getattr(self._get_viking_fs(), "_delete_from_vector_store", None)
        if delete_vectors is None:
            return
        try:
            await delete_vectors(uris, ctx=ctx)
        except Exception as exc:
            # Storage status and Agent read guards remain authoritative.  Do
            # not roll back a completed archive because index cleanup can be
            # retried independently, but keep the failure observable.
            tracer.error(f"Failed to remove archived Experience vectors: uris={uris}, error={exc}")
            result.add_error("vector_index", exc)

    async def _unlink_archived_experience_cases(
        self,
        operations: ResolvedOperations,
        result: MemoryUpdateResult,
        ctx: RequestContext,
        *,
        lease_ref: Any = None,
    ) -> None:
        """Remove or remap Case links after an Experience becomes archived.

        The caller holds one exact-batch lease covering every archived
        Experience, its persisted Case backlinks, and any replacement target.
        Each Case is read at most once per apply batch.
        """

        viking_fs = self._get_viking_fs()
        case_schema = self._registry.get(CASE_MEMORY_TYPE)
        replacement_links: list[StoredLink] = []
        cases_by_uri: dict[str, list[tuple[str, str | None]]] = {}
        case_updates: dict[
            str,
            tuple[
                MemoryFile,
                list[dict[str, Any]],
                list[dict[str, Any]],
                list[dict[str, Any]],
            ],
        ] = {}

        for op in operations.upsert_operations:
            if op.lifecycle_action != "archive":
                continue
            for experience_uri in op.uris:
                if experience_uri not in result.archived_uris:
                    continue
                replacement_uri = op.archive_replacement_uri
                for case_uri in op.archive_case_uris_by_uri.get(experience_uri, []):
                    cases_by_uri.setdefault(case_uri, []).append((experience_uri, replacement_uri))

        for case_uri, archive_targets in cases_by_uri.items():
            try:
                raw = await viking_fs.read_file(case_uri, ctx=ctx)
                case_file = MemoryFileUtils.read(raw or "", uri=case_uri)
                original_links = list(case_file.links or [])
                retained_links: list[dict[str, Any]] = []
                replacement_candidates: list[dict[str, Any]] = []
                for link in original_links:
                    target_uri = str(link.get("to_uri") or "")
                    match = next(
                        (
                            (archived_uri, replacement_uri)
                            for archived_uri, replacement_uri in archive_targets
                            if target_uri == archived_uri
                        ),
                        None,
                    )
                    if match is None:
                        retained_links.append(link)
                        continue
                    _, replacement_uri = match
                    if not replacement_uri:
                        continue
                    remapped = dict(link)
                    remapped["to_uri"] = replacement_uri
                    try:
                        replacement_links.append(StoredLink(**remapped))
                        replacement_candidates.append(remapped)
                    except Exception:
                        tracer.error(
                            f"Failed to remap archived Experience link: case={case_uri}, "
                            f"replacement={replacement_uri}"
                        )

                case_updates[case_uri] = (
                    case_file,
                    original_links,
                    retained_links,
                    replacement_candidates,
                )
            except Exception as exc:
                result.add_error(case_uri, exc)
                tracer.error(f"Failed to unlink archived Experience from Case {case_uri}: {exc}")

        updated_replacement_uris: set[str] = set()
        if replacement_links:
            replacement_files: dict[str, MemoryFile] = {}
            excluded_replacement_uris: set[str] = set()
            for replacement_uri in {link.to_uri for link in replacement_links if link.to_uri}:
                try:
                    replacement_file = result.files_by_uri.get(replacement_uri)
                    if replacement_file is None:
                        raw = await viking_fs.read_file(replacement_uri, ctx=ctx)
                        if not raw:
                            raise FileNotFoundError(
                                f"archive replacement Experience does not exist: {replacement_uri}"
                            )
                        replacement_file = MemoryFileUtils.read(raw, uri=replacement_uri)
                    if not experience_is_case_linkable(replacement_file.extra_fields.get("status")):
                        # A valid draft/degraded replacement may be written in
                        # this batch. It simply has no public Case link yet;
                        # reporting a write error would invalidate its applied
                        # policy snapshot despite the successful file writes.
                        excluded_replacement_uris.add(replacement_uri)
                        continue
                    replacement_files[replacement_uri] = replacement_file
                except Exception as exc:
                    excluded_replacement_uris.add(replacement_uri)
                    result.add_error(replacement_uri, exc)
            valid_replacement_links = [
                link
                for link in replacement_links
                if link.to_uri and link.to_uri not in excluded_replacement_uris
            ]
            updated_uris = await write_stored_links(
                valid_replacement_links,
                ctx,
                viking_fs,
                skip_uris=set(cases_by_uri),
                lease_ref=lease_ref,
                prefetched_files=replacement_files,
            )
            updated_replacement_uris = set(updated_uris)
            for uri in updated_uris:
                if uri not in result.edited_uris:
                    result.add_edited(uri)
            expected_replacement_uris = {
                link.to_uri for link in valid_replacement_links if link.to_uri
            }
            for uri in sorted(expected_replacement_uris - updated_replacement_uris):
                result.add_error(
                    uri,
                    RuntimeError("archived Experience replacement backlink could not be persisted"),
                )

        # Only expose a replacement URI from a Case after the replacement's
        # backlink is durable.  A failed replacement write degrades to simply
        # removing the archived URI, which is safe and retryable.
        from openviking.session.memory.merge_op.link_merge import merge_links

        for case_uri, (
            case_file,
            original_links,
            retained_links,
            replacement_candidates,
        ) in case_updates.items():
            final_links = [
                *retained_links,
                *[
                    link
                    for link in replacement_candidates
                    if str(link.get("to_uri") or "") in updated_replacement_uris
                ],
            ]
            if final_links == original_links:
                continue
            try:
                case_file.links = merge_links([], final_links)
                bump_memory_version(case_file)
                await viking_fs.write_file(
                    case_uri,
                    MemoryFileUtils.write(
                        case_file,
                        content_template=(
                            case_schema.content_template if case_schema is not None else None
                        ),
                    ),
                    ctx=ctx,
                    lease_ref=lease_ref,
                )
                if case_uri not in result.edited_uris:
                    result.add_edited(case_uri)
            except Exception as exc:
                result.add_error(case_uri, exc)
                tracer.error(f"Failed to unlink archived Experience from Case {case_uri}: {exc}")

    async def _vectorize_memories(
        self,
        result: MemoryUpdateResult,
        ctx: RequestContext,
        extract_context: Any = None,
        uri_memory_type_map: Dict[str, str] = None,
        search_tags_by_uri: Dict[str, List[str]] = None,
    ) -> int:
        """Vectorize written and edited memory files.

        Args:
            result: MemoryUpdateResult with written_uris and edited_uris
            ctx: Request context
            extract_context: Extract context for embedding template rendering
            uri_memory_type_map: Mapping from URI to memory_type
            search_tags_by_uri: Transient search tags to attach while indexing each URI
        """
        if not self._vikingdb:
            logger.debug("VikingDB not available, skipping vectorization")
            return 0

        uri_memory_type_map = uri_memory_type_map or {}
        search_tags_by_uri = search_tags_by_uri or {}
        viking_fs = self._get_viking_fs()
        request_wait_tracker = get_request_wait_tracker()
        attempted_count = 0

        # Collect all URIs to vectorize (skip .overview.md and .abstract.md - they are handled separately)
        # Also skip URIs that were deleted in the same batch
        uris_to_vectorize = []
        deleted_set = set(result.deleted_uris)
        archived_set = set(result.archived_uris)
        for uri in result.written_uris + result.edited_uris:
            cached_file = result.files_by_uri.get(uri)
            if (
                uri in deleted_set
                or uri in archived_set
                or (cached_file is not None and experience_file_is_archived(cached_file, uri=uri))
            ):
                continue
            if not uri.endswith("/.overview.md") and not uri.endswith("/.abstract.md"):
                uris_to_vectorize.append(uri)

        if not uris_to_vectorize:
            logger.debug("No memory files to vectorize")
            return 0

        for uri in uris_to_vectorize:
            try:
                cached_file = result.files_by_uri.get(uri)
                if cached_file is not None:
                    mf = cached_file.model_copy(deep=True)
                else:
                    content = await viking_fs.read_file(uri, ctx=ctx) or ""
                    mf = MemoryFileUtils.read(content, uri=uri)
                from openviking.session.memory.utils.link_renderer import LinkRenderer

                abstract = LinkRenderer.strip_all_links(mf.content or "")
                abstract = self._truncate_memory_abstract(abstract)
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
                    transient_tags = search_tags_by_uri.get(uri)
                    if transient_tags:
                        embedding_msg.context_data["search_tags"] = list(transient_tags)
                        embedding_msg.context_data["_upsert_options"] = {
                            "search_tag_mode": "append"
                        }
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

    @staticmethod
    def _truncate_memory_abstract(abstract: str) -> str:
        """Cap memory vector-store abstract fields below backend byte limits."""
        encoded = (abstract or "").encode("utf-8")
        if len(encoded) <= _MEMORY_ABSTRACT_MAX_BYTES:
            return abstract or ""
        return encoded[:_MEMORY_ABSTRACT_MAX_BYTES].decode("utf-8", errors="ignore")

    async def generate_overview(
        self,
        memory_type: str,
        directory: str,
        ctx: RequestContext,
        extract_context: Any = None,
        lease_ref: Any = None,
    ) -> bool:
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
            return False

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
            return False
        except Exception as e:
            tracer.error(f"Failed to list files in {directory}: {e}")
            return False

        # If no memory files, delete the .overview.md and the directory if empty
        if not md_files:
            overview_path = f"{directory.rstrip('/')}/.overview.md"
            can_delete_directory = all(
                entry.get("name", "") in {"", ".overview.md"} for entry in entries
            )
            try:
                await viking_fs.rm(
                    overview_path,
                    recursive=False,
                    ctx=ctx,
                    lease_ref=lease_ref,
                )
            except Exception:
                pass
            # Try to delete empty directory
            if can_delete_directory:
                try:
                    await viking_fs.rm(
                        directory,
                        recursive=True,
                        ctx=ctx,
                        lease_ref=lease_ref,
                    )
                except Exception:
                    pass
            return True

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
            return False

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
            return False

        # Write .overview.md to the directory
        overview_path = f"{directory.rstrip('/')}/.overview.md"
        try:
            await viking_fs.write_file(
                overview_path,
                render_abstract_overview(
                    ContextLevel.OVERVIEW,
                    directory,
                    rendered,
                    {
                        "generated_by": {
                            "component": "MemoryUpdater",
                            "trigger": "memory_update",
                        },
                        "freshness": freshness_metadata(len(md_files), len(items)),
                    },
                ),
                ctx=ctx,
                lease_ref=lease_ref,
            )
            from openviking.utils.embedding_utils import vectorize_directory_meta

            await vectorize_directory_meta(
                uri=directory,
                abstract="",
                overview=rendered,
                context_type="memory",
                ctx=ctx,
                include_abstract=False,
            )
            return True
        except Exception as e:
            tracer.error(f"Failed to write overview {overview_path}: {e}")
            return False
