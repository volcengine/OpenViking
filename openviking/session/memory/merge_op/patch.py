# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0
"""
Patch merge operation - SEARCH/REPLACE for strings, direct replace for others.
"""

import hashlib
from typing import Any, Optional, Type

from openviking.session.memory.merge_op.base import (
    FieldType,
    MergeOp,
    MergeOpBase,
    SearchReplaceBlock,
    StrPatch,
    StrPatchWithBase,
    get_python_type_for_field,
)
from openviking.session.memory.merge_op.patch_handler import PatchParseError
from openviking.telemetry import get_current_telemetry
from openviking_cli.utils import get_logger

logger = get_logger(__name__)


def _text_digest(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def _is_patch_stale(current_value: str, patch_value: StrPatchWithBase) -> bool:
    """Return whether a runtime patch was generated against an older field value."""
    if patch_value.base_digest:
        return _text_digest(current_value) != patch_value.base_digest
    return patch_value.base_value is not None and current_value != patch_value.base_value


def _stale_patch_rewrite_config() -> tuple[bool, int]:
    try:
        from openviking_cli.utils.config import get_openviking_config

        memory_config = get_openviking_config().memory
        return (
            bool(getattr(memory_config, "stale_patch_rewrite_enabled", False)),
            int(getattr(memory_config, "stale_patch_rewrite_max_attempts", 0) or 0),
        )
    except Exception:
        return False, 0


async def _rewrite_stale_patch(
    *,
    current_value: str,
    patch_value: StrPatchWithBase,
    error: Exception,
) -> Optional[StrPatch]:
    """Ask the configured LLM to rewrite a stale string patch for latest content."""
    from openviking_cli.utils.llm import StructuredLLM

    original_blocks = [
        {"search": block.search, "replace": block.replace} for block in patch_value.blocks
    ]
    prompt = (
        "Rewrite a stale SEARCH/REPLACE patch so it applies to the latest memory field.\n"
        "Return ONLY the JSON schema requested by the caller.\n\n"
        "Rules:\n"
        "- Preserve the user's intended edit from the original patch.\n"
        "- Use exact SEARCH text copied from the latest field value.\n"
        "- Do not include line-number prefixes unless they are truly part of the field.\n"
        "- If no safe rewrite exists, return an empty blocks list.\n\n"
        f"Patch base value:\n{patch_value.base_value or ''}\n\n"
        f"Latest field value:\n{current_value}\n\n"
        f"Original patch blocks:\n{original_blocks}\n\n"
        f"Patch error:\n{error}"
    )
    rewritten = await StructuredLLM().complete_model_async(prompt, StrPatch)
    if rewritten and rewritten.blocks:
        return rewritten
    return None


class PatchOp(MergeOpBase):
    """Patch merge operation - SEARCH/REPLACE for strings, direct replace for others."""

    op_type = MergeOp.PATCH

    def __init__(self, field_type: FieldType):
        self._field_type = field_type

    def get_output_schema_type(self, field_type: FieldType) -> Type[Any]:
        if field_type == FieldType.STRING:
            return StrPatch
        return get_python_type_for_field(field_type)

    def get_output_schema_description(self, field_description: str) -> str:
        if self._field_type == FieldType.STRING:
            return f"PATCH operation for '{field_description}'. Follow the shared SEARCH/REPLACE rules above."
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
            # Filter out empty-search blocks when there's existing content.
            # Empty search with existing content is invalid (can't match empty string
            # against non-empty content), so skip those blocks.
            valid_blocks = [b for b in patch_value.blocks if b.search]
            if valid_blocks:
                return apply_str_patch(current_str, StrPatch(blocks=valid_blocks))
            # All blocks have empty search → no valid patches, keep original
            return current_value

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
                    # Filter out empty-search blocks when there's existing content
                    valid_blocks = [b for b in blocks if b.search]
                    if valid_blocks:
                        return apply_str_patch(current_str, StrPatch(blocks=valid_blocks))
                    # All blocks have empty search → keep original
                    return current_value
            except Exception:
                # If conversion fails, treat as simple replacement
                return str(patch_value) if patch_value is not None else ""

        # Case 3: Simple full replacement
        # 空字符串和 None 都保持原值
        if patch_value is None or patch_value == "":
            return current_value
        return patch_value

    async def apply_async(self, current_value: Any, patch_value: Any) -> Any:
        if self._field_type != FieldType.STRING:
            return self.apply(current_value, patch_value)

        try:
            return self.apply(current_value, patch_value)
        except PatchParseError as exc:
            enabled, max_attempts = _stale_patch_rewrite_config()
            telemetry = get_current_telemetry()
            if (
                not enabled
                or not isinstance(patch_value, StrPatchWithBase)
                or not isinstance(current_value, str)
                or patch_value.base_value is None
            ):
                raise
            if not _is_patch_stale(current_value, patch_value):
                raise
            telemetry.increment("memory.apply.patch_stale.detected")
            if patch_value.attempt_id >= max_attempts:
                telemetry.increment("memory.apply.patch_rewrite.exhausted")
                raise

            telemetry.increment("memory.apply.patch_rewrite.attempted")
            rewritten = await _rewrite_stale_patch(
                current_value=current_value,
                patch_value=patch_value,
                error=exc,
            )
            if rewritten is None:
                telemetry.increment("memory.apply.patch_rewrite.failed")
                raise

            retry_patch = rewritten.with_base(
                base_value=current_value,
                base_digest=_text_digest(current_value),
                source_operation_id=patch_value.source_operation_id,
                attempt_id=patch_value.attempt_id + 1,
            )
            logger.info(
                "Rewrote stale memory patch: source_operation_id=%s attempt=%s",
                patch_value.source_operation_id,
                retry_patch.attempt_id,
            )
            try:
                result = self.apply(current_value, retry_patch)
            except Exception:
                telemetry.increment("memory.apply.patch_rewrite.failed")
                raise
            telemetry.increment("memory.apply.patch_rewrite.succeeded")
            return result

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
