# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: Apache-2.0
"""
Elegant serialization decorator: automatically generate schema and serialization methods from type annotations
"""

from dataclasses import asdict, fields, is_dataclass
from typing import Any, get_args, get_origin

from openviking.storage.vectordb.store.bytes_row import BytesRow, FieldType, Schema


def _python_type_to_field_type(py_type: Any, field_name: str) -> FieldType:
    """Convert Python type annotation to BytesRow FieldType enum"""
    origin = get_origin(py_type)

    # Handle List type
    if origin is list:
        args = get_args(py_type)
        if not args:
            raise ValueError(f"Field '{field_name}': List must have type parameter")

        inner_type = args[0]
        if inner_type is int:
            return FieldType.list_int64
        elif inner_type is float:
            return FieldType.list_float32
        elif inner_type is str:
            return FieldType.list_string
        else:
            raise ValueError(f"Field '{field_name}': Unsupported list type {inner_type}")

    # Handle basic types
    if py_type is int:
        return FieldType.uint64  # Default to uint64, can be overridden via metadata
    elif py_type is float:
        return FieldType.float32
    elif py_type is str:
        return FieldType.string
    elif py_type is bool:
        return FieldType.boolean
    elif py_type is bytes:
        return FieldType.binary
    else:
        raise ValueError(f"Field '{field_name}': Unsupported type {py_type}")


def serializable(cls):
    """
    Decorator: automatically generate schema and serialization methods for dataclass

    Usage:
        @serializable
        @dataclass
        class MyData:
            label: int = 0
            vector: List[float] = field(default_factory=list)
            name: str = ""

    Optional field metadata:
        - field_type: Override the auto-inferred type with FieldType enum (e.g., FieldType.int64 vs FieldType.uint64)
        - default_value: Override the default value

    Example:
        @serializable
        @dataclass
        class MyData:
            # Default to uint64
            id: int = 0
            # Explicitly specify int64
            delta: int = field(default=0, metadata={"field_type": FieldType.int64})
    """
    if not is_dataclass(cls):
        raise TypeError(f"{cls.__name__} must be a dataclass (use @dataclass decorator first)")

    # Automatically generate schema
    field_list = []
    for idx, f in enumerate(fields(cls)):
        field_name = f.name

        # Get custom type from metadata, otherwise auto-infer
        if f.metadata and "field_type" in f.metadata:
            field_type = f.metadata["field_type"]
            # Check if it's the correct C++ enum type
            if hasattr(field_type, "value") and isinstance(field_type.value, int):
                # Try to find corresponding FieldType
                # Assuming enum names match
                if hasattr(FieldType, field_type.name):
                    field_type = getattr(FieldType, field_type.name)
        else:
            field_type = _python_type_to_field_type(f.type, field_name)

        field_def = {
            "name": field_name,
            "data_type": field_type,
            "id": idx,
        }

        # Optional default value override
        if f.metadata and "default_value" in f.metadata:
            field_def["default_value"] = f.metadata["default_value"]

        field_list.append(field_def)

    # Create schema and bytes_row
    # Pass field_list (list of dicts) to C++ Schema constructor
    cls.schema = Schema(field_list)
    cls.bytes_row = BytesRow(cls.schema)

    # Automatically generate serialization method
    def serialize(self) -> bytes:
        return self.__class__.bytes_row.serialize(asdict(self))

    # Automatically generate deserialization method
    def deserialize(self, data: bytes):
        data_dict = self.__class__.bytes_row.deserialize(data)
        for key, value in data_dict.items():
            # Handle potential None values if C++ returns std::monostate
            if value is not None:
                setattr(self, key, value)

    # Automatically generate from_bytes class method
    @classmethod
    def from_bytes(cls_method, data: bytes):
        if not data:
            return cls_method()
        inst = cls_method()
        inst.deserialize(data)
        return inst

    # Automatically generate serialize_list class method
    @classmethod
    def serialize_list(cls_method, objects: list) -> list[bytes]:
        """Batch serialization for a list of objects"""
        if not objects:
            return []
        return cls_method.bytes_row.serialize_batch(objects)

    # Inject methods into class
    cls.serialize = serialize
    cls.deserialize = deserialize
    cls.from_bytes = from_bytes
    cls.serialize_list = serialize_list

    return cls
