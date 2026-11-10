# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0
"""Usage event extractors."""

from __future__ import annotations

import json
import re
from datetime import datetime
from typing import Any, Iterable, Literal, Protocol, cast

from openviking.message import Message, ToolPart
from openviking.utils.time_utils import format_iso8601, parse_iso_datetime

from .models import UsageContext, UsageEvent, utc_now_iso

UsageKind = Literal["recall", "injection"]
ToolOperation = Literal["find", "search", "list", "read", "multi_read"]

_EXPERIENCE_SIDECAR_FILENAMES = {".abstract.md", ".overview.md", ".relations.json"}
_RECALL_TOOL_OPERATIONS: dict[str, ToolOperation] = {
    "find": "find",
    "search": "search",
    "list": "list",
    "openviking_find": "find",
    "openviking_search": "search",
    "openviking_list": "list",
    "ov_search": "search",
    "ov_list": "list",
}
_INJECTION_TOOL_OPERATIONS: dict[str, ToolOperation] = {
    "read": "read",
    "multi_read": "multi_read",
    "openviking_read": "read",
    "openviking_multi_read": "multi_read",
    "ov_read": "read",
    "ov_multi_read": "multi_read",
}
_MCP_OPENVIKING_TOOL_RE = re.compile(
    r"^mcp__(?:openviking|plugin_.+_openviking)__(find|search|list|read|multi_read)$",
    re.IGNORECASE,
)
_MCP_SEARCH_RESULT_RE = re.compile(r"^- \[[^]]+\]\s+(viking://.+?)\s*$")
_OPENVIKING_SEARCH_TABLE_ROW_RE = re.compile(
    r"^\s*\d+\s{2,}(?:memory|resource|skill)\s{2,}"
    r"(viking://.+?)\s{2,}(?:\d+)?\s{2,}(?:\d+(?:\.\d+)?)?\s{2,}.*$"
)
_OPENVIKING_SEARCH_SIMPLE_ROW_RE = re.compile(
    r"^\s*\d+\s+(?:memory|resource|skill)\s+(viking://\S+)\s*$"
)
_LIST_FILE_RE = re.compile(r"^\s*\[file\]\s+(.+?)\s*$")
# Historical transcripts recorded the removed uid-less shorthand; newer ones use
# the viking://~ home alias. Both are canonicalized to the caller's user root.
_USER_MEMORY_SHORTHAND_PREFIX = "viking://user/memories/"
_USER_MEMORY_HOME_ALIAS_PREFIX = "viking://~/memories/"
_USER_MEMORY_RELATIVE_PREFIXES = (
    _USER_MEMORY_SHORTHAND_PREFIX,
    _USER_MEMORY_HOME_ALIAS_PREFIX,
)


class UsageExtractor(Protocol):
    name: str

    async def extract(
        self,
        *,
        messages: list[Message],
        context: UsageContext,
    ) -> list[UsageEvent]: ...


def _load_json(value: Any) -> Any:
    if isinstance(value, (dict, list)):
        return value
    if isinstance(value, str) and value.strip():
        try:
            return json.loads(value)
        except json.JSONDecodeError:
            return value
    return value


def _tool_usage(tool_name: str) -> tuple[UsageKind, ToolOperation] | None:
    name = tool_name.strip()
    operation = _RECALL_TOOL_OPERATIONS.get(name)
    if operation is not None:
        return "recall", operation
    operation = _INJECTION_TOOL_OPERATIONS.get(name)
    if operation is not None:
        return "injection", operation

    match = _MCP_OPENVIKING_TOOL_RE.fullmatch(name)
    if match is None:
        return None
    operation = cast(ToolOperation, match.group(1).lower())
    if operation in {"find", "search", "list"}:
        return "recall", operation
    return "injection", operation


def _iter_named_uris(value: Any) -> Iterable[str]:
    """Yield URI values from structured `uri`/`uris` fields only."""
    if isinstance(value, dict):
        for key, child in value.items():
            if key in {"uri", "uris"}:
                if isinstance(child, str):
                    yield child
                elif isinstance(child, list):
                    for item in child:
                        if isinstance(item, str):
                            yield item
            if isinstance(child, (dict, list)):
                yield from _iter_named_uris(child)
    elif isinstance(value, list):
        for child in value:
            yield from _iter_named_uris(child)


def _iter_search_result_uris(value: Any) -> Iterable[str]:
    """Yield URIs only from known search-result collections, not scope metadata."""
    if isinstance(value, dict):
        for key, child in value.items():
            if key in {"results", "memories", "resources", "skills"}:
                yield from _iter_named_uris(child)
            if isinstance(child, (dict, list)):
                yield from _iter_search_result_uris(child)
    elif isinstance(value, list):
        for child in value:
            yield from _iter_search_result_uris(child)


def _iter_text_fields(value: Any) -> Iterable[str]:
    if isinstance(value, str):
        yield value
    elif isinstance(value, dict):
        for key, child in value.items():
            if key == "text" and isinstance(child, str):
                yield child
            elif isinstance(child, (dict, list)):
                yield from _iter_text_fields(child)
    elif isinstance(value, list):
        for child in value:
            yield from _iter_text_fields(child)


def _unique_uris(uris: Iterable[str], context: UsageContext) -> list[str]:
    seen: set[str] = set()
    result: list[str] = []
    for raw_uri in uris:
        uri = _canonicalize_usage_uri(raw_uri, context)
        if uri in seen or not _is_experience_uri(uri, context):
            continue
        seen.add(uri)
        result.append(uri)
    return result


def _canonicalize_usage_uri(uri: str, context: UsageContext) -> str:
    normalized = uri.strip()
    for prefix in _USER_MEMORY_RELATIVE_PREFIXES:
        if normalized.startswith(prefix):
            relative = normalized.removeprefix(prefix)
            return f"viking://user/{context.user_id}/memories/{relative}"
    return normalized


def _join_uri(parent: str, child: str) -> str:
    return f"{parent.rstrip('/')}/{child.lstrip('/')}"


def _iter_list_file_uris(value: Any, parent_uri: str) -> Iterable[str]:
    """Extract files from structured list results without treating directories as recalls."""
    if not isinstance(value, dict):
        return
    entries = value.get("entries")
    if isinstance(entries, list):
        for entry in entries:
            if not isinstance(entry, dict):
                continue
            if entry.get("isDir") is True or entry.get("is_dir") is True:
                continue
            if str(entry.get("type") or "").lower() in {"dir", "directory"}:
                continue
            uri = str(entry.get("uri") or "").strip()
            if uri:
                yield uri
                continue
            name = str(entry.get("name") or "").strip()
            if name and parent_uri:
                yield _join_uri(parent_uri, name)
    for child in value.values():
        if isinstance(child, (dict, list)):
            if isinstance(child, dict):
                yield from _iter_list_file_uris(child, parent_uri)
            else:
                for item in child:
                    if isinstance(item, dict):
                        yield from _iter_list_file_uris(item, parent_uri)


def _read_result_statuses(value: Any) -> dict[str, bool]:
    """Return per-URI success flags when a multi-read result exposes them."""
    statuses: dict[str, bool] = {}
    if isinstance(value, dict):
        uri = value.get("uri")
        success = value.get("success")
        if isinstance(uri, str) and isinstance(success, bool):
            statuses[uri.strip()] = success
        for child in value.values():
            if isinstance(child, (dict, list)):
                statuses.update(_read_result_statuses(child))
    elif isinstance(value, list):
        for child in value:
            statuses.update(_read_result_statuses(child))
    return statuses


def _read_failed_in_text(uri: str, texts: Iterable[str], context: UsageContext) -> bool:
    aliases = [uri]
    canonical_prefix = f"viking://user/{context.user_id}/memories/"
    if uri.startswith(canonical_prefix):
        relative = uri.removeprefix(canonical_prefix)
        aliases.extend(f"{prefix}{relative}" for prefix in _USER_MEMORY_RELATIVE_PREFIXES)
    for text in texts:
        for alias in aliases:
            if f"(nothing found at {alias})" in text:
                return True
            section_marker = f"--- START OF {alias} ---"
            if section_marker in text:
                section = text.split(section_marker, 1)[1].split(f"--- END OF {alias} ---", 1)[0]
                if section.lstrip().startswith("ERROR:"):
                    return True
    return False


def _is_experience_uri(uri: str, context: UsageContext) -> bool:
    prefix = f"viking://user/{context.user_id}/memories/experiences/"
    if not uri.startswith(prefix) or "?" in uri or "#" in uri:
        return False
    relative = uri.removeprefix(prefix)
    segments = relative.split("/")
    if not relative or any(not segment or segment in {".", ".."} for segment in segments):
        return False
    return segments[-1] not in _EXPERIENCE_SIDECAR_FILENAMES


def _event_time(message: Message) -> str:
    value = message.created_at
    try:
        if isinstance(value, datetime):
            return format_iso8601(value)
        if isinstance(value, str) and value.strip():
            return format_iso8601(parse_iso_datetime(value.strip()))
    except (TypeError, ValueError):
        pass
    return utc_now_iso()


class MemoryUsageExtractor:
    """Extract Experience recall/injection events from OpenViking tool parts."""

    name = "memory_usage"

    async def extract(
        self,
        *,
        messages: list[Message],
        context: UsageContext,
    ) -> list[UsageEvent]:
        events: list[UsageEvent] = []
        tool_inputs: dict[tuple[str, str], dict[str, Any]] = {}
        for message in messages:
            for part in message.parts:
                if (
                    isinstance(part, ToolPart)
                    and part.tool_id
                    and isinstance(part.tool_input, dict)
                    and part.tool_input
                ):
                    tool_inputs[(part.tool_id, part.tool_name)] = part.tool_input

        for message in messages:
            for part in message.parts:
                if not isinstance(part, ToolPart):
                    continue
                if not part.tool_id:
                    continue
                if part.tool_status != "completed":
                    continue
                usage = _tool_usage(part.tool_name)
                if usage is None:
                    continue
                kind, operation = usage
                fallback_input = tool_inputs.get((part.tool_id, part.tool_name), {})
                if kind == "recall":
                    events.extend(
                        self._extract_recall_events(
                            part,
                            operation=operation,
                            context=context,
                            message=message,
                            fallback_input=fallback_input,
                        )
                    )
                else:
                    events.extend(
                        self._extract_injection_events(
                            part,
                            operation=operation,
                            context=context,
                            message=message,
                            fallback_input=fallback_input,
                        )
                    )
        return events

    def _extract_recall_events(
        self,
        part: ToolPart,
        *,
        operation: ToolOperation,
        context: UsageContext,
        message: Message,
        fallback_input: dict[str, Any],
    ) -> Iterable[UsageEvent]:
        output = _load_json(part.tool_output)
        tool_input = part.tool_input if isinstance(part.tool_input, dict) else {}
        if not tool_input:
            tool_input = fallback_input

        candidates: list[str] = []
        if operation == "list":
            parent_uri = str(tool_input.get("uri") or "").strip()
            candidates.extend(_iter_list_file_uris(output, parent_uri))
            for text in _iter_text_fields(output):
                for line in text.splitlines():
                    match = _LIST_FILE_RE.fullmatch(line)
                    if match is None:
                        continue
                    label = match.group(1).split(" - ", 1)[0].strip()
                    if label.startswith("viking://"):
                        candidates.append(label)
                    elif parent_uri:
                        candidates.append(_join_uri(parent_uri, label))
        else:
            candidates.extend(_iter_search_result_uris(output))
            for text in _iter_text_fields(output):
                for line in text.splitlines():
                    match = _MCP_SEARCH_RESULT_RE.fullmatch(line)
                    if match is None:
                        match = _OPENVIKING_SEARCH_TABLE_ROW_RE.fullmatch(line)
                    if match is None:
                        match = _OPENVIKING_SEARCH_SIMPLE_ROW_RE.fullmatch(line)
                    if match is not None:
                        candidates.append(match.group(1).strip())

        return [
            self._build_event(
                event_type="memory.recalled",
                resource_uri=uri,
                part=part,
                context=context,
                message=message,
            )
            for uri in _unique_uris(candidates, context)
        ]

    def _extract_injection_events(
        self,
        part: ToolPart,
        *,
        operation: ToolOperation,
        context: UsageContext,
        message: Message,
        fallback_input: dict[str, Any],
    ) -> list[UsageEvent]:
        tool_input = part.tool_input if isinstance(part.tool_input, dict) else {}
        if not tool_input:
            tool_input = fallback_input
        output = _load_json(part.tool_output)

        candidates = list(_iter_named_uris(tool_input))
        if not candidates:
            candidates.extend(_iter_named_uris(output))
        statuses = (
            {
                _canonicalize_usage_uri(uri, context): success
                for uri, success in _read_result_statuses(output).items()
            }
            if operation == "multi_read"
            else {}
        )
        texts = list(_iter_text_fields(output))

        events: list[UsageEvent] = []
        for uri in _unique_uris(candidates, context):
            if statuses.get(uri) is False or _read_failed_in_text(uri, texts, context):
                continue
            events.append(
                self._build_event(
                    event_type="memory.injected",
                    resource_uri=uri,
                    part=part,
                    context=context,
                    message=message,
                )
            )
        return events

    def _build_event(
        self,
        *,
        event_type: str,
        resource_uri: str,
        part: ToolPart,
        context: UsageContext,
        message: Message,
    ) -> UsageEvent:
        return UsageEvent(
            event_type=event_type,
            resource_uri=resource_uri,
            resource_type="experience",
            account_id=context.account_id,
            user_id=context.user_id,
            session_id=context.session_id,
            task_id=context.task_id,
            occurred_at=_event_time(message),
            evidence={
                "archive_uri": context.archive_uri,
                "message_id": message.id,
                "tool_call_id": part.tool_id,
                "tool_name": part.tool_name,
            },
        )
