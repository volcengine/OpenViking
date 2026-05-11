# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0
"""
Replace merge operation - full replacement, no SEARCH/REPLACE blocks.

Use this instead of patch when the field should always be fully rewritten
(e.g., structured documents where holistic synthesis is preferable to
surgical patching). The LLM receives a plain `str` output type, so it
cannot accidentally output StrPatch blocks.
"""

from typing import Any, Type

from openviking.session.memory.merge_op.base import (
    FieldType,
    MergeOp,
    MergeOpBase,
    get_python_type_for_field,
)


class ReplaceOp(MergeOpBase):
    """Full-replacement merge operation for string fields."""

    op_type = MergeOp.REPLACE

    def get_output_schema_type(self, field_type: FieldType) -> Type[Any]:
        return get_python_type_for_field(field_type)

    def get_output_schema_description(self, field_description: str) -> str:
        return (
            f"Full replacement for '{field_description}'. "
            "Output the complete new content as a plain string. "
            "You must have read the current content first and incorporate it."
        )

    def apply(self, current_value: Any, patch_value: Any) -> Any:
        if patch_value is None or patch_value == "":
            return current_value
        return patch_value
