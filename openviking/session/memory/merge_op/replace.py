# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0
"""
Replace merge operation - full replacement, no SEARCH/REPLACE blocks.

Use this instead of patch when the field should always be fully rewritten
(e.g., structured documents where holistic synthesis is preferable to
surgical patching). The LLM receives a plain `str` output type, so it
cannot accidentally output StrPatch blocks.
"""

import difflib
from typing import Any, Optional, Type

from pydantic import BaseModel

from openviking.session.memory.merge_op.base import (
    FieldType,
    MergeOp,
    MergeOpBase,
    ReplaceValueWithBase,
    get_python_type_for_field,
    text_digest,
)
from openviking.session.memory.merge_op.patch_handler import PatchParseError
from openviking.telemetry import get_current_telemetry
from openviking_cli.utils import get_logger

logger = get_logger(__name__)


def _is_replace_stale(current_value: Any, patch_value: ReplaceValueWithBase) -> bool:
    if patch_value.base_digest and isinstance(current_value, str):
        return text_digest(current_value) != patch_value.base_digest
    if isinstance(current_value, str) and isinstance(patch_value.base_value, str):
        return current_value != patch_value.base_value
    return current_value != patch_value.base_value


def _stale_replace_rewrite_config() -> tuple[bool, int]:
    try:
        from openviking_cli.utils.config import get_openviking_config

        memory_config = get_openviking_config().memory
        return (
            bool(getattr(memory_config, "stale_patch_rewrite_enabled", False)),
            int(getattr(memory_config, "stale_patch_rewrite_max_attempts", 0) or 0),
        )
    except Exception:
        return False, 0


def _format_unified_diff(base_value: str, proposed_value: str) -> str:
    if base_value == proposed_value:
        return ""
    return "".join(
        difflib.unified_diff(
            base_value.splitlines(keepends=True),
            proposed_value.splitlines(keepends=True),
            fromfile="base",
            tofile="proposed",
            lineterm="",
        )
    )


async def _rewrite_stale_replace(
    *,
    current_value: str,
    patch_value: ReplaceValueWithBase,
    intent_diff: str,
    reason: str,
) -> Optional[str]:
    from openviking_cli.utils.llm import StructuredLLM

    prompt = (
        "Reconcile a stale full-value memory replacement against the latest field value.\n"
        "Return ONLY a JSON object with one string field named `final_value`.\n"
        "Do not return a JSON schema, and do not include keys such as "
        "`properties`, `required`, `title`, or `type`.\n\n"
        "Rules:\n"
        "- Preserve compatible intent from the proposed replacement.\n"
        "- Preserve latest current content unless the proposed replacement clearly updates it.\n"
        "- Do not invent unrelated facts.\n"
        "- If the proposed replacement is no longer applicable, return the latest field value.\n\n"
        'Expected shape example:\n{"final_value": "the reconciled full field content"}\n\n'
        f"Replacement base value:\n{patch_value.base_value or ''}\n\n"
        f"Proposed full replacement:\n{patch_value.proposed_value or ''}\n\n"
        f"Latest field value:\n{current_value}\n\n"
        f"Intent diff from base to proposed replacement:\n{intent_diff}\n\n"
        f"Synthesis reason:\n{reason}"
    )

    class _ReplaceRewriteResult(BaseModel):
        final_value: str

    rewritten = await StructuredLLM().complete_model_async(prompt, _ReplaceRewriteResult)
    final_value = getattr(rewritten, "final_value", None)
    if isinstance(final_value, str) and final_value.strip():
        return final_value
    return None


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
        if isinstance(patch_value, ReplaceValueWithBase):
            raise PatchParseError("ReplaceValueWithBase requires apply_async")
        if patch_value is None or patch_value == "":
            return current_value
        return patch_value

    async def apply_async(self, current_value: Any, patch_value: Any) -> Any:
        if not isinstance(patch_value, ReplaceValueWithBase):
            return self.apply(current_value, patch_value)

        proposed_value = patch_value.proposed_value
        if proposed_value is None or proposed_value == "":
            return current_value
        if current_value is None:
            return proposed_value
        if patch_value.base_value is None:
            get_current_telemetry().increment("memory.apply.replace_missing_base.rejected")
            raise PatchParseError("existing replacement requires read-time base")
        if not _is_replace_stale(current_value, patch_value):
            return proposed_value
        telemetry = get_current_telemetry()
        telemetry.increment("memory.apply.replace_stale.detected")

        if not (
            isinstance(current_value, str)
            and isinstance(patch_value.base_value, str)
            and isinstance(proposed_value, str)
        ):
            telemetry.increment("memory.apply.replace_rewrite.unsupported")
            raise PatchParseError("stale non-string replacement requires tree lock")

        enabled, max_attempts = _stale_replace_rewrite_config()
        if not enabled or patch_value.attempt_id >= max_attempts:
            telemetry.increment("memory.apply.replace_rewrite.exhausted")
            raise PatchParseError("stale replacement requires LLM synthesis")

        telemetry.increment("memory.apply.replace_rewrite.attempted")
        rewritten = await _rewrite_stale_replace(
            current_value=current_value,
            patch_value=patch_value,
            intent_diff=_format_unified_diff(patch_value.base_value, proposed_value),
            reason="base_digest_mismatch",
        )
        if rewritten is None:
            telemetry.increment("memory.apply.replace_rewrite.failed")
            raise PatchParseError("stale replacement synthesis failed")

        logger.info(
            "Rewrote stale memory replacement: source_operation_id=%s attempt=%s",
            patch_value.source_operation_id,
            patch_value.attempt_id + 1,
        )
        telemetry.increment("memory.apply.replace_rewrite.succeeded")
        return rewritten
