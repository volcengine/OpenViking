# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: Apache-2.0
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
    skill_uri: str = ""  # viking://agent/{agent_space_name}/skills/{skill_name}
    tool_input: Optional[dict] = None
    tool_output: str = ""
    tool_status: str = "pending"  # pending | running | completed | error
    duration_ms: Optional[float] = None  # 执行耗时（毫秒）
    prompt_tokens: Optional[int] = None  # 输入 Token
    completion_tokens: Optional[int] = None  # 输出 Token


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
        )
    else:
        return TextPart(text=str(data))
