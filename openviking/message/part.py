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
    tool_uri: str = ""  # viking://session/{id}/tools/{tool_id}
    skill_uri: str = ""  # viking://agent/skills/{skill_name}
    tool_input: Optional[dict] = None
    tool_output: str = ""
    tool_status: str = "pending"  # pending | running | completed | error


Part = Union[TextPart, ContextPart, ToolPart]
