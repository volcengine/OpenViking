# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0
"""
Prefer complete replacement values while tolerating runtime string edits.

The model-facing schema remains a plain string. An edit produced despite that
instruction follows the normal patch-validation path and is materialized against
the current field value when applied, rather than rejected by the DSL compiler.
"""

from __future__ import annotations

from typing import Annotated, Any, Union

from pydantic import WithJsonSchema

from openviking.session.memory.merge_op.base import (
    FieldType,
    MergeOp,
    MergeOpBase,
    StrPatch,
    get_python_type_for_field,
)


class ReplaceOp(MergeOpBase):
    """Full-replacement merge operation for string fields."""

    op_type = MergeOp.REPLACE

    def __init__(self, field_type: FieldType = FieldType.STRING):
        self._field_type = field_type

    def get_output_schema_type(self, field_type: FieldType) -> Any:
        if field_type == FieldType.STRING:
            # Accept edits at runtime without advertising them to the model.
            return Annotated[Union[str, StrPatch], WithJsonSchema({"type": "string"})]
        return get_python_type_for_field(field_type)

    def get_output_schema_description(self, field_description: str) -> str:
        return (
            f"Full replacement for '{field_description}'. "
            "Output the complete new content as a plain string. "
            "You must have read the current content first and incorporate it."
        )

    async def apply(self, current_value: Any, patch_value: Any) -> Any:
        if self._field_type == FieldType.STRING and (
            isinstance(patch_value, StrPatch)
            or isinstance(patch_value, dict)
            and "blocks" in patch_value
        ):
            from openviking.session.memory.merge_op.patch import PatchOp

            patch = (
                patch_value
                if isinstance(patch_value, StrPatch)
                else StrPatch.model_validate(patch_value)
            )
            return await PatchOp(FieldType.STRING).apply(
                "" if current_value is None else current_value, patch
            )
        if patch_value is None or patch_value == "":
            return current_value
        return patch_value
