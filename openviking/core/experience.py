# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0
"""Primitives shared by the Experience tools and their usage attribution.

Lives in ``openviking.core`` rather than ``openviking.session.memory`` so the
usage-reporter extractors can reuse it without importing the session package.
"""

from __future__ import annotations

import json
from typing import Any

from openviking.core.namespace import uri_parts

EXPERIENCE_SIDECAR_FILENAMES = frozenset({".abstract.md", ".overview.md", ".relations.json"})
EXPERIENCE_TOOL_NAMES = ("search_experience", "read_experience")

_ENVELOPE_UNWRAP_LIMIT = 6


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
    return relative_parts[-1] not in EXPERIENCE_SIDECAR_FILENAMES


def normalize_experience_tool_name(name: Any) -> str:
    """Strip the harness-specific MCP namespace prefix from a tool name.

    Every harness namespaces MCP tools differently — Claude Code records
    ``mcp__openviking__search_experience``, opencode ``openviking_search_experience``,
    Codex the bare name — so attribution has to compare against the bare name.
    Names that are not Experience tools are returned stripped but unchanged.
    """
    candidate = str(name or "").strip()
    for bare in EXPERIENCE_TOOL_NAMES:
        if candidate == bare:
            return bare
        for separator in ("__", "_"):
            suffix = f"{separator}{bare}"
            if len(candidate) > len(suffix) and candidate.endswith(suffix):
                return bare
    return candidate


def load_tool_output_mapping(value: Any) -> dict[str, Any]:
    """Parse recorded tool output into a mapping, unwrapping harness envelopes.

    Harnesses record MCP output in incompatible shapes: the raw JSON string,
    the MCP content-block array, and FastMCP's ``structuredContent`` /
    ``{"result": "<json>"}`` wrappers. Anything else yields an empty mapping.
    """
    payload: Any = value
    for _ in range(_ENVELOPE_UNWRAP_LIMIT):
        if isinstance(payload, str):
            text = payload.strip()
            if not text:
                return {}
            try:
                payload = json.loads(text)
            except json.JSONDecodeError:
                return {}
        elif isinstance(payload, list):
            payload = _join_content_blocks(payload)
        elif isinstance(payload, dict):
            inner = _unwrap_envelope(payload)
            if inner is None:
                return payload
            payload = inner
        else:
            return {}
    return payload if isinstance(payload, dict) else {}


def _join_content_blocks(blocks: list[Any]) -> str:
    texts: list[str] = []
    for block in blocks:
        if isinstance(block, str):
            texts.append(block)
        elif isinstance(block, dict) and isinstance(block.get("text"), str):
            texts.append(block["text"])
    return "\n".join(texts)


def _unwrap_envelope(payload: dict[str, Any]) -> Any:
    """Return the inner payload of one envelope layer, or None when there is none."""
    for key in ("structuredContent", "result"):
        inner = payload.get(key)
        if isinstance(inner, (str, dict)):
            return inner
    content = payload.get("content")
    if isinstance(content, list):
        return content
    return None
