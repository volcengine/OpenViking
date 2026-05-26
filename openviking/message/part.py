# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0
"""Part type definitions - based on opencode Part design.

Message consists of multiple Parts, each Part has different type and purpose.
"""

from dataclasses import dataclass
from typing import Literal, Optional, Union


@dataclass
class TextPart:
    """Text content component."""

    text: str = ""
    type: Literal["text"] = "text"


@dataclass
class ContextPart:
    """Context reference component (L0 abstract + URI).

    Used to track which contexts (memory/resource/skill) the message references.
    """

    type: Literal["context"] = "context"
    uri: str = ""
    context_type: Literal["memory", "resource", "skill"] = "memory"
    abstract: str = ""


@dataclass
class ToolPart:
    """Tool call component (references tool file within session).

    Tool status: pending | running | completed | error
    """

    type: Literal["tool"] = "tool"
    tool_id: str = ""
    tool_name: str = ""
    tool_uri: str = ""  # viking://session/{user_space_name}/{session_id}/tools/{tool_id}
    skill_uri: str = ""  # viking://agent/{agent_id}/skills/{skill_name} or .../user/{user_id}/skills/{skill_name}
    tool_input: Optional[dict] = None
    tool_output: str = ""
    tool_status: str = "pending"  # pending | running | completed | error
    duration_ms: Optional[float] = None  # 执行耗时（毫秒）
    prompt_tokens: Optional[int] = None  # 输入 Token
    completion_tokens: Optional[int] = None  # 输出 Token
    tool_output_ref: str = ""
    tool_output_truncated: bool = False
    tool_output_original_chars: Optional[int] = None
    tool_output_preview_chars: Optional[int] = None
    tool_output_sha256: str = ""
    tool_output_storage_uri: str = ""
    tool_output_mime_type: str = "text/plain"
    tool_output_source_ref: str = ""
    tool_output_source_offset: Optional[int] = None
    tool_output_source_limit: Optional[int] = None
    tool_output_externalization_error: str = ""
    tool_output_group_id: str = ""
    tool_output_externalized_reason: str = ""
    tool_output_group_original_chars: Optional[int] = None
    tool_output_group_budget_chars: Optional[int] = None


Part = Union[TextPart, ContextPart, ToolPart]


def part_from_dict(data: dict) -> Part:
    """Convert a dict to a Part object.

    Args:
        data: Dictionary with part data. Must contain 'type' field.

    Returns:
        Part object (TextPart, ContextPart, or ToolPart)
    """
    part_type = data.get("type", "text")
    if part_type == "text":
        return TextPart(text=data.get("text", ""))
    elif part_type == "context":
        return ContextPart(
            uri=data.get("uri", ""),
            context_type=data.get("context_type", "memory"),
            abstract=data.get("abstract", ""),
        )
    elif part_type == "tool":
        return ToolPart(
            tool_id=data.get("tool_id", ""),
            tool_name=data.get("tool_name", ""),
            tool_uri=data.get("tool_uri", ""),
            skill_uri=data.get("skill_uri", ""),
            tool_input=data.get("tool_input"),
            tool_output=data.get("tool_output", ""),
            tool_status=data.get("tool_status", "pending"),
            duration_ms=data.get("duration_ms"),
            prompt_tokens=data.get("prompt_tokens"),
            completion_tokens=data.get("completion_tokens"),
            tool_output_ref=data.get("tool_output_ref", ""),
            tool_output_truncated=bool(data.get("tool_output_truncated", False)),
            tool_output_original_chars=data.get("tool_output_original_chars"),
            tool_output_preview_chars=data.get("tool_output_preview_chars"),
            tool_output_sha256=data.get("tool_output_sha256", ""),
            tool_output_storage_uri=data.get("tool_output_storage_uri", ""),
            tool_output_mime_type=data.get("tool_output_mime_type", "text/plain"),
            tool_output_source_ref=data.get("tool_output_source_ref", ""),
            tool_output_source_offset=data.get("tool_output_source_offset"),
            tool_output_source_limit=data.get("tool_output_source_limit"),
            tool_output_externalization_error=data.get("tool_output_externalization_error", ""),
            tool_output_group_id=data.get("tool_output_group_id", ""),
            tool_output_externalized_reason=data.get("tool_output_externalized_reason", ""),
            tool_output_group_original_chars=data.get("tool_output_group_original_chars"),
            tool_output_group_budget_chars=data.get("tool_output_group_budget_chars"),
        )
    else:
        return TextPart(text=str(data))
