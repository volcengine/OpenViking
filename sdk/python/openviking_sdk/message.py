from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Dict, Literal, Optional, Union


@dataclass
class TextPart:
    text: str = ""
    type: Literal["text"] = "text"


@dataclass
class ContextPart:
    uri: str = ""
    context_type: Literal["memory", "resource", "skill"] = "memory"
    abstract: str = ""
    type: Literal["context"] = "context"


@dataclass
class ImagePart:
    url: str = ""
    detail: Optional[str] = None
    type: Literal["image_url"] = "image_url"


@dataclass
class ToolPart:
    tool_id: str = ""
    tool_name: str = ""
    tool_uri: str = ""
    skill_uri: str = ""
    tool_input: Optional[Dict[str, Any]] = None
    tool_output: str = ""
    tool_status: str = "pending"
    duration_ms: Optional[float] = None
    prompt_tokens: Optional[int] = None
    completion_tokens: Optional[int] = None
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
    type: Literal["tool"] = "tool"


MessagePart = Union[TextPart, ContextPart, ImagePart, ToolPart]


def normalize_part(part: Union[Dict[str, Any], MessagePart, Any]) -> Dict[str, Any]:
    if isinstance(part, dict):
        return dict(part)

    part_type = getattr(part, "type", None)
    if part_type == "text":
        return {"type": "text", "text": getattr(part, "text", "")}
    if part_type == "context":
        return {
            "type": "context",
            "uri": getattr(part, "uri", ""),
            "context_type": getattr(part, "context_type", "memory"),
            "abstract": getattr(part, "abstract", ""),
        }
    if part_type == "image_url":
        image_url = {"url": getattr(part, "url", "")}
        detail = getattr(part, "detail", None)
        if detail is not None:
            image_url["detail"] = detail
        return {"type": "image_url", "image_url": image_url}
    if part_type == "tool":
        payload = {
            "type": "tool",
            "tool_id": getattr(part, "tool_id", ""),
            "tool_name": getattr(part, "tool_name", ""),
            "tool_status": getattr(part, "tool_status", "pending"),
        }
        for field in (
            "tool_uri",
            "skill_uri",
            "tool_input",
            "tool_output",
            "tool_output_ref",
            "tool_output_sha256",
            "tool_output_storage_uri",
            "tool_output_source_ref",
            "tool_output_externalization_error",
            "tool_output_group_id",
            "tool_output_externalized_reason",
        ):
            value = getattr(part, field, None)
            if value:
                payload[field] = value
        for field in (
            "duration_ms",
            "prompt_tokens",
            "completion_tokens",
            "tool_output_original_chars",
            "tool_output_preview_chars",
            "tool_output_source_offset",
            "tool_output_source_limit",
            "tool_output_group_original_chars",
            "tool_output_group_budget_chars",
        ):
            value = getattr(part, field, None)
            if value is not None:
                payload[field] = value
        if getattr(part, "tool_output_truncated", False):
            payload["tool_output_truncated"] = True
        tool_output_mime_type = getattr(part, "tool_output_mime_type", "text/plain")
        if tool_output_mime_type and tool_output_mime_type != "text/plain":
            payload["tool_output_mime_type"] = tool_output_mime_type
        return payload

    raise TypeError(
        "parts must contain dictionaries or TextPart, ContextPart, ImagePart, or ToolPart"
    )
