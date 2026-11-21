# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0
"""
Message formatting and memory file parsing utilities.
"""

from typing import Any, Dict, List

import json_repair

from openviking.session.memory.utils.memory_fields import (
    MEMORY_FIELDS_COMMENT_RE,
    find_memory_fields_trailer,
)
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

    Prefer the trailing metadata comment and return its fields alongside the body.
    Files without a trailer can use an older inline metadata comment.

    Args:
        content: Raw file content string

    Returns:
        Dict with {field1: value1, field2: value2, ..., "content": str}
    """
    if not content:
        return {"content": ""}

    trailer = find_memory_fields_trailer(content)
    match = trailer or MEMORY_FIELDS_COMMENT_RE.search(content)

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
                logger.info(
                    "memory fields metadata parse failed; keeping content error_type=%s",
                    type(e).__name__,
                    extra={"degraded": True, "error_type": type(e).__name__},
                )

    if trailer:
        content = content[: trailer.start()]
    elif match:
        content = MEMORY_FIELDS_COMMENT_RE.sub("", content)
    result["content"] = content.strip()

    return result
