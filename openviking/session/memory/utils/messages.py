# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0
"""
Message formatting and memory file parsing utilities.
"""

import json
import re
from typing import Any, Dict, List

import json_repair

from openviking.session.memory.utils import truncate_content
from openviking.telemetry import tracer
from openviking_cli.utils import get_logger

logger = get_logger(__name__)


def pretty_print_messages(messages: List[Dict[str, Any]]) -> None:
    """
    Print messages in a human-readable format.

    Formats messages with [role] headers and indented content for readability.
    Tool calls and results are printed in a way that shows their correspondence.

    Args:
        messages: List of message dictionaries with 'role', 'content', and optional 'tool_calls'
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
                # Handle content as dict (e.g., tool_call format)
                if isinstance(content, dict):
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
                    except:
                        output.append(args_str)
                else:
                    output.append(f"\n[{role} tool_calls]")
                    output.append(json.dumps(tool_calls, indent=2, ensure_ascii=False))

    output.append("\n=== End Messages ===")
    tracer.info("messages=" + "\n".join(output))


def parse_memory_file_with_fields(content: str) -> Dict[str, Any]:
    """
    Parse memory file content with optional MEMORY_FIELDS HTML comment.

    Extracts fields from <!-- MEMORY_FIELDS { ... } --> comment and returns
    the fields merged at top level with the content.

    Args:
        content: Raw file content string

    Returns:
        Dict with {field1: value1, field2: value2, ..., "content": str}
    """
    if not content:
        return {"content": ""}

    # Pattern to match: <!-- MEMORY_FIELDS ... -->
    # Matches multi-line JSON inside the comment
    pattern = r"<!--\s*MEMORY_FIELDS\s*([\s\S]*?)\s*-->"

    match = re.search(pattern, content)

    result = {}

    if match:
        fields_json_str = match.group(1).strip()
        if fields_json_str:
            try:
                fields = json_repair.loads(fields_json_str)
                # If it's a list, take the first item (just in case)
                if isinstance(fields, list) and len(fields) > 0:
                    fields = fields[0]
                if isinstance(fields, dict):
                    result.update(fields)
            except Exception as e:
                tracer.warning(f"Failed to parse MEMORY_FIELDS JSON: {e}")

    # Remove the comment from content
    content_without_comment = re.sub(pattern, "", content).strip()

    content_without_comment = truncate_content(content_without_comment)
    result["content"] = content_without_comment

    return result
