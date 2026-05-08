# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0
"""
Memory Templating System for OpenViking.

This module provides a YAML-configurable memory templating system with
ReAct (Reasoning + Action) pattern for memory updates.
"""

from openviking.session.memory.dataclass import (
    MemoryData,
    MemoryField,
    MemoryOperations,
    MemoryTypeSchema,
    StructuredMemoryOperations,
)
from openviking.session.memory.extract_loop import (
    ExtractLoop,
)
from openviking.session.memory.memory_type_registry import MemoryTypeRegistry
from openviking.session.memory.memory_updater import MemoryUpdater, MemoryUpdateResult
from openviking.session.memory.merge_op import FieldType, MemoryPatchHandler, MergeOp
from openviking.session.memory.schema_model_generator import (
    SchemaModelGenerator,
    SchemaPromptGenerator,
)
from openviking.session.memory.tools import (
    MemoryLsTool,
    MemoryReadTool,
    MemorySearchTool,
    MemoryTool,
    add_tool_call_pair_to_messages,
    get_tool,
    register_tool,
)
from openviking.session.memory.utils import (
    detect_language_from_conversation,
    generate_uri,
    is_uri_allowed,
    pretty_print_messages,
    validate_uri_template,
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
    # ExtractLoop
    "ExtractLoop",
    # Tools (Tool implementations)
    "MemoryTool",
    "MemoryReadTool",
    "MemorySearchTool",
    "MemoryLsTool",
    "register_tool",
    "get_tool",
    "add_tool_call_pair_to_messages",
    # Language utilities and helpers
    "detect_language_from_conversation",
    "pretty_print_messages",
    # URI utilities
    "generate_uri",
    "validate_uri_template",
    "is_uri_allowed",
]
