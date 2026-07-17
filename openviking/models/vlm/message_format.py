# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0
"""Human-readable formatting for VLM chat messages."""

from __future__ import annotations

import json
from typing import Any, Dict, List


def _format_json_value(value: Any) -> str:
    decoded = value
    if isinstance(value, str):
        try:
            decoded = json.loads(value)
        except json.JSONDecodeError:
            return value
    try:
        return json.dumps(decoded, ensure_ascii=False, indent=2)
    except (TypeError, ValueError):
        try:
            return str(value)
        except Exception:
            return f"<{type(value).__qualname__}>"


def format_messages(messages: List[Dict[str, Any]]) -> str:
    """Format chat messages with readable role and tool-exchange sections."""

    output = ["=== Messages ==="]
    for msg in messages:
        role = msg.get("role", "unknown")
        content = msg.get("content", "")

        if role == "tool_call":
            output.append(f"\n[{role}]")
            output.append(_format_json_value(msg))
        elif role == "tool":
            tool_call_id = msg.get("tool_call_id", "")
            output.append(f"\n[{role}] (id={tool_call_id})")
            if content:
                output.append(_format_json_value(content))
        else:
            if content:
                output.append(f"\n[{role}]")
                output.append(content if isinstance(content, str) else _format_json_value(content))

            if "tool_calls" in msg and msg["tool_calls"]:
                tool_calls = msg["tool_calls"]
                if len(tool_calls) == 1:
                    tool_call = tool_calls[0]
                    tool_call_id = tool_call.get("id", "")
                    tool_name = tool_call.get("function", {}).get("name", "")
                    output.append(f"\n[{role} tool_call] (id={tool_call_id}, name={tool_name})")
                    arguments = tool_call.get("function", {}).get("arguments", {})
                    output.append(_format_json_value(arguments))
                else:
                    output.append(f"\n[{role} tool_calls]")
                    output.append(_format_json_value(tool_calls))

    output.append("\n=== End Messages ===")
    return "\n".join(output)
