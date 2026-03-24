# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: Apache-2.0
"""
Patch merge operation - SEARCH/REPLACE for strings, direct replace for others.
"""

from typing import Any, Type, TYPE_CHECKING

from openviking.session.memory.merge_op.base import (
    MergeOp,
    MergeOpBase,
    FieldType,
    StrPatch,
    SearchReplaceBlock,
    get_python_type_for_field,
)

if TYPE_CHECKING:
    from openviking.session.memory.merge_op.patch_handler import MemoryPatchHandler


class PatchOp(MergeOpBase):
    """Patch merge operation - SEARCH/REPLACE for strings, direct replace for others."""

    op_type = MergeOp.PATCH

    def __init__(self, field_type: FieldType):
        self._field_type = field_type
        self._patch_handler: 'MemoryPatchHandler | None' = None

    def get_output_schema_type(self, field_type: FieldType) -> Type[Any]:
        if field_type == FieldType.STRING:
            return StrPatch
        return get_python_type_for_field(field_type)

    def get_output_schema_description(self, field_description: str) -> str:
        if self._field_type == FieldType.STRING:
            return f"PATCH operation for '{field_description}'. Use SEARCH/REPLACE blocks to modify content."
        return f"Replace value for '{field_description}'"

    def apply(self, current_value: Any, patch_value: Any) -> Any:
        """
        Apply patch operation.

        For string fields (content):
        - StrPatch: use apply_str_patch()
        - str with "<<<<<<< SEARCH": use MemoryPatchHandler
        - other str: full replacement

        For non-string fields:
        - Just replace with patch_value
        """
        # For non-string fields, just replace
        if self._field_type != FieldType.STRING:
            return patch_value

        # For string fields, handle various patch formats
        from openviking.session.memory.merge_op.patch_handler import (
            MemoryPatchHandler,
            apply_str_patch,
        )

        current_str = current_value or ""

        # Case 1: StrPatch object
        if isinstance(patch_value, StrPatch):
            return apply_str_patch(current_str, patch_value)

        # Case 2: dict form of StrPatch (from JSON parsing)
        if isinstance(patch_value, dict):
            try:
                if "blocks" in patch_value:
                    blocks = []
                    for block_dict in patch_value["blocks"]:
                        if isinstance(block_dict, dict):
                            blocks.append(SearchReplaceBlock(**block_dict))
                        else:
                            blocks.append(block_dict)
                    patch_value = StrPatch(blocks=blocks)
                    return apply_str_patch(current_str, patch_value)
            except Exception:
                # If conversion fails, treat as simple replacement
                return str(patch_value) if patch_value is not None else ""

        # Case 3: string with SEARCH/REPLACE markers
        if isinstance(patch_value, str):
            if "<<<<<<< SEARCH" in patch_value:
                if self._patch_handler is None:
                    self._patch_handler = MemoryPatchHandler()
                return self._patch_handler.apply_content_patch(current_str, patch_value)
            else:
                # Simple full replacement
                return patch_value

        # Fallback: just return patch_value as-is
        return patch_value
