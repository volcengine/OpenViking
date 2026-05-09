# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0
"""
Tests for MergeOp architecture - type-safe merge operations.
"""

from openviking.session.memory.dataclass import (
    MemoryField,
)
from openviking.session.memory.merge_op import (
    ImmutableOp,
    MergeOp,
    MergeOpFactory,
    PatchOp,
    SearchReplaceBlock,
    StrPatch,
    SumOp,
    apply_str_patch,
)
from openviking.session.memory.merge_op.base import FieldType

# ============================================================================
# Test MergeOp Base Classes
# ============================================================================


class TestPatchOp:
    """Tests for PatchOp."""

    def test_get_output_schema_type_string(self):
        """String field with patch should return StrPatch."""
        op = PatchOp(FieldType.STRING)
        assert op.get_output_schema_type(FieldType.STRING) == StrPatch

    def test_get_output_schema_type_int(self):
        """Int field with patch should return int."""
        op = PatchOp(FieldType.INT64)
        assert op.get_output_schema_type(FieldType.INT64) == int

    def test_get_output_schema_type_float(self):
        """Float field with patch should return float."""
        op = PatchOp(FieldType.FLOAT32)
        assert op.get_output_schema_type(FieldType.FLOAT32) == float

    def test_get_output_schema_type_bool(self):
        """Bool field with patch should return bool."""
        op = PatchOp(FieldType.BOOL)
        assert op.get_output_schema_type(FieldType.BOOL) == bool

    def test_get_output_schema_description_string(self):
        """String field description should mention PATCH."""
        op = PatchOp(FieldType.STRING)
        desc = op.get_output_schema_description("test content")
        assert "PATCH" in desc
        assert "test content" in desc

    def test_get_output_schema_description_other(self):
        """Non-string field description should mention replace."""
        op = PatchOp(FieldType.INT64)
        desc = op.get_output_schema_description("score")
        assert "Replace" in desc
        assert "score" in desc

    def test_apply(self):
        """PatchOp apply should just return the patch value."""
        op_str = PatchOp(FieldType.STRING)
        assert op_str.apply("old", "new") == "new"

        op_int = PatchOp(FieldType.INT64)
        assert op_int.apply(100, 200) == 200


class TestSumOp:
    """Tests for SumOp."""

    def test_get_output_schema_type(self):
        """SumOp should return appropriate numeric types."""
        op = SumOp()
        assert op.get_output_schema_type(FieldType.INT64) == int
        assert op.get_output_schema_type(FieldType.FLOAT32) == float

    def test_get_output_schema_description(self):
        """Description should have 'add for' format."""
        op = SumOp()
        desc = op.get_output_schema_description("打分合")
        assert desc == "add for '打分合'"

    def test_apply_both_int(self):
        """Sum of two ints."""
        op = SumOp()
        assert op.apply(10, 5) == 15

    def test_apply_both_float(self):
        """Sum of two floats."""
        op = SumOp()
        assert op.apply(10.5, 3.5) == 14.0

    def test_apply_mixed(self):
        """Sum of int and float."""
        op = SumOp()
        assert op.apply(10, 3.5) == 13.5

    def test_apply_current_none(self):
        """Current is None should return patch."""
        op = SumOp()
        assert op.apply(None, 10) == 10

    def test_apply_invalid_values(self):
        """Invalid values should fall back to patch."""
        op = SumOp()
        assert op.apply("not a number", 10) == 10


class TestImmutableOp:
    """Tests for ImmutableOp."""

    def test_get_output_schema_type(self):
        """ImmutableOp should return base types."""
        op = ImmutableOp()
        assert op.get_output_schema_type(FieldType.STRING) == str
        assert op.get_output_schema_type(FieldType.INT64) == int

    def test_get_output_schema_description(self):
        """Description should mention immutable."""
        op = ImmutableOp()
        desc = op.get_output_schema_description("name")
        assert "Immutable" in desc
        assert "name" in desc
        assert "can only be set once" in desc

    def test_apply_current_none(self):
        """Current is None should set to patch."""
        op = ImmutableOp()
        assert op.apply(None, "new value") == "new value"

    def test_apply_current_exists(self):
        """Current exists should keep current."""
        op = ImmutableOp()
        assert op.apply("existing", "new value") == "existing"


class TestMergeOpFactory:
    """Tests for MergeOpFactory."""

    def test_create_patch(self):
        """Factory should create PatchOp for PATCH."""
        op = MergeOpFactory.create(MergeOp.PATCH, FieldType.STRING)
        assert isinstance(op, PatchOp)

    def test_create_sum(self):
        """Factory should create SumOp for SUM."""
        op = MergeOpFactory.create(MergeOp.SUM, FieldType.INT64)
        assert isinstance(op, SumOp)

    def test_create_immutable(self):
        """Factory should create ImmutableOp for IMMUTABLE."""
        op = MergeOpFactory.create(MergeOp.IMMUTABLE, FieldType.STRING)
        assert isinstance(op, ImmutableOp)

    def test_from_field(self):
        """Factory should create from MemoryField."""
        field = MemoryField(
            name="test",
            field_type=FieldType.STRING,
            merge_op=MergeOp.SUM,
        )
        op = MergeOpFactory.from_field(field)
        assert isinstance(op, SumOp)


# ============================================================================
# Test Structured Patch Models
# ============================================================================


class TestSearchReplaceBlock:
    """Tests for SearchReplaceBlock."""

    def test_create_basic(self):
        """Create a basic SearchReplaceBlock."""
        block = SearchReplaceBlock(
            search="old content",
            replace="new content",
        )
        assert block.search == "old content"
        assert block.replace == "new content"


class TestStrPatch:
    """Tests for StrPatch."""

    def test_create_empty(self):
        """Create empty StrPatch."""
        patch = StrPatch()
        assert len(patch.blocks) == 0

    def test_create_with_blocks(self):
        """Create with blocks."""
        block1 = SearchReplaceBlock(search="a", replace="b")
        block2 = SearchReplaceBlock(search="c", replace="d")
        patch = StrPatch(blocks=[block1, block2])
        assert len(patch.blocks) == 2


# ============================================================================
# Test StrPatch Conversion
# ============================================================================


class TestApplyStrPatch:
    """Tests for apply_str_patch."""

    def test_empty_patch(self):
        """Empty patch returns original."""
        original = "line1\nline2\nline3"
        patch = StrPatch()
        result = apply_str_patch(original, patch)
        assert result == original

    def test_simple_replace(self):
        """Simple replace."""
        original = "hello world"
        patch = StrPatch(
            blocks=[SearchReplaceBlock(search="hello world", replace="hello there")]
        )
        result = apply_str_patch(original, patch)
        # Directly test apply_str_patch
        assert result == "hello there"


# ============================================================================
# Test Schema Generation Integration - tested in test_schema_models.py
# ============================================================================
