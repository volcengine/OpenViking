# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0
"""
Memory utilities package.
"""

from openviking.session.memory.utils.json_parser import (
    JsonUtils,
    _any_to_str,
    _get_arg_type,
    _get_origin_type,
    extract_json_content,
    parse_json_with_stability,
    parse_value_with_tolerance,
    remove_json_trailing_content,
    value_fault_tolerance,
)
from openviking.session.memory.utils.language import (
    detect_language_from_conversation,
    resolve_output_language,
    resolve_output_language_from_conversation,
    resolve_with_override,
)
from openviking.session.memory.utils.line_numbers import (
    add_line_numbers,
    every_line_has_line_numbers,
    extract_start_line_number,
    line_count,
    slice_content_lines,
    split_content_lines,
    strip_line_numbers,
)
from openviking.session.memory.utils.messages import (
    parse_memory_file_with_fields,
    pretty_print_messages,
)
from openviking.session.memory.utils.model import (
    flat_model_to_dict,
    model_to_dict,
)

__all__ = [
    # MemoryFile + MemoryFileUtils
    "MemoryFileUtils",
    # Language
    "detect_language_from_conversation",
    "resolve_output_language",
    "resolve_output_language_from_conversation",
    "resolve_with_override",
    "add_line_numbers",
    "every_line_has_line_numbers",
    "extract_start_line_number",
    "line_count",
    "slice_content_lines",
    "split_content_lines",
    "strip_line_numbers",
    # Messages
    "pretty_print_messages",
    "parse_memory_file_with_fields",
    # URI
    "generate_uri",
    "validate_uri_template",
    "is_uri_allowed",
    # JSON Parser
    "extract_json_content",
    "remove_json_trailing_content",
    "parse_json_with_stability",
    "value_fault_tolerance",
    "parse_value_with_tolerance",
    "JsonUtils",
    "_get_origin_type",
    "_get_arg_type",
    "_any_to_str",
    # Model
    "model_to_dict",
    "flat_model_to_dict",
]


def __getattr__(name: str):
    if name == "MemoryFileUtils":
        from openviking.session.memory.utils.memory_file_utils import MemoryFileUtils

        return MemoryFileUtils
    if name in {"generate_uri", "is_uri_allowed", "validate_uri_template"}:
        from openviking.session.memory.utils.uri import (
            generate_uri,
            is_uri_allowed,
            validate_uri_template,
        )

        return {
            "generate_uri": generate_uri,
            "is_uri_allowed": is_uri_allowed,
            "validate_uri_template": validate_uri_template,
        }[name]
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
