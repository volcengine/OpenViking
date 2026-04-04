# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0
"""Message class definition - based on opencode Message design.

Message = role + parts, supports serialization to JSONL.
"""

import json
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import List, Literal, Optional

from openviking.message.part import ContextPart, Part, TextPart, ToolPart
from openviking.utils.time_utils import format_iso8601, parse_iso_datetime


@dataclass
class Message:
    """Message = role + parts."""

    id: str
    role: Literal["user", "assistant"]
    parts: List[Part]
    created_at: datetime = None

    @property
    def content(self) -> str:
        """Quick access to first TextPart content."""
        for p in self.parts:
            if isinstance(p, TextPart):
                return p.text
        return ""

    @property
    def estimated_tokens(self) -> int:
        """Estimate token count from all parts (ceil(len/4) heuristic).

        Counts fields that actually appear in the assembled prompt:
        - TextPart.text: always emitted
        - ContextPart.abstract: injected as text (uri is not sent to the model)
        - ToolPart: tool_id (appears in toolUse.id / toolResult.toolCallId),
          tool_name, tool_input (JSON), tool_output

        Known limitation: ToolPart estimation undercounts by ~10-20 tokens per
        tool call because tool_id/toolName appear twice in the assembled transcript
        (toolUse + toolResult), and small literals like "(no output)" / "{}" are
        not counted. Under 128k budgets this is negligible; for smaller budgets
        (8k/16k) or tool-dense sessions, consider adding a conservative per-tool
        buffer instead of mirroring the full convertToAgentMessages logic.
        """
        total_chars = 0
        for p in self.parts:
            if isinstance(p, TextPart):
                total_chars += len(p.text)
            elif isinstance(p, ContextPart):
                total_chars += len(p.abstract)
            elif isinstance(p, ToolPart):
                total_chars += len(p.tool_id) + len(p.tool_name)
                if p.tool_input:
                    total_chars += len(json.dumps(p.tool_input, ensure_ascii=False))
                if p.tool_output:
                    total_chars += len(p.tool_output)
        return -(-total_chars // 4)  # ceil division

    def to_dict(self) -> dict:
        """Serialize to JSONL."""
        created_at_val = self.created_at or datetime.now(timezone.utc)
        created_at_str = format_iso8601(created_at_val)
        return {
            "id": self.id,
            "role": self.role,
            "parts": [self._part_to_dict(p) for p in self.parts],
            "created_at": created_at_str,
        }

    def _part_to_dict(self, part: Part) -> dict:
        if isinstance(part, TextPart):
            return {"type": part.type, "text": part.text}
        elif isinstance(part, ContextPart):
            return {
                "type": part.type,
                "uri": part.uri,
                "context_type": part.context_type,
                "abstract": part.abstract,
            }
        elif isinstance(part, ToolPart):
            d = {
                "type": part.type,
                "tool_id": part.tool_id,
                "tool_name": part.tool_name,
                "tool_uri": part.tool_uri,
                "skill_uri": part.skill_uri,
                "tool_status": part.tool_status,
            }
            if part.tool_input:
                d["tool_input"] = part.tool_input
            if part.tool_output:
                d["tool_output"] = part.tool_output
            if part.duration_ms is not None:
                d["duration_ms"] = part.duration_ms
            if part.prompt_tokens is not None:
                d["prompt_tokens"] = part.prompt_tokens
            if part.completion_tokens is not None:
                d["completion_tokens"] = part.completion_tokens
            return d
        return {}

    @classmethod
    def from_dict(cls, data: dict) -> "Message":
        """Deserialize from JSONL."""
        parts = []
        for p in data.get("parts", []):
            if p["type"] == "text":
                parts.append(TextPart(text=p.get("text", "")))
            elif p["type"] == "context":
                parts.append(
                    ContextPart(
                        uri=p["uri"],
                        context_type=p.get("context_type", "memory"),
                        abstract=p.get("abstract", ""),
                    )
                )
            elif p["type"] == "tool":
                parts.append(
                    ToolPart(
                        tool_id=p["tool_id"],
                        tool_name=p["tool_name"],
                        tool_uri=p["tool_uri"],
                        skill_uri=p.get("skill_uri", ""),
                        tool_input=p.get("tool_input"),
                        tool_output=p.get("tool_output", ""),
                        tool_status=p.get("tool_status", "pending"),
                        duration_ms=p.get("duration_ms"),
                        prompt_tokens=p.get("prompt_tokens"),
                        completion_tokens=p.get("completion_tokens"),
                    )
                )
        return cls(
            id=data["id"],
            role=data["role"],
            parts=parts,
            created_at=parse_iso_datetime(data["created_at"]),
        )

    @classmethod
    def create_user(cls, content: str, msg_id: str = None) -> "Message":
        """Create user message."""
        from uuid import uuid4

        return cls(
            id=msg_id or f"msg_{uuid4().hex}",
            role="user",
            parts=[TextPart(text=content)],
            created_at=datetime.now(timezone.utc),
        )

    @classmethod
    def create_assistant(
        cls,
        content: str = "",
        context_refs: List[dict] = None,
        tool_calls: List[dict] = None,
        msg_id: str = None,
    ) -> "Message":
        """Create assistant message."""
        from uuid import uuid4

        parts: List[Part] = []
        if content:
            parts.append(TextPart(text=content))

        for ref in context_refs or []:
            parts.append(
                ContextPart(
                    uri=ref.get("uri", ""),
                    context_type=ref.get("context_type", "memory"),
                    abstract=ref.get("abstract", ""),
                )
            )

        for tc in tool_calls or []:
            parts.append(
                ToolPart(
                    tool_id=tc.get("id", ""),
                    tool_name=tc.get("name", ""),
                    tool_uri=tc.get("uri", ""),
                    skill_uri=tc.get("skill_uri", ""),
                    tool_input=tc.get("input"),
                    tool_status=tc.get("status", "pending"),
                )
            )

        return cls(
            id=msg_id or f"msg_{uuid4().hex}",
            role="assistant",
            parts=parts,
            created_at=datetime.now(timezone.utc),
        )

    def get_context_parts(self) -> List[ContextPart]:
        """Get all ContextParts."""
        return [p for p in self.parts if isinstance(p, ContextPart)]

    def get_tool_parts(self) -> List[ToolPart]:
        """Get all ToolParts."""
        return [p for p in self.parts if isinstance(p, ToolPart)]

    def find_tool_part(self, tool_id: str) -> Optional[ToolPart]:
        """Find ToolPart by tool_id."""
        for p in self.parts:
            if isinstance(p, ToolPart) and p.tool_id == tool_id:
                return p
        return None

    def to_jsonl(self) -> str:
        """Serialize to JSONL string."""
        return json.dumps(self.to_dict(), ensure_ascii=False)
