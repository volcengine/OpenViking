# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: Apache-2.0
"""
Tests for MemoryPatchHandler.
"""

import pytest

from openviking.session.memory.memory_patch import MemoryPatchHandler, PatchParseError


class TestMemoryPatchHandler:
    """Tests for MemoryPatchHandler."""

    def setup_method(self):
        self.handler = MemoryPatchHandler()

    def test_apply_content_patch_basic(self):
        """Test basic SEARCH/REPLACE patch."""
        original = "Hello world\nThis is a test"
        patch = """<<<<<<< SEARCH
:start_line:1
-------
Hello world
=======
Hello everyone
>>>>>>> REPLACE
"""

        result = self.handler.apply_content_patch(original, patch)
        assert "Hello everyone" in result
        assert "This is a test" in result

    def test_apply_content_patch_without_line_number(self):
        """Test patch without line number."""
        original = "Line 1\nLine 2\nLine 3"
        patch = """<<<<<<< SEARCH
Line 2
=======
Line 2 modified
>>>>>>> REPLACE
"""

        result = self.handler.apply_content_patch(original, patch)
        assert "Line 2 modified" in result

    def test_apply_content_patch_not_found_fallback_append(self):
        """Test that search not found falls back to append."""
        original = "Original content"
        patch = """<<<<<<< SEARCH
Non-existent content
=======
New content
>>>>>>> REPLACE
"""

        result = self.handler.apply_content_patch(original, patch)
        assert result.startswith(original)
        assert "New content" in result

    def test_parse_patch_invalid_missing_search(self):
        """Test invalid patch missing SEARCH marker."""
        patch = """=======
content
>>>>>>> REPLACE
"""

        with pytest.raises(PatchParseError):
            self.handler.apply_content_patch("original", patch)

    def test_parse_patch_invalid_missing_split(self):
        """Test invalid patch missing split marker."""
        patch = """<<<<<<< SEARCH
content
>>>>>>> REPLACE
"""

        with pytest.raises(PatchParseError):
            self.handler.apply_content_patch("original", patch)

    def test_apply_field_patches_patch_default(self):
        """Test field patches with default patch strategy."""
        current = {"name": "Alice", "age": 30}
        patches = {"age": 31, "city": "Beijing"}

        result = self.handler.apply_field_patches(current, patches)
        assert result["age"] == 31
        assert result["city"] == "Beijing"
        assert result["name"] == "Alice"

    def test_apply_field_patches_sum(self):
        """Test field patches with sum strategy."""
        current = {"count": 10}
        patches = {"count": 5}
        merge_ops = {"count": "sum"}

        result = self.handler.apply_field_patches(current, patches, merge_ops)
        assert result["count"] == 15

    def test_apply_field_patches_avg(self):
        """Test field patches with avg strategy."""
        current = {"score": 80}
        patches = {"score": 90}
        merge_ops = {"score": "avg"}

        result = self.handler.apply_field_patches(current, patches, merge_ops)
        assert result["score"] == 85

    def test_apply_field_patches_immutable_existing(self):
        """Test immutable fields don't change if already present."""
        current = {"id": "123"}
        patches = {"id": "456"}
        merge_ops = {"id": "immutable"}

        result = self.handler.apply_field_patches(current, patches, merge_ops)
        assert result["id"] == "123"

    def test_apply_field_patches_immutable_new(self):
        """Test immutable fields are set if not present."""
        current = {}
        patches = {"id": "123"}
        merge_ops = {"id": "immutable"}

        result = self.handler.apply_field_patches(current, patches, merge_ops)
        assert result["id"] == "123"

    def test_create_content_patch(self):
        """Test creating a patch from original and updated content."""
        original = "Original line"
        updated = "Updated line"

        patch = self.handler.create_content_patch(original, updated, start_line=5)

        assert "<<<<<<< SEARCH" in patch
        assert ":start_line:5" in patch
        assert "Original line" in patch
        assert "=======" in patch
        assert "Updated line" in patch
        assert ">>>>>>> REPLACE" in patch

    def test_create_content_patch_identical(self):
        """Test creating patch for identical content returns empty."""
        content = "Same content"
        patch = self.handler.create_content_patch(content, content)
        assert patch == ""

    def test_apply_content_patch_multiple_blocks(self):
        """Test applying patch with multiple SEARCH/REPLACE blocks."""
        original = """Line 1
Line 2
Line 3
Line 4"""
        patch = """<<<<<<< SEARCH
:start_line:1
-------
Line 1
=======
Line 1 modified
>>>>>>> REPLACE
<<<<<<< SEARCH
:start_line:3
-------
Line 3
=======
Line 3 modified
>>>>>>> REPLACE
"""
        result = self.handler.apply_content_patch(original, patch)
        assert "Line 1 modified" in result
        assert "Line 3 modified" in result
        assert "Line 2" in result
        assert "Line 4" in result

    def test_apply_content_patch_with_indentation(self):
        """Test that indentation is preserved."""
        original = """def func():
    if True:
        print("hello")
    return"""
        patch = """<<<<<<< SEARCH
:start_line:2
-------
    if True:
        print("hello")
=======
    if True:
        print("hello world")
        print("another line")
>>>>>>> REPLACE
"""
        result = self.handler.apply_content_patch(original, patch)
        assert '    if True:' in result
        assert '        print("hello world")' in result
        assert '        print("another line")' in result

    def test_apply_content_patch_fuzzy_matching(self):
        """Test fuzzy matching with a lower threshold."""
        handler = MemoryPatchHandler(fuzzy_threshold=0.8)
        original = """def calculate():
    total = 0
    for i in range(10):
        total += i
    return total"""
        # Search content has a small difference (missing space)
        patch = """<<<<<<< SEARCH
:start_line:1
-------
def calculate():
    total = 0
    for i in range(10):
      total += i
    return total
=======
def calculate():
    sum = 0
    for i in range(10):
        sum += i
    return sum
>>>>>>> REPLACE
"""
        result = handler.apply_content_patch(original, patch)
        assert "sum = 0" in result
