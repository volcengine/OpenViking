# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: Apache-2.0
"""
Immutable merge operation - field cannot be changed once set.
"""

from typing import Any, Type

from openviking.session.memory.merge_op.base import (
    MergeOp,
    MergeOpBase,
    FieldType,
    get_python_type_for_field,
)


class ImmutableOp(MergeOpBase):
    """Immutable merge operation - field cannot be changed once set."""

    op_type = MergeOp.IMMUTABLE

    def get_output_schema_type(self, field_type: FieldType) -> Type[Any]:
        return get_python_type_for_field(field_type)

    def get_output_schema_description(self, field_description: str) -> str:
        return f"Immutable field '{field_description}' - can only be set once, cannot be modified"

    def apply(self, current_value: Any, patch_value: Any) -> Any:
        if current_value is None:
            return patch_value
        # Keep current value if already set
        return current_value
