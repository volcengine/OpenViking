# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0
"""Human-readable formatting for LLM message lists (shared by memory and VLM layers)."""

import json
from typing import Any, Dict, List


def sanitize_openai_messages(messages: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """Return a copy without structurally empty assistant messages.

    OpenAI-compatible chat APIs reject an assistant message unless it has
    meaningful content or a tool/function call. Persisted sessions may still
    contain empty assistant turns as context markers, so sanitize only at the
    provider request boundary.
    """
    sanitized: List[Dict[str, Any]] = []
    for message in messages:
        if not isinstance(message, dict):
            # Non-dict entries are passed through untouched; only dict messages
            # carry the role/content structure this filter reasons about.
            sanitized.append(message)
            continue
        copied = dict(message)
        if copied.get("role") == "assistant":
            has_content = _has_message_content(copied.get("content"))
            has_tool_call = bool(copied.get("tool_calls") or copied.get("function_call"))
            if not has_content and not has_tool_call:
                continue
        sanitized.append(copied)
    return sanitized


def _has_message_content(content: Any) -> bool:
    if isinstance(content, str):
        return bool(content.strip())
    if isinstance(content, list):
        return any(_has_content_part(part) for part in content)
    return bool(content)


def _has_content_part(part: Any) -> bool:
    if isinstance(part, str):
        return bool(part.strip())
    if not isinstance(part, dict):
        return part is not None
    for key in ("text", "image_url", "input_image", "refusal"):
        value = part.get(key)
        if isinstance(value, str) and value.strip():
            return True
        if value and not isinstance(value, str):
            return True
    return False


def format_messages(messages: List[Dict[str, Any]]) -> str:
    """Render a chat message list in a human-readable ``[role]``-headed layout.

    Tool calls and results are shown so their correspondence is visible. Returns
    the formatted string; callers decide how to log/trace it.

    Args:
        messages: List of message dicts with 'role', 'content', and optional 'tool_calls'.
    """
    output = ["=== Messages ==="]
    for msg in messages:
        role = msg.get("role", "unknown")
        content = msg.get("content", "")

        if role == "tool_call":
            # Optimized tool call format - print as JSON to match stored format
            output.append(f"\n[{role}]")
            output.append(json.dumps(msg, ensure_ascii=False, indent=2))
        elif role == "tool":
            # Legacy tool result format
            tool_call_id = msg.get("tool_call_id", "")
            output.append(f"\n[{role}] (id={tool_call_id})")
            if content:
                try:
                    result_json = json.loads(content)
                    output.append(json.dumps(result_json, indent=2, ensure_ascii=False))
                except (json.JSONDecodeError, TypeError):
                    output.append(content)
        else:
            if content:
                output.append(f"\n[{role}]")
                # Structured/multimodal content is easier to inspect as JSON and
                # must be stringified before joining the output lines.
                if not isinstance(content, str):
                    output.append(json.dumps(content, ensure_ascii=False, indent=2))
                else:
                    output.append(content)

            if "tool_calls" in msg and msg["tool_calls"]:
                # Legacy tool call format
                tool_calls = msg["tool_calls"]
                if len(tool_calls) == 1:
                    tc = tool_calls[0]
                    tc_id = tc.get("id", "")
                    tc_name = tc.get("function", {}).get("name", "")
                    output.append(f"\n[{role} tool_call] (id={tc_id}, name={tc_name})")
                    args_str = tc.get("function", {}).get("arguments", {})
                    try:
                        args_json = json.loads(args_str)
                        output.append(json.dumps(args_json, indent=2, ensure_ascii=False))
                    except Exception:
                        output.append(args_str)
                else:
                    output.append(f"\n[{role} tool_calls]")
                    output.append(json.dumps(tool_calls, indent=2, ensure_ascii=False))

    output.append("\n=== End Messages ===")
    return "\n".join(output)
