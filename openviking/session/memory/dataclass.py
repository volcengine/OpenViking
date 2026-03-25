# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: Apache-2.0
"""
Core domain data classes for memory system.
"""

from datetime import datetime
from typing import Any, List, Optional, Protocol, TypeVar

from pydantic import BaseModel, Field

from openviking.session.memory.merge_op.base import (
    FieldType,
    MergeOp,
    SearchReplaceBlock,
    StrPatch,
    get_python_type_for_field,
)


T = TypeVar('T')


# ============================================================================
# Memory Field and Schema Definitions
# ============================================================================


class MemoryField(BaseModel):
    """Memory field definition."""

    name: str = Field(..., description="Field name")
    field_type: FieldType = Field(..., description="Field type")
    description: str = Field("", description="Field description")
    merge_op: MergeOp = Field(MergeOp.PATCH, description="Merge strategy")


class MemoryTypeSchema(BaseModel):
    """Memory type schema definition."""

    memory_type: str = Field(..., description="Memory type name")
    description: str = Field("", description="Type description")
    fields: List[MemoryField] = Field(default_factory=list, description="Field definitions")
    filename_template: str = Field("", description="Filename template")
    content_template: Optional[str] = Field(None, description="Content template (for template mode)")
    directory: str = Field("", description="Directory path")
    enabled: bool = Field(True, description="Whether this memory type is enabled")


class MemoryData(BaseModel):
    """Dynamic memory data."""

    memory_type: str = Field(..., description="Memory type name")
    uri: Optional[str] = Field(None, description="Memory URI (for updates)")
    fields: dict[str, Any] = Field(default_factory=dict, description="Dynamic field data")
    abstract: Optional[str] = Field(None, description="L0 abstract")
    overview: Optional[str] = Field(None, description="L1 overview")
    content: Optional[str] = Field(None, description="L2 content")
    name: Optional[str] = Field(None, description="Memory name")
    tags: List[str] = Field(default_factory=list, description="Tags")
    created_at: Optional[datetime] = Field(None, description="Created time")
    updated_at: Optional[datetime] = Field(None, description="Updated time")

    def get_field(self, field_name: str) -> Any:
        """Get field value."""
        return self.fields.get(field_name)

    def set_field(self, field_name: str, value: Any) -> None:
        """Set field value."""
        self.fields[field_name] = value




# ============================================================================
# Memory Operations
# ============================================================================


class MemoryOperationsProtocol(Protocol):
    """Protocol for memory operations (for type checking)."""

    reasoning: str
    write_uris: List[Any]
    edit_uris: List[Any]
    edit_overview_uris: List[Any]
    delete_uris: List[str]

    def is_empty(self) -> bool: ...


class StructuredMemoryOperations(BaseModel):
    """
    DEPRECATED: Placeholder only. The actual model is dynamically generated.

    Use SchemaModelGenerator.create_structured_operations_model() to get
    the actual type-safe implementation with proper union types for write_uris
    and edit_uris.
    """

    reasoning: str = Field(
        '',
        description="reasoning",
    )
    write_uris: List[Any] = Field(
        default_factory=list,
        description="Write operations with flat data format",
    )
    edit_uris: List[Any] = Field(
        default_factory=list,
        description="Edit operations with flat data format",
    )
    edit_overview_uris: List[Any] = Field(
        default_factory=list,
        description="Edit operations for .overview.md files using memory_type",
    )
    delete_uris: List[str] = Field(
        default_factory=list,
        description="Delete operations as URI strings",
    )

    def is_empty(self) -> bool:
        """Check if there are any operations."""
        return (
            len(self.write_uris) == 0
            and len(self.edit_uris) == 0
            and len(self.edit_overview_uris) == 0
            and len(self.delete_uris) == 0
        )

    model_config = {'extra': 'ignore'}


# Backward compatibility alias
MemoryOperations = StructuredMemoryOperations
