# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0
"""
Patch merge operation - SEARCH/REPLACE for strings, direct replace for others.
"""

from typing import TYPE_CHECKING, Any, Type

from openviking.session.memory.merge_op.base import (
    FieldType,
    MergeOp,
    MergeOpBase,
    SearchReplaceBlock,
    StrPatch,
    get_python_type_for_field,
)

if TYPE_CHECKING:
    from openviking.session.memory.merge_op.patch_handler import MemoryPatchHandler


class PatchOp(MergeOpBase):
    """Patch merge operation - SEARCH/REPLACE for strings, direct replace for others."""

    op_type = MergeOp.PATCH

    def __init__(self, field_type: FieldType):
        self._field_type = field_type
        self._patch_handler: "MemoryPatchHandler | None" = None

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
        - other: full replacement

        For non-string fields:
        - Just replace with patch_value

        Special case: when current_value is None (no original content),
        use the replace value directly instead of trying to match.
        """
        # For non-string fields, just replace
        if self._field_type != FieldType.STRING:
            return patch_value

        # For string fields - check if current_value is None (no original)
        if current_value is None:
            # No original content - extract replace value from patch
            return self._extract_replace_when_no_original(patch_value)

        # For string fields with existing content
        from openviking.session.memory.merge_op.patch_handler import apply_str_patch

        current_str = current_value or ""

        # Case 1: StrPatch object - apply patch
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

        # Case 3: Simple full replacement
        # 空字符串和 None 都保持原值
        if patch_value is None or patch_value == "":
            return current_value
        return patch_value

    def _extract_replace_when_no_original(self, patch_value: Any) -> Any:
        """
        Extract replace value from patch when there's no original content.

        Called when current_value is None - we use the replace content
        directly instead of trying to match against an empty string.

        Args:
            patch_value: The patch value (StrPatch, dict, or string)

        Returns:
            The replace content, or empty string if not available
        """
        from openviking.session.memory.merge_op.base import StrPatch

        # Case 1: StrPatch object
        if isinstance(patch_value, StrPatch):
            replace = patch_value.get_first_replace()
            return replace if replace is not None else ""

        # Case 2: dict form
        if isinstance(patch_value, dict) and "blocks" in patch_value:
            blocks = patch_value.get("blocks", [])
            if blocks:
                first_block = blocks[0]
                if isinstance(first_block, dict):
                    replace = first_block.get("replace")
                    return replace if replace is not None else ""

        # Case 3: Simple string - use as is
        if isinstance(patch_value, str):
            return patch_value

        return ""
