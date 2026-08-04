# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0
"""Experience-to-trajectory lineage helpers."""

from __future__ import annotations

import json
from typing import Any, Iterable

from openviking.core.namespace import canonicalize_uri, uri_parts
from openviking.message import Message, ToolPart
from openviking.server.identity import RequestContext
from openviking.utils.tags import normalize_search_tag

_EXPERIENCE_SIDECAR_FILENAMES = {".abstract.md", ".overview.md", ".relations.json"}


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
    """Canonicalize an Experience URI and enforce current-user ownership."""
    try:
        canonical_uri = canonicalize_uri(str(uri or "").strip(), ctx)
    except (TypeError, ValueError):
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
            if part.tool_name != "read_experience" or part.tool_status != "completed":
                continue
            tool_input = part.tool_input if isinstance(part.tool_input, dict) else {}
            if not tool_input and part.tool_id:
                tool_input = tool_inputs.get((part.tool_id, part.tool_name), {})
            output = _load_mapping(part.tool_output)
            uri = tool_input.get("uri") or output.get("uri")
            canonical_uri = canonical_experience_uri(str(uri or ""), ctx)
            if not canonical_uri or canonical_uri in seen:
                continue
            seen.add(canonical_uri)
            result.append(canonical_uri)
    return result


def _load_mapping(value: Any) -> dict[str, Any]:
    if isinstance(value, dict):
        return value
    if isinstance(value, str) and value.strip():
        try:
            parsed = json.loads(value)
        except json.JSONDecodeError:
            return {}
        return parsed if isinstance(parsed, dict) else {}
    return {}
