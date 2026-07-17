# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0
"""Human-readable formatting for VLM chat messages."""

from __future__ import annotations

import json
from typing import Any, Dict, List


def format_messages(messages: List[Dict[str, Any]]) -> str:
    """Format chat messages with readable role and tool-exchange sections."""

    output = ["=== Messages ==="]
    for msg in messages:
        role = msg.get("role", "unknown")
        content = msg.get("content", "")

        if role == "tool_call":
            output.append(f"\n[{role}]")
            output.append(json.dumps(msg, ensure_ascii=False, indent=2))
        elif role == "tool":
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
                if isinstance(content, dict):
                    output.append(json.dumps(content, ensure_ascii=False, indent=2))
                else:
                    output.append(content)

            if "tool_calls" in msg and msg["tool_calls"]:
                tool_calls = msg["tool_calls"]
                if len(tool_calls) == 1:
                    tool_call = tool_calls[0]
                    tool_call_id = tool_call.get("id", "")
                    tool_name = tool_call.get("function", {}).get("name", "")
                    output.append(f"\n[{role} tool_call] (id={tool_call_id}, name={tool_name})")
                    arguments = tool_call.get("function", {}).get("arguments", {})
                    try:
                        output.append(
                            json.dumps(json.loads(arguments), indent=2, ensure_ascii=False)
                        )
                    except (json.JSONDecodeError, TypeError):
                        output.append(arguments)
                else:
                    output.append(f"\n[{role} tool_calls]")
                    output.append(json.dumps(tool_calls, indent=2, ensure_ascii=False))

    output.append("\n=== End Messages ===")
    return "\n".join(output)
