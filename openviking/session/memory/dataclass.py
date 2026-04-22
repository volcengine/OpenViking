# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0
"""
Core domain data classes for memory system.
"""

import json
from datetime import datetime
from typing import (
    Any,
    Dict,
    List,
    Optional,
    Protocol,
    TypeVar,
    Union,
    get_args,
    get_origin,
    get_type_hints,
)

from pydantic import BaseModel, Field, model_validator

from openviking.session.memory.merge_op.base import (
    FieldType,
    MergeOp,
)

T = TypeVar("T")


# ============================================================================
# Memory Field and Schema Definitions
# ============================================================================


class MemoryField(BaseModel):
    """Memory field definition."""

    name: str = Field(..., description="Field name")
    field_type: FieldType = Field(..., description="Field type")
    description: str = Field("", description="Field description")
    merge_op: MergeOp = Field(MergeOp.PATCH, description="Merge strategy")
    init_value: Optional[str] = Field(None, description="Initial value for this field")


class MemoryTypeSchema(BaseModel):
    """Memory type schema definition."""

    memory_type: str = Field(..., description="Memory type name")
    description: str = Field("", description="Type description")
    fields: List[MemoryField] = Field(default_factory=list, description="Field definitions")
    filename_template: str = Field("", description="Filename template")
    content_template: Optional[str] = Field(
        None, description="Content template (for template mode)"
    )
    directory: str = Field("", description="Directory path")
    enabled: bool = Field(True, description="Whether this memory type is enabled")
    operation_mode: str = Field(
        "upsert", description="Operation mode: 'upsert' (default), 'add_only', or 'update_only'"
    )
    overview_template: Optional[str] = Field(
        None, description="Overview template for auto-generating .overview.md files"
    )


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
# Fault Tolerant Base Model (参考 vikingdb BaseModelCompat)
# ============================================================================


class FaultTolerantBaseModel(BaseModel):
    """
    支持验证前自动容错的 BaseModel，类似 vikingdb 的 BaseModelCompat。

    在 model_validator(mode='before') 中对所有字段做类型容错处理，
    使得模型可以接受 LLM 输出的不标准格式数据。
    """

    @model_validator(mode="before")
    @classmethod
    def values_fault_tolerance(cls, data: Dict[str, Any]) -> Dict[str, Any]:
        """在验证前对所有字段做容错处理"""
        if isinstance(data, dict):
            field_types = get_type_hints(cls)
            for field_name, value in data.items():
                if field_name in field_types:
                    data[field_name] = cls.value_fault_tolerance(field_types[field_name], value)
            return data
        return {}

    @classmethod
    def get_origin_type(cls, annotation) -> type:
        """从 Optional 或 Union 类型中提取基础类型"""
        origin = get_origin(annotation)
        if origin is Union:
            args = get_args(annotation)
            if len(args) == 2 and args[1] is type(None):
                return cls.get_origin_type(args[0])
        elif origin is list:
            return list
        return annotation

    @classmethod
    def get_arg_type(cls, annotation) -> type:
        """从 List annotation 中提取元素类型"""
        origin = get_origin(annotation)
        if origin is Union:
            args = get_args(annotation)
            if len(args) == 2 and args[1] is type(None):
                return cls.get_arg_type(args[0])
        elif origin is list:
            args = get_args(annotation)
            if args:
                return args[0]
        return None

    @classmethod
    def any_to_str(cls, value) -> str:
        """将任意值转换为字符串"""
        if value is None:
            return ""
        if isinstance(value, list):
            return ",".join(map(str, value))
        elif isinstance(value, dict):
            return json.dumps(value, ensure_ascii=False)
        elif isinstance(value, (int, bool, float)):
            return f"{value}"
        return str(value)

    @classmethod
    def value_fault_tolerance(cls, field_type, value):
        """
        字段级别的容错处理：
        - 'None' -> None (非 str 类型)
        - list/dict/number -> str (目标是 str)
        - str -> int/float (目标是数字)
        - str/dict -> list (目标是 list)
        - list 元素类型容错
        """
        origin_type = cls.get_origin_type(field_type)

        # json_repair 会把 None 转换成 'None'
        if value == "None" and origin_type is not str:
            return None

        if origin_type is str:
            return cls.any_to_str(value)
        elif origin_type is int:
            if isinstance(value, str):
                if value is None or value == "None":
                    return 0
                try:
                    return int(value)
                except (ValueError, TypeError):
                    pass
        elif origin_type is float:
            if isinstance(value, str):
                if value is None or value == "None":
                    return 0.0
                try:
                    return float(value)
                except (ValueError, TypeError):
                    pass
        elif origin_type is list:
            if isinstance(value, str):
                return [value]
            elif isinstance(value, dict):
                return [value]
            elif isinstance(value, list):
                arg_type = cls.get_arg_type(field_type)
                if arg_type is str:
                    return [cls.any_to_str(v) for v in value]
        return value


# ============================================================================
# Memory Operations
# ============================================================================


class MemoryOperationsProtocol(Protocol):
    """Protocol for memory operations (for type checking)."""

    reasoning: str
    write_uris: List[Any]
    edit_uris: List[Any]
    delete_uris: List[str]

    def is_empty(self) -> bool: ...


class StructuredMemoryOperations(FaultTolerantBaseModel):
    """
    Fallback memory operations model with fault tolerance.

    Use SchemaModelGenerator.create_structured_operations_model() to get
    the actual type-safe implementation with per-memory_type fields.
    """

    reasoning: str = Field(
        "",
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
    delete_uris: List[str] = Field(
        default_factory=list,
        description="Delete operations as URI strings",
    )

    def is_empty(self) -> bool:
        """Check if there are any operations."""
        return len(self.write_uris) == 0 and len(self.edit_uris) == 0 and len(self.delete_uris) == 0

    def to_legacy_operations(self) -> Dict[str, Any]:
        """Convert to legacy format (identity for fallback)."""
        return {
            "write_uris": self.write_uris,
            "edit_uris": self.edit_uris,
            "delete_uris": self.delete_uris,
        }

    model_config = {"extra": "ignore"}


# Backward compatibility alias
MemoryOperations = StructuredMemoryOperations
