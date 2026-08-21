# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0
"""Experience-to-trajectory lineage helpers."""

from __future__ import annotations

import json
import re
from typing import Any, Iterable

from openviking.core.namespace import uri_parts
from openviking.message import Message, ToolPart
from openviking.server.identity import RequestContext
from openviking.utils.tags import normalize_search_tag

_EXPERIENCE_SIDECAR_FILENAMES = {".abstract.md", ".overview.md", ".relations.json"}
_READ_TOOL_OPERATIONS = {
    "read": "read",
    "multi_read": "multi_read",
    "openviking_read": "read",
    "openviking_multi_read": "multi_read",
    "ov_read": "read",
    "ov_multi_read": "multi_read",
}
_MCP_OPENVIKING_READ_RE = re.compile(r"^mcp__openviking__(read|multi_read)$")
TRAJECTORY_OUTCOMES = ("success", "failure", "partial", "unknown", "unfinished")


def is_experience_uri_for_user(uri: str, user_id: str) -> bool:
    """Return whether ``uri`` identifies an Experience owned by ``user_id``."""
    if not uri or "?" in uri or "#" in uri:
        return False
    parts = uri_parts(uri)
    if len(parts) < 5 or parts[:4] != ["user", user_id, "memories", "experiences"]:
        return False
    relative_parts = parts[4:]
    if any(not segment or segment in {".", ".."} for segment in relative_parts):
        return False
    return relative_parts[-1] not in _EXPERIENCE_SIDECAR_FILENAMES


def canonical_experience_uri(uri: str, ctx: RequestContext) -> str | None:
    """Enforce canonical Experience URI shape and current-user ownership."""
    canonical_uri = str(uri or "").strip()
    if not canonical_uri.startswith("viking://"):
        return None
    if not is_experience_uri_for_user(canonical_uri, ctx.user.user_id):
        return None
    return canonical_uri


def experience_source_tag(experience_uri: str) -> str:
    """Build the exact retrieval tag used for Experience lineage filtering."""
    uri_key = _escape_search_tag_key(str(experience_uri or "").strip())
    return normalize_search_tag(f"{uri_key}=1")


def _escape_search_tag_key(value: str) -> str:
    """Preserve URI identity through lowercase-only strict k=v tag normalization."""
    escaped: list[str] = []
    for character in value:
        if character in {"%", "="} or character.lower() != character:
            escaped.extend(f"%{byte:02x}" for byte in character.encode("utf-8"))
        else:
            escaped.append(character)
    return "".join(escaped)


def experience_source_tags(experience_uris: Iterable[str] | None) -> list[str]:
    """Build stable, de-duplicated lineage tags for Experience URIs."""
    if isinstance(experience_uris, str):
        experience_uris = [experience_uris]
    tags: list[str] = []
    seen: set[str] = set()
    for uri in experience_uris or []:
        normalized = str(uri or "").strip()
        if not normalized:
            continue
        tag = experience_source_tag(normalized)
        if tag in seen:
            continue
        seen.add(tag)
        tags.append(tag)
    return tags


def normalize_trajectory_outcome(outcome: Any) -> str:
    """Normalize trajectory outcome values used by scalar lineage aggregation."""
    normalized = str(outcome or "").strip().lower()
    if normalized not in TRAJECTORY_OUTCOMES:
        return "unknown"
    return normalized


def trajectory_outcome_tag(outcome: Any) -> str:
    """Build the exact scalar tag used for trajectory outcome aggregation."""
    return normalize_search_tag(f"trajectory_outcome={normalize_trajectory_outcome(outcome)}")


def collect_read_experience_uris(
    messages: Iterable[Message] | None,
    *,
    ctx: RequestContext,
) -> list[str]:
    """Collect Experiences successfully read in a committed session message set."""
    message_list = list(messages or [])
    tool_inputs: dict[tuple[str, str], dict[str, Any]] = {}
    for message in message_list:
        for part in message.parts:
            if (
                isinstance(part, ToolPart)
                and part.tool_id
                and isinstance(part.tool_input, dict)
                and part.tool_input
            ):
                tool_inputs[(part.tool_id, part.tool_name)] = part.tool_input

    result: list[str] = []
    seen: set[str] = set()
    for message in message_list:
        for part in message.parts:
            if not isinstance(part, ToolPart):
                continue
            operation = _read_operation(part.tool_name)
            if operation is None or part.tool_status != "completed":
                continue
            tool_input = part.tool_input if isinstance(part.tool_input, dict) else {}
            if not tool_input and part.tool_id:
                tool_input = tool_inputs.get((part.tool_id, part.tool_name), {})
            output = _load_value(part.tool_output)
            candidates = list(_iter_named_uris(tool_input))
            if not candidates:
                candidates.extend(_iter_named_uris(output))
            statuses = _read_result_statuses(output) if operation == "multi_read" else {}
            texts = list(_iter_text_fields(output))
            for raw_uri in candidates:
                uri = str(raw_uri or "").strip()
                canonical_uri = canonical_experience_uri(uri, ctx)
                if (
                    not canonical_uri
                    or canonical_uri in seen
                    or statuses.get(uri) is False
                    or _read_failed_in_text(uri, texts)
                ):
                    continue
                seen.add(canonical_uri)
                result.append(canonical_uri)
    return result


def _read_operation(tool_name: str) -> str | None:
    name = str(tool_name or "").strip()
    operation = _READ_TOOL_OPERATIONS.get(name)
    if operation is not None:
        return operation
    match = _MCP_OPENVIKING_READ_RE.fullmatch(name)
    return match.group(1) if match is not None else None


def _load_value(value: Any) -> Any:
    if isinstance(value, (dict, list)):
        return value
    if isinstance(value, str) and value.strip():
        try:
            return json.loads(value)
        except json.JSONDecodeError:
            return value
    return value


def _iter_named_uris(value: Any) -> Iterable[str]:
    if isinstance(value, dict):
        for key, child in value.items():
            if key in {"uri", "uris"}:
                if isinstance(child, str):
                    yield child
                elif isinstance(child, list):
                    yield from (item for item in child if isinstance(item, str))
            if isinstance(child, (dict, list)):
                yield from _iter_named_uris(child)
    elif isinstance(value, list):
        for child in value:
            yield from _iter_named_uris(child)


def _read_result_statuses(value: Any) -> dict[str, bool]:
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


def _read_failed_in_text(uri: str, texts: Iterable[str]) -> bool:
    missing_marker = f"(nothing found at {uri})"
    for text in texts:
        if missing_marker in text:
            return True
        section_marker = f"--- START OF {uri} ---"
        if section_marker in text:
            section = text.split(section_marker, 1)[1].split(f"--- END OF {uri} ---", 1)[0]
            if section.lstrip().startswith("ERROR:"):
                return True
    return False
