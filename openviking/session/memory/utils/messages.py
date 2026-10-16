# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0
"""
Message formatting and memory file parsing utilities.
"""

from typing import Any, Dict, List

import json_repair

from openviking.session.memory.utils.memory_fields import MEMORY_FIELDS_COMMENT_RE
from openviking.telemetry import tracer
from openviking.utils.message_format import format_messages
from openviking_cli.utils import get_logger

logger = get_logger(__name__)


def pretty_print_messages(messages: List[Dict[str, Any]]) -> None:
    """Trace messages in a human-readable format (see utils.message_format)."""
    tracer.info("llm_input_messages=" + format_messages(messages))


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

    match = MEMORY_FIELDS_COMMENT_RE.search(content)

    result = {}

    if match:
        fields_json_str = match.group("fields").strip()
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
    content_without_comment = MEMORY_FIELDS_COMMENT_RE.sub("", content).strip()
    result["content"] = content_without_comment

    return result
