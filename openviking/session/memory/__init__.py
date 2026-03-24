# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: Apache-2.0
"""
Memory Templating System for OpenViking.

This module provides a YAML-configurable memory templating system with
ReAct (Reasoning + Action) pattern for memory updates.
"""

from openviking.session.memory.utils import (
    detect_language_from_conversation,
    generate_uri,
    is_uri_allowed,
    is_uri_allowed_for_schema,
    pretty_print_messages,
    resolve_all_operations,
    validate_uri_template,
)
from openviking.session.memory.dataclass import (
    MemoryData,
    MemoryField,
    MemoryOperations,
    MemoryTypeSchema,
    StructuredMemoryOperations,
)
from openviking.session.memory.merge_op import MergeOp, FieldType, MemoryPatchHandler
from openviking.session.memory.memory_react import (
    MemoryReAct,
)
from openviking.session.memory.memory_type_registry import MemoryTypeRegistry
from openviking.session.memory.memory_updater import MemoryUpdater, MemoryUpdateResult
from openviking.session.memory.schema_model_generator import (
    SchemaModelGenerator,
    SchemaPromptGenerator,
)
from openviking.session.memory.tools import (
    MemorySearchTool,
    MemoryLsTool,
    MemoryReadTool,
    MemoryTool,
    add_tool_call_items_to_messages,
    add_tool_call_pair_to_messages,
    create_tool_call_message,
    create_tool_result_message,
    get_tool,
    get_tool_schemas,
    list_tools,
    register_tool,
)

__all__ = [
    # Data structures
    "FieldType",
    "MergeOp",
    "MemoryField",
    "MemoryTypeSchema",
    "MemoryData",
    # Operations
    "MemoryOperations",
    "StructuredMemoryOperations",
    # Registry
    "MemoryTypeRegistry",
    # Schema models
    "SchemaModelGenerator",
    "SchemaPromptGenerator",
    # Patch
    "MemoryPatchHandler",
    # Updater
    "MemoryUpdater",
    "MemoryUpdateResult",
    # ReAct
    "MemoryReAct",
    # Tools (Tool implementations)
    "MemoryTool",
    "MemoryReadTool",
    "MemorySearchTool",
    "MemoryLsTool",
    "register_tool",
    "get_tool",
    "list_tools",
    "get_tool_schemas",
    "create_tool_call_message",
    "create_tool_result_message",
    "add_tool_call_pair_to_messages",
    "add_tool_call_items_to_messages",
    # Language utilities and helpers
    "detect_language_from_conversation",
    "pretty_print_messages",
    # URI utilities
    "generate_uri",
    "validate_uri_template",
    "resolve_all_operations",
    "is_uri_allowed",
    "is_uri_allowed_for_schema",
]
