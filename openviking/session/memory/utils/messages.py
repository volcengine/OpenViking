# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0
"""
Message formatting and memory file parsing utilities.
"""

import re
from typing import Any, Dict, List

import json_repair

from openviking.models.vlm.message_format import format_messages
from openviking.telemetry import tracer
from openviking_cli.utils import get_logger

logger = get_logger(__name__)


def pretty_print_messages(messages: List[Dict[str, Any]]) -> None:
    """Record messages in the shared human-readable format."""

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

    # Remove the comment from content.  When MEMORY_FIELDS itself contains a
    # structured ``content`` field, prefer that value as the source of truth.
    # The visible markdown body may be a schema ``content_template`` rendering
    # (for example experiences render template-only metadata), so using the rendered
    # body as the parsed content would make future updates depend on reversing
    # template-specific markdown.
    content_without_comment = re.sub(pattern, "", content).strip()
    result.setdefault("content", content_without_comment)

    return result
