# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: Apache-2.0
"""
Sum merge operation - numeric addition.
"""

from typing import Any, Type

from openviking.session.memory.merge_op.base import (
    MergeOp,
    MergeOpBase,
    FieldType,
    get_python_type_for_field,
)


class SumOp(MergeOpBase):
    """Sum merge operation - numeric addition."""

    op_type = MergeOp.SUM

    def get_output_schema_type(self, field_type: FieldType) -> Type[Any]:
        return get_python_type_for_field(field_type, default=int)

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
