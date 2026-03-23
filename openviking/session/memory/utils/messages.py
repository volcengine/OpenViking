# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: Apache-2.0
"""
Message formatting and memory file parsing utilities.
"""

import json
import re
from typing import Any, Dict, List

import json_repair

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
    def _format_tool_call(tc: Dict[str, Any]) -> Dict[str, Any]:
        """Format a single tool call, pretty-printing arguments if it's JSON."""
        tc_copy = dict(tc)
        if "function" in tc_copy and "arguments" in tc_copy["function"]:
            args_str = tc_copy["function"]["arguments"]
            if isinstance(args_str, str):
                try:
                    # Try to parse and pretty-print the arguments
                    args_json = json.loads(args_str)
                    tc_copy["function"] = dict(tc_copy["function"])
                    tc_copy["function"]["arguments"] = args_json
                except (json.JSONDecodeError, TypeError):
                    # If it's not valid JSON, leave it as is
                    pass
        return tc_copy

    print("=== Messages ===")
    for msg in messages:
        role = msg.get("role", "unknown")
        content = msg.get("content", "")

        if role == "tool":
            # Tool result - show correspondence with tool_call_id
            tool_call_id = msg.get("tool_call_id", "")
            print(f"\n[{role}] (id={tool_call_id})")
            if content:
                # Try to pretty-print tool result if it's JSON
                try:
                    result_json = json.loads(content)
                    print(json.dumps(result_json, indent=2, ensure_ascii=False))
                except (json.JSONDecodeError, TypeError):
                    # If it's not valid JSON, print as is
                    print(content)
        else:
            if content:
                print(f"\n[{role}]")
                print(content)

            if "tool_calls" in msg and msg["tool_calls"]:
                tool_calls = msg["tool_calls"]
                if len(tool_calls) == 1:
                    # Single tool call - show its id
                    tc = tool_calls[0]
                    tc_id = tc.get("id", "")
                    tc_name = tc.get("function", {}).get("name", "")
                    print(f"\n[{role} tool_call] (id={tc_id}, name={tc_name})")
                    formatted_tc = _format_tool_call(tc)
                    print(json.dumps(formatted_tc, indent=2, ensure_ascii=False))
                else:
                    # Multiple tool calls
                    print(f"\n[{role} tool_calls]")
                    formatted_tcs = [_format_tool_call(tc) for tc in tool_calls]
                    print(json.dumps(formatted_tcs, indent=2, ensure_ascii=False))

    print("\n=== End Messages ===")


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
                logger.warning(f"Failed to parse MEMORY_FIELDS JSON: {e}")

    # Remove the comment from content
    content_without_comment = re.sub(pattern, "", content).strip()
    result["content"] = content_without_comment

    return result
