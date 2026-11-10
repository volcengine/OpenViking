"""Regression tests: all blocks must be preserved when creating a new memory.

When ``current_value is None`` (the memory file does not exist yet),
``PatchOp._extract_replace_when_no_original`` previously took only the
first block's replace content — silently discarding every subsequent block.

The schema instructs the LLM to "split non-adjacent edits into separate
blocks."  A new memory therefore routinely arrives as a multi-block patch
(e.g. one block per extracted fact or preference).  Dropping all but the
first block means every fact after the first is silently lost with no log,
warning, or error visible to the caller.
"""

from __future__ import annotations

import pytest

from openviking.session.memory.merge_op.base import FieldType, SearchReplaceBlock, StrPatch
from openviking.session.memory.merge_op.patch import PatchOp


@pytest.mark.asyncio
class TestNewMemoryMultiBlockPatch:
    """PatchOp: all blocks must survive when writing a new memory."""

    async def test_strpatch_multiblock_no_silent_loss(self):
        """All StrPatch blocks are joined when current_value is None."""
        op = PatchOp(FieldType.STRING)
        patch = StrPatch(
            blocks=[
                SearchReplaceBlock(search="", replace="Fact A: user prefers dark mode."),
                SearchReplaceBlock(search="", replace="Fact B: user is in Tokyo."),
            ]
        )
        result = await op.apply(None, patch)
        assert "Fact A" in result, "block 0 content should be present"
        assert "Fact B" in result, "block 1 content was silently dropped before the fix"

    async def test_dict_form_multiblock_no_silent_loss(self):
        """dict-form blocks (the actual JSON-parsed LLM path) are all joined."""
        op = PatchOp(FieldType.STRING)
        patch_value = {
            "blocks": [
                {"search": "", "replace": "Line 1 preference"},
                {"search": "", "replace": "Line 2 fact"},
            ]
        }
        result = await op.apply(None, patch_value)
        assert "Line 1" in result
        assert "Line 2" in result, "second dict block was silently dropped before the fix"

    async def test_dict_form_accepts_model_blocks(self):
        """Model block objects in a dict-form patch are supported."""
        op = PatchOp(FieldType.STRING)
        patch_value = {"blocks": [SearchReplaceBlock(search="", replace="Model block")]}
        result = await op.apply(None, patch_value)
        assert result == "Model block"

    async def test_single_block_behaviour_unchanged(self):
        """The common single-block case is unaffected by this change."""
        op = PatchOp(FieldType.STRING)
        patch = StrPatch(blocks=[SearchReplaceBlock(search="", replace="Only fact.")])
        result = await op.apply(None, patch)
        assert result == "Only fact."

    async def test_existing_content_path_unaffected(self):
        """When current_value is NOT None the existing search/replace path runs."""
        op = PatchOp(FieldType.STRING)
        patch = StrPatch(blocks=[SearchReplaceBlock(search="old text", replace="new text")])
        result = await op.apply("some old text here", patch)
        assert "new text" in result
        assert "old text" not in result
