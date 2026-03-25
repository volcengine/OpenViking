# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: Apache-2.0
"""
Merge operation base classes and registry.
"""

from abc import ABC, abstractmethod
from enum import Enum
from typing import Any, Dict, List, Optional, Type

from pydantic import BaseModel, Field


class FieldType(str, Enum):
    """Field type enumeration."""

    STRING = "string"
    INT64 = "int64"
    FLOAT32 = "float32"
    BOOL = "bool"


# ============================================================================
# Field Type Mapping (shared across all merge operations)
# ============================================================================

_FIELD_TYPE_TO_PYTHON: Dict[FieldType, Type[Any]] = {
    FieldType.STRING: str,
    FieldType.INT64: int,
    FieldType.FLOAT32: float,
    FieldType.BOOL: bool,
}


def get_python_type_for_field(field_type: FieldType, default: Type[Any] = str) -> Type[Any]:
    """Map FieldType to corresponding Python type.

    Args:
        field_type: The FieldType enum value
        default: Default type if field_type is not recognized

    Returns:
        Corresponding Python type (str, int, float, or bool)
    """
    return _FIELD_TYPE_TO_PYTHON.get(field_type, default)


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


class MergeOp(str, Enum):
    """Merge operation enumeration."""

    PATCH = "patch"
    SUM = "sum"
    IMMUTABLE = "immutable"


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
