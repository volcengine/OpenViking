# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0
"""
Memory content serialization with metadata in HTML comments.

This module handles the serialization and deserialization of memory content
with metadata stored in HTML comments at the end of the file.
"""

import json
import re
from datetime import datetime
from typing import Any, Dict, Optional, Tuple

from openviking.utils.time_utils import parse_iso_datetime

# Regex pattern to match the MEMORY_FIELDS HTML comment
MEMORY_FIELDS_PATTERN = re.compile(r"\n\n<!--\s*MEMORY_FIELDS\s*\n(.*?)\n-->", re.DOTALL)

# Alternative pattern that might appear at the end without leading newlines
MEMORY_FIELDS_PATTERN_END = re.compile(r"<!--\s*MEMORY_FIELDS\s*\n(.*?)\n-->$", re.DOTALL)


def _serialize_datetime(obj: Any) -> Any:
    """Serialize datetime objects to ISO format strings."""
    if isinstance(obj, datetime):
        return obj.isoformat()
    raise TypeError(f"Object of type {obj.__class__.__name__} is not JSON serializable")


def _deserialize_datetime(metadata: Dict[str, Any]) -> Dict[str, Any]:
    """Deserialize ISO format datetime strings back to datetime objects."""
    result = metadata.copy()
    for key in ["created_at", "updated_at"]:
        if key in result and isinstance(result[key], str):
            try:
                result[key] = parse_iso_datetime(result[key])
            except (ValueError, TypeError):
                # Keep as string if parsing fails
                pass
    return result


def serialize_with_metadata(
    metadata: Dict[str, Any],
    content_template: str = None,
    extract_context: Any = None,
) -> str:
    """
    Serialize content and metadata into a single string.

    The metadata is stored in an HTML comment at the end of the content.

    Args:
        metadata: Dictionary containing all fields including "content".
                  content is extracted and used as the main body.
        content_template: Optional Jinja2 template to render content.
        extract_context: Optional context for template rendering.

    Returns:
        Combined string with content followed by metadata in HTML comment
    """
    # Extract content from metadata (default to empty string)
    content = metadata.pop("content", "") or ""

    # Render template if provided
    if content_template:
        try:
            import jinja2
            from jinja2 import Environment

            env = Environment(autoescape=False, undefined=jinja2.DebugUndefined)
            template_vars = metadata.copy()
            template_vars["extract_context"] = extract_context

            jinja_template = env.from_string(content_template)
            content = jinja_template.render(**template_vars).strip()
        except Exception:
            # If template rendering fails, use content as-is
            pass

    # Restore metadata (we popped content earlier)
    # Note: metadata dict is modified in place, caller should be aware

    # Clean metadata - remove None values and memory_type
    clean_metadata = {k: v for k, v in metadata.items() if v is not None and k != "memory_type"}

    if not clean_metadata:
        return content

    # Serialize metadata to JSON with datetime handling
    metadata_json = json.dumps(
        clean_metadata, indent=2, default=_serialize_datetime, ensure_ascii=False
    )

    # Combine content and metadata
    comment = f"\n\n<!-- MEMORY_FIELDS\n{metadata_json}\n-->"

    # If content is empty, just return the comment (but trim leading newlines)
    if not content or not content.strip():
        return comment.lstrip()

    return content + comment


def deserialize_content(full_content: str) -> str:
    """
    Extract the main content from a serialized string (strip metadata comment).

    Args:
        full_content: Complete content including metadata comment

    Returns:
        The main content without the metadata comment
    """
    if not full_content:
        return ""

    # Try to remove the MEMORY_FIELDS comment
    content = MEMORY_FIELDS_PATTERN.sub("", full_content)

    # If no match, check if it's at the very end
    if content == full_content:
        content = MEMORY_FIELDS_PATTERN_END.sub("", content)

    return content.rstrip()


def deserialize_metadata(full_content: str) -> Optional[Dict[str, Any]]:
    """
    Extract and parse metadata from a serialized string.

    Args:
        full_content: Complete content including metadata comment

    Returns:
        Parsed metadata dictionary, or None if no metadata found
    """
    if not full_content:
        return None

    # Try to find the MEMORY_FIELDS comment
    match = MEMORY_FIELDS_PATTERN.search(full_content)
    if not match:
        match = MEMORY_FIELDS_PATTERN_END.search(full_content)

    if not match:
        return None

    try:
        json_str = match.group(1).strip()
        metadata = json.loads(json_str)
        return _deserialize_datetime(metadata)
    except (json.JSONDecodeError, IndexError, AttributeError):
        # Failed to parse, return None
        return None


def deserialize_full(full_content: str) -> Tuple[str, Optional[Dict[str, Any]]]:
    """
    Extract both content and metadata from a serialized string.

    Args:
        full_content: Complete content including metadata comment

    Returns:
        Tuple of (content, metadata) where metadata may be None
    """
    content = deserialize_content(full_content)
    metadata = deserialize_metadata(full_content)
    return content, metadata


# 默认截断配置
DEFAULT_TRUNCATE_MAX_CHARS = 1000


def truncate_content(content: str, max_chars: int = DEFAULT_TRUNCATE_MAX_CHARS) -> str:
    """
    Truncate content to max_chars while keeping complete lines.

    Args:
        content: Content to truncate
        max_chars: Maximum number of characters to keep (default: 1000)

    Returns:
        Truncated content with truncation note appended
    """
    if len(content) <= max_chars:
        return content

    # 从 max_chars 位置向前找最近的换行符，保持完整行
    truncated = content[:max_chars]
    last_newline = truncated.rfind("\n")
    if last_newline > 0:
        truncated = truncated[:last_newline]

    return truncated + f"\n... [truncated {len(content) - len(truncated)} chars]"
