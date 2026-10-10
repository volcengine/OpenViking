# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0
"""Human-readable formatting for LLM message lists (shared by memory and VLM layers)."""

import hashlib
import json
from typing import Any, Dict, List

OPENAI_TOOL_CALL_ID_MAX_LENGTH = 40


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


def normalize_openai_tool_call_ids(
    messages: List[Dict[str, Any]],
) -> List[Dict[str, Any]]:
    """Bound paired tool-call IDs for an OpenAI-family request.

    Some providers embed opaque signatures in tool-call IDs. Those IDs can be
    longer than the OpenAI limit when persisted history is replayed across
    providers. Rewrite only oversized IDs at the target request boundary, and
    use the same deterministic replacement for the call and its tool result.
    """
    all_ids: set[str] = set()
    for message in messages:
        if not isinstance(message, dict):
            continue
        if message.get("role") == "assistant":
            for tool_call in message.get("tool_calls") or []:
                if not isinstance(tool_call, dict):
                    continue
                for key in ("id", "call_id"):
                    tool_call_id = tool_call.get(key)
                    if isinstance(tool_call_id, str):
                        all_ids.add(tool_call_id)
        elif message.get("role") == "tool":
            for key in ("tool_call_id", "call_id"):
                tool_call_id = message.get(key)
                if isinstance(tool_call_id, str):
                    all_ids.add(tool_call_id)

    used_ids = {
        tool_call_id
        for tool_call_id in all_ids
        if len(tool_call_id) <= OPENAI_TOOL_CALL_ID_MAX_LENGTH
    }
    replacements: Dict[str, str] = {}
    for original in sorted(all_ids):
        if len(original) <= OPENAI_TOOL_CALL_ID_MAX_LENGTH:
            continue
        collision_index = 0
        while True:
            material = original if collision_index == 0 else f"{original}\0{collision_index}"
            digest = hashlib.sha256(material.encode("utf-8")).hexdigest()
            candidate = f"call_{digest[:35]}"
            if candidate not in used_ids:
                replacements[original] = candidate
                used_ids.add(candidate)
                break
            collision_index += 1

    if not replacements:
        return list(messages)

    normalized: List[Dict[str, Any]] = []
    for message in messages:
        if not isinstance(message, dict):
            normalized.append(message)
            continue
        copied = message
        if message.get("role") == "assistant" and isinstance(message.get("tool_calls"), list):
            tool_calls = []
            changed = False
            for tool_call in message["tool_calls"]:
                if not isinstance(tool_call, dict):
                    tool_calls.append(tool_call)
                    continue
                copied_tool_call = dict(tool_call)
                tool_call_changed = False
                for key in ("id", "call_id"):
                    replacement = replacements.get(tool_call.get(key))
                    if replacement is not None:
                        copied_tool_call[key] = replacement
                        tool_call_changed = True
                if not tool_call_changed:
                    tool_calls.append(tool_call)
                    continue
                tool_calls.append(copied_tool_call)
                changed = True
            if changed:
                copied = dict(message)
                copied["tool_calls"] = tool_calls
        elif message.get("role") == "tool":
            for key in ("tool_call_id", "call_id"):
                replacement = replacements.get(message.get(key))
                if replacement is None:
                    continue
                if copied is message:
                    copied = dict(message)
                copied[key] = replacement
        normalized.append(copied)
    return normalized


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
