# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: Apache-2.0
"""
Core data structures for memory templating system.
"""

from abc import ABC, abstractmethod
from datetime import datetime
from enum import Enum
from typing import Any, Dict, List, Optional, Type, Union

from pydantic import BaseModel, Field


class FieldType(str, Enum):
    """Field type enumeration."""

    STRING = "string"
    INT64 = "int64"
    FLOAT32 = "float32"
    BOOL = "bool"


class MergeOp(str, Enum):
    """Merge operation enumeration."""

    PATCH = "patch"
    SUM = "sum"
    AVG = "avg"
    IMMUTABLE = "immutable"


# ============================================================================
# Structured Patch Models
# ============================================================================


class SearchReplaceBlock(BaseModel):
    """Single SEARCH/REPLACE block for string patches."""

    search: str = Field(..., description="Content to search for")
    replace: str = Field(..., description="Content to replace with")
    start_line: Optional[int] = Field(None, description="Starting line number hint")


class StrPatch(BaseModel):
    """String patch containing multiple SEARCH/REPLACE blocks.

    All string fields with merge_op=patch use this structure.
    """

    blocks: List[SearchReplaceBlock] = Field(
        default_factory=list,
        description="List of SEARCH/REPLACE blocks to apply"
    )


# ============================================================================
# MergeOp Base and Implementations
# ============================================================================


class MergeOpBase(ABC):
    """Abstract base class for merge operations."""

    op_type: MergeOp

    @abstractmethod
    def get_output_schema_type(self, field_type: FieldType) -> Type[Any]:
        """Get the Python type for this merge operation's output schema.

        Args:
            field_type: The underlying field type

        Returns:
            Python type to use in the Pydantic schema
        """
        pass

    @abstractmethod
    def get_output_schema_description(self, field_description: str) -> str:
        """Get the description for this merge operation's output schema.

        Args:
            field_description: The original field description

        Returns:
            Description string to use in the Pydantic schema
        """
        pass

    @abstractmethod
    def apply(self, current_value: Any, patch_value: Any) -> Any:
        """Apply this merge operation.

        Args:
            current_value: Current field value
            patch_value: Patch value from the operation

        Returns:
            New field value after applying the merge
        """
        pass


class PatchOp(MergeOpBase):
    """Patch merge operation - SEARCH/REPLACE for strings, direct replace for others."""

    op_type = MergeOp.PATCH

    def __init__(self, field_type: FieldType):
        self._field_type = field_type

    def get_output_schema_type(self, field_type: FieldType) -> Type[Any]:
        if field_type == FieldType.STRING:
            return StrPatch
        return self._get_base_type(field_type)

    def get_output_schema_description(self, field_description: str) -> str:
        if self._field_type == FieldType.STRING:
            return f"PATCH operation for '{field_description}'. Use SEARCH/REPLACE blocks to modify content."
        return f"Replace value for '{field_description}'"

    def apply(self, current_value: Any, patch_value: Any) -> Any:
        # For string fields, patch_value should be StrPatch or already patched string
        # For non-string fields, just replace
        return patch_value

    def _get_base_type(self, field_type: FieldType) -> Type[Any]:
        type_mapping = {
            FieldType.STRING: str,
            FieldType.INT64: int,
            FieldType.FLOAT32: float,
            FieldType.BOOL: bool,
        }
        return type_mapping.get(field_type, str)


class SumOp(MergeOpBase):
    """Sum merge operation - numeric addition."""

    op_type = MergeOp.SUM

    def get_output_schema_type(self, field_type: FieldType) -> Type[Any]:
        type_mapping = {
            FieldType.STRING: str,
            FieldType.INT64: int,
            FieldType.FLOAT32: float,
            FieldType.BOOL: bool,
        }
        return type_mapping.get(field_type, int)

    def get_output_schema_description(self, field_description: str) -> str:
        return f"add for '{field_description}'"

    def apply(self, current_value: Any, patch_value: Any) -> Any:
        if current_value is None:
            return patch_value
        try:
            if isinstance(current_value, float) or isinstance(patch_value, float):
                return float(current_value) + float(patch_value)
            return int(current_value) + int(patch_value)
        except (ValueError, TypeError):
            return patch_value


class AvgOp(MergeOpBase):
    """Average merge operation - numeric averaging."""

    op_type = MergeOp.AVG

    def get_output_schema_type(self, field_type: FieldType) -> Type[Any]:
        type_mapping = {
            FieldType.STRING: str,
            FieldType.INT64: int,
            FieldType.FLOAT32: float,
            FieldType.BOOL: bool,
        }
        return type_mapping.get(field_type, float)

    def get_output_schema_description(self, field_description: str) -> str:
        return f"average value update for '{field_description}'"

    def apply(self, current_value: Any, patch_value: Any) -> Any:
        if current_value is None:
            return patch_value
        try:
            return (float(current_value) + float(patch_value)) / 2
        except (ValueError, TypeError):
            return patch_value


class ImmutableOp(MergeOpBase):
    """Immutable merge operation - field cannot be changed once set."""

    op_type = MergeOp.IMMUTABLE

    def get_output_schema_type(self, field_type: FieldType) -> Type[Any]:
        type_mapping = {
            FieldType.STRING: str,
            FieldType.INT64: int,
            FieldType.FLOAT32: float,
            FieldType.BOOL: bool,
        }
        return type_mapping.get(field_type, str)

    def get_output_schema_description(self, field_description: str) -> str:
        return f"Immutable field '{field_description}' - can only be set once, cannot be modified"

    def apply(self, current_value: Any, patch_value: Any) -> Any:
        if current_value is None:
            return patch_value
        # Keep current value if already set
        return current_value


class MergeOpFactory:
    """Factory for creating MergeOp instances."""

    @staticmethod
    def create(merge_op: MergeOp, field_type: FieldType) -> MergeOpBase:
        """Create a MergeOp instance from a MergeOp enum.

        Args:
            merge_op: The merge operation type
            field_type: The underlying field type

        Returns:
            MergeOpBase implementation
        """
        if merge_op == MergeOp.PATCH:
            return PatchOp(field_type)
        elif merge_op == MergeOp.SUM:
            return SumOp()
        elif merge_op == MergeOp.AVG:
            return AvgOp()
        elif merge_op == MergeOp.IMMUTABLE:
            return ImmutableOp()
        else:
            # Default to PatchOp
            return PatchOp(field_type)

    @staticmethod
    def from_field(field: 'MemoryField') -> MergeOpBase:
        """Create a MergeOp instance from a MemoryField.

        Args:
            field: The memory field definition

        Returns:
            MergeOpBase implementation
        """
        return MergeOpFactory.create(field.merge_op, field.field_type)


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


# Backward compatibility alias
class MemoryType(MemoryTypeSchema):
    """
    Deprecated: Use MemoryTypeSchema instead.
    Backward compatibility alias for MemoryTypeSchema.
    """

    def __init__(self, **data):
        # Support both 'name' and 'memory_type' for backward compatibility
        if "name" in data and "memory_type" not in data:
            data["memory_type"] = data.pop("name")
        super().__init__(**data)

    @property
    def name(self):
        """Backward compatibility: alias for memory_type."""
        return self.memory_type

    @name.setter
    def name(self, value):
        """Backward compatibility: alias for memory_type."""
        self.memory_type = value


class MemoryData(BaseModel):
    """Dynamic memory data."""

    memory_type: str = Field(..., description="Memory type name")
    uri: Optional[str] = Field(None, description="Memory URI (for updates)")
    fields: Dict[str, Any] = Field(default_factory=dict, description="Dynamic field data")
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
