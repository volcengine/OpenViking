# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0
"""Schemas for conditional experience constraints.

A constraint experience stores natural-language reminder text in the memory
content and stores executable trigger metadata in MEMORY_FIELDS.  The trigger is
intentionally evaluated against a small JSON-like context before a candidate tool
call, not against full runtime objects.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Mapping

TriggerContext = dict[str, Any]


@dataclass(slots=True)
class ConstraintExperience:
    """Runtime view of one conditional experience constraint."""

    uri: str
    name: str
    constraint: str
    trigger_code: str
    metadata: dict[str, Any] = field(default_factory=dict)

    @classmethod
    def from_policy(cls, policy: Any) -> "ConstraintExperience | None":
        """Build from a train-domain Policy/Experience-like object.

        Returns ``None`` when the policy is not triggerable.  Old experience
        files without ``trigger_code`` are intentionally excluded from runtime
        reminder activation.
        """

        metadata = _mapping(getattr(policy, "metadata", None))
        trigger_code = str(metadata.get("trigger_code") or "").strip()
        if not trigger_code:
            return None
        uri = str(getattr(policy, "uri", "") or metadata.get("uri") or "")
        if not uri:
            return None
        name = str(
            metadata.get("experience_name")
            or metadata.get("name")
            or getattr(policy, "name", "")
            or uri.rstrip("/").rsplit("/", 1)[-1].removesuffix(".md")
        )
        constraint = str(metadata.get("content") or getattr(policy, "content", "") or "").strip()
        if not constraint:
            return None
        return cls(
            uri=uri,
            name=name,
            constraint=constraint,
            trigger_code=trigger_code,
            metadata=metadata,
        )

    @classmethod
    def from_memory_file(cls, memory_file: Any) -> "ConstraintExperience | None":
        """Build from a MemoryFile-like object parsed from an experience markdown."""

        fields = _mapping(getattr(memory_file, "extra_fields", None))
        uri = str(getattr(memory_file, "uri", "") or fields.get("uri") or "")
        if not uri:
            return None
        metadata = dict(fields)
        trigger_code = str(metadata.get("trigger_code") or "").strip()
        if not trigger_code:
            return None
        if fields.get("content"):
            constraint = str(fields.get("content") or "").strip()
        else:
            plain = getattr(memory_file, "plain_content", None)
            if callable(plain):
                constraint = str(plain() or "").strip()
            else:
                constraint = str(getattr(memory_file, "content", "") or "").strip()
        name = str(
            metadata.get("experience_name")
            or metadata.get("name")
            or uri.rstrip("/").rsplit("/", 1)[-1].removesuffix(".md")
        )
        if not constraint:
            return None
        return cls(
            uri=uri,
            name=name,
            constraint=constraint,
            trigger_code=trigger_code,
            metadata=metadata,
        )

    @classmethod
    def from_rendered_markdown(
        cls,
        content: str,
        *,
        uri: str,
        fallback_name: str | None = None,
        metadata: Mapping[str, Any] | None = None,
    ) -> "ConstraintExperience | None":
        """Build from OV client content plus structured memory metadata."""

        uri = str(uri or "").strip()
        if not uri:
            return None
        merged_metadata = _mapping(metadata)
        trigger_code = str(merged_metadata.get("trigger_code") or "").strip()
        constraint = str(merged_metadata.get("content") or content or "").strip()
        if not trigger_code or not constraint:
            return None
        name = str(
            merged_metadata.get("experience_name")
            or merged_metadata.get("name")
            or fallback_name
            or uri.rstrip("/").rsplit("/", 1)[-1].removesuffix(".md")
        )
        return cls(
            uri=uri,
            name=name,
            constraint=constraint,
            trigger_code=trigger_code,
            metadata=merged_metadata,
        )


def sanitize_messages(messages: list[Any]) -> list[dict[str, Any]]:
    """Return JSON-like message dicts safe for trigger code.

    Only stable fields are exposed.  Dict messages from provider loops are
    preserved in a compact form; OpenViking ``Message`` objects are converted to
    role/content plus basic tool evidence.
    """

    sanitized: list[dict[str, Any]] = []
    for message in messages or []:
        if isinstance(message, Mapping):
            item: dict[str, Any] = {
                "role": str(message.get("role", "") or ""),
                "content": _safe_content(message.get("content")),
            }
            if message.get("name"):
                item["name"] = str(message.get("name"))
            if message.get("tool_call_id"):
                item["tool_call_id"] = str(message.get("tool_call_id"))
            if message.get("tool_calls"):
                item["tool_calls"] = _json_safe(message.get("tool_calls"))
            sanitized.append(item)
            continue

        role = str(getattr(message, "role", "") or "")
        content = str(getattr(message, "content", "") or "")
        item = {"role": role, "content": content}
        tool_parts = []
        get_tool_parts = getattr(message, "get_tool_parts", None)
        parts = get_tool_parts() if callable(get_tool_parts) else getattr(message, "parts", [])
        for part in parts or []:
            if getattr(part, "type", None) != "tool":
                continue
            tool_parts.append(
                {
                    "tool_name": str(getattr(part, "tool_name", "") or ""),
                    "tool_input": _json_safe(getattr(part, "tool_input", None) or {}),
                    "tool_status": str(getattr(part, "tool_status", "") or ""),
                    "tool_output": str(getattr(part, "tool_output", "") or ""),
                }
            )
        if tool_parts:
            item["tool_parts"] = tool_parts
        sanitized.append(item)
    return sanitized


def build_trigger_context(
    *,
    messages: list[Any],
    candidate_tool: str,
    candidate_tool_args: Mapping[str, Any] | None,
) -> TriggerContext:
    """Build the small context available to ``should_trigger(ctx)``."""

    return {
        "messages": sanitize_messages(messages),
        "candidate_tool": str(candidate_tool or ""),
        "candidate_tool_args": _json_safe(dict(candidate_tool_args or {})),
    }


def _mapping(value: Any) -> dict[str, Any]:
    return dict(value) if isinstance(value, Mapping) else {}


def _safe_content(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, str):
        return value
    return str(_json_safe(value))


def _json_safe(value: Any) -> Any:
    if value is None or isinstance(value, (str, int, float, bool)):
        return value
    if isinstance(value, Mapping):
        return {str(k): _json_safe(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [_json_safe(v) for v in value]
    return str(value)
