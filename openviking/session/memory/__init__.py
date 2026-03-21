# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: Apache-2.0
"""
Memory Templating System for OpenViking.

This module provides a YAML-configurable memory templating system with
ReAct (Reasoning + Action) pattern for memory updates.
"""

from openviking.session.memory.memory_utils import (
    detect_language_from_conversation,
    generate_uri,
    is_uri_allowed,
    is_uri_allowed_for_schema,
    pretty_print_messages,
    resolve_all_operations,
    validate_uri_template,
)
from openviking.session.memory.memory_data import (
    FieldType,
    MemoryData,
    MemoryField,
    MemoryType,
    MemoryTypeSchema,
    MergeOp,
)
from openviking.session.memory.memory_operations import (
    MemoryOperations,
    StructuredMemoryOperations,
)
from openviking.session.memory.memory_patch import MemoryPatchHandler
from openviking.session.memory.memory_react import (
    ActionType,
    MemoryReAct,
    ReadAction,
)
from openviking.session.memory.memory_types import MemoryTypeRegistry
from openviking.session.memory.memory_updater import MemoryUpdater, MemoryUpdateResult
from openviking.session.memory.schema_models import (
    SchemaModelGenerator,
    SchemaPromptGenerator,
)
from openviking.session.memory.tools import (
    MemoryFindTool,
    MemoryLsTool,
    MemoryReadTool,
    MemoryTool,
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
    "MemoryType",
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
    "ActionType",
    "ReadAction",
    "MemoryReAct",
    # Tools (Tool implementations)
    "MemoryTool",
    "MemoryReadTool",
    "MemoryFindTool",
    "MemoryLsTool",
    "MemoryTreeTool",
    "register_tool",
    "get_tool",
    "list_tools",
    "get_tool_schemas",
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
