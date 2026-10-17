# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0
"""Human-readable formatting for LLM message lists (shared by memory and VLM layers)."""

import hashlib
import json
from typing import Any, Dict, List, Optional

OPENAI_TOOL_CALL_ID_MAX_LENGTH = 40


def _tool_call_id(item: Dict[str, Any], keys: tuple[str, ...]) -> Optional[str]:
    for key in keys:
        value = item.get(key)
        if isinstance(value, str):
            return value
    return None


def _replacement_tool_call_id(
    original: str,
    occurrence: int,
    used_ids: set[str],
) -> str:
    collision = 0
    while True:
        occurrence_material = original if occurrence == 0 else f"{original}\0{occurrence}"
        material = (
            occurrence_material
            if collision == 0
            else f"{occurrence_material}\0collision\0{collision}"
        )
        candidate = f"call_{hashlib.sha256(material.encode('utf-8')).hexdigest()[:35]}"
        if candidate not in used_ids:
            return candidate
        collision += 1


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
    """Make paired tool-call IDs valid for one OpenAI-family request.

    Some providers embed opaque signatures in tool-call IDs. Those IDs can be
    longer than the OpenAI limit or reused across turns when persisted history
    is replayed across providers. Rewrite only oversized or later reused call
    occurrences at the target request boundary. Pair results in call order.
    """
    used_ids: set[str] = set()
    for message in messages:
        if not isinstance(message, dict):
            continue
        if message.get("role") == "assistant":
            for tool_call in message.get("tool_calls") or []:
                if not isinstance(tool_call, dict):
                    continue
                for key in ("id", "call_id"):
                    tool_call_id = tool_call.get(key)
                    if (
                        isinstance(tool_call_id, str)
                        and len(tool_call_id) <= OPENAI_TOOL_CALL_ID_MAX_LENGTH
                    ):
                        used_ids.add(tool_call_id)
        elif message.get("role") == "tool":
            for key in ("tool_call_id", "call_id"):
                tool_call_id = message.get(key)
                if (
                    isinstance(tool_call_id, str)
                    and len(tool_call_id) <= OPENAI_TOOL_CALL_ID_MAX_LENGTH
                ):
                    used_ids.add(tool_call_id)

    occurrences: Dict[str, int] = {}
    pending: Dict[str, List[str]] = {}
    consumed: Dict[str, int] = {}
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

                original = _tool_call_id(tool_call, ("id", "call_id"))
                if original is None:
                    tool_calls.append(tool_call)
                    continue

                occurrence = occurrences.get(original, 0)
                occurrences[original] = occurrence + 1
                replacement = original
                if len(original) > OPENAI_TOOL_CALL_ID_MAX_LENGTH or occurrence > 0:
                    replacement = _replacement_tool_call_id(original, occurrence, used_ids)
                    used_ids.add(replacement)
                pending.setdefault(original, []).append(replacement)

                if replacement == original:
                    tool_calls.append(tool_call)
                    continue

                copied_tool_call = dict(tool_call)
                for key in ("id", "call_id"):
                    if tool_call.get(key) == original:
                        copied_tool_call[key] = replacement
                tool_calls.append(copied_tool_call)
                changed = True
            if changed:
                copied = dict(message)
                copied["tool_calls"] = tool_calls
        elif message.get("role") == "tool":
            original = _tool_call_id(message, ("tool_call_id", "call_id"))
            if original is None:
                normalized.append(copied)
                continue
            result_index = consumed.get(original, 0)
            call_ids = pending.get(original, [])
            if result_index >= len(call_ids):
                normalized.append(copied)
                continue
            consumed[original] = result_index + 1
            replacement = call_ids[result_index]
            if replacement == original:
                normalized.append(copied)
                continue
            for key in ("tool_call_id", "call_id"):
                if message.get(key) != original:
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
