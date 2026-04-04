# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0
"""
Memory utilities package.
"""

from openviking.session.memory.utils.content import (
    deserialize_content,
    deserialize_full,
    deserialize_metadata,
    serialize_with_metadata,
    truncate_content,
)
from openviking.session.memory.utils.json_parser import (
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
)
from openviking.session.memory.utils.messages import (
    parse_memory_file_with_fields,
    pretty_print_messages,
)
from openviking.session.memory.utils.model import (
    flat_model_to_dict,
    model_to_dict,
)
from openviking.session.memory.utils.uri import (
    ResolvedOperation,
    ResolvedOperations,
    collect_allowed_directories,
    collect_allowed_path_patterns,
    extract_uri_fields_from_flat_model,
    generate_uri,
    is_uri_allowed,
    is_uri_allowed_for_schema,
    resolve_all_operations,
    resolve_flat_model_uri,
    validate_operations_uris,
    validate_uri_template,
)

__all__ = [
    # Content serialization
    "serialize_with_metadata",
    "deserialize_content",
    "deserialize_metadata",
    "deserialize_full",
    "truncate_content",
    # Language
    "detect_language_from_conversation",
    # Messages
    "pretty_print_messages",
    "parse_memory_file_with_fields",
    # URI
    "generate_uri",
    "validate_uri_template",
    "collect_allowed_directories",
    "collect_allowed_path_patterns",
    "is_uri_allowed",
    "is_uri_allowed_for_schema",
    "extract_uri_fields_from_flat_model",
    "resolve_flat_model_uri",
    "ResolvedOperation",
    "ResolvedOperations",
    "resolve_all_operations",
    "validate_operations_uris",
    # JSON Parser
    "extract_json_content",
    "remove_json_trailing_content",
    "parse_json_with_stability",
    "value_fault_tolerance",
    "parse_value_with_tolerance",
    "_get_origin_type",
    "_get_arg_type",
    "_any_to_str",
    # Model
    "model_to_dict",
    "flat_model_to_dict",
]
