# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0
"""
Tests for MemoryUpdater.
"""

from unittest.mock import AsyncMock, MagicMock

import pytest

from openviking.session.memory.memory_type_registry import MemoryTypeRegistry
from openviking.session.memory.memory_updater import (
    MemoryUpdater,
    MemoryUpdateResult,
)
from openviking.session.memory.merge_op import (
    SearchReplaceBlock,
    StrPatch,
)
from openviking.session.memory.utils import deserialize_full, serialize_with_metadata


class TestMemoryUpdateResult:
    """Tests for MemoryUpdateResult."""

    def test_create_empty(self):
        """Test creating an empty result."""
        result = MemoryUpdateResult()

        assert len(result.written_uris) == 0
        assert len(result.edited_uris) == 0
        assert len(result.deleted_uris) == 0
        assert len(result.errors) == 0
        assert result.has_changes() is False

    def test_add_written(self):
        """Test adding written URI."""
        result = MemoryUpdateResult()
        result.add_written("viking://user/test/memories/profile.md")

        assert len(result.written_uris) == 1
        assert result.has_changes() is True

    def test_add_edited(self):
        """Test adding edited URI."""
        result = MemoryUpdateResult()
        result.add_edited("viking://user/test/memories/profile.md")

        assert len(result.edited_uris) == 1
        assert result.has_changes() is True

    def test_add_deleted(self):
        """Test adding deleted URI."""
        result = MemoryUpdateResult()
        result.add_deleted("viking://user/test/memories/to_delete.md")

        assert len(result.deleted_uris) == 1
        assert result.has_changes() is True

    def test_summary(self):
        """Test summary generation."""
        result = MemoryUpdateResult()
        result.add_written("uri1")
        result.add_edited("uri2")
        result.add_deleted("uri3")

        summary = result.summary()
        assert "Written: 1" in summary
        assert "Edited: 1" in summary
        assert "Deleted: 1" in summary
        assert "Errors: 0" in summary


class TestMemoryUpdater:
    """Tests for MemoryUpdater."""

    def test_create(self):
        """Test creating a MemoryUpdater."""
        updater = MemoryUpdater()

        assert updater is not None
        assert updater._viking_fs is None
        assert updater._registry is None

    def test_create_with_registry(self):
        """Test creating a MemoryUpdater with registry."""
        registry = MemoryTypeRegistry()
        updater = MemoryUpdater(registry)

        assert updater._registry == registry

    def test_set_registry(self):
        """Test setting registry after creation."""
        updater = MemoryUpdater()
        registry = MemoryTypeRegistry()

        updater.set_registry(registry)

        assert updater._registry == registry


# The TestApplyWriteWithContentInFields tests are outdated because WriteOp no longer exists
# The _apply_write method now accepts any flat model (dict or Pydantic model) that
# can be converted with flat_model_to_dict(). Since the main issue we're fixing is
# the StrPatch handling in _apply_edit, we'll keep the focus on that.


class TestApplyEditWithSearchReplacePatch:
    """Tests for _apply_edit with SEARCH/REPLACE patches."""

    @pytest.mark.asyncio
    async def test_apply_edit_with_str_patch_instance(self):
        """Test _apply_edit with StrPatch instance."""
        updater = MemoryUpdater()

        # Original content
        original_content = """Line 1
Line 2
Line 3
Line 4"""
        original_metadata = {"name": "test"}
        original_metadata_with_content = {**original_metadata, "content": original_content}
        original_full_content = serialize_with_metadata(original_metadata_with_content)

        # Mock VikingFS
        mock_viking_fs = MagicMock()
        mock_viking_fs.read_file = AsyncMock(return_value=original_full_content)
        written_content = None

        async def mock_write_file(uri, content, **kwargs):
            nonlocal written_content
            written_content = content

        mock_viking_fs.write_file = mock_write_file
        updater._get_viking_fs = MagicMock(return_value=mock_viking_fs)

        # Create StrPatch
        patch = StrPatch(
            blocks=[
                SearchReplaceBlock(
                    search="Line 2\nLine 3",
                    replace="Line 2 modified\nLine 3 modified",
                    start_line=2,
                )
            ]
        )

        # Mock request context
        mock_ctx = MagicMock()

        # Apply edit
        await updater._apply_edit({"content": patch}, "viking://test/test.md", mock_ctx)

        # Verify
        assert written_content is not None
        body_content, metadata = deserialize_full(written_content)
        assert "Line 1" in body_content
        assert "Line 2 modified" in body_content
        assert "Line 3 modified" in body_content
        assert "Line 4" in body_content

    @pytest.mark.asyncio
    async def test_apply_edit_with_str_patch_dict(self):
        """Test _apply_edit with StrPatch in dict form (from JSON parsing)."""
        updater = MemoryUpdater()

        # Original content
        original_content = """Hello world
This is a test
Goodbye"""
        original_metadata = {"name": "test"}
        original_metadata_with_content = {**original_metadata, "content": original_content}
        original_full_content = serialize_with_metadata(original_metadata_with_content)

        # Mock VikingFS
        mock_viking_fs = MagicMock()
        mock_viking_fs.read_file = AsyncMock(return_value=original_full_content)
        written_content = None

        async def mock_write_file(uri, content, **kwargs):
            nonlocal written_content
            written_content = content

        mock_viking_fs.write_file = mock_write_file
        updater._get_viking_fs = MagicMock(return_value=mock_viking_fs)

        # StrPatch as dict (this is what JSON parsing gives us)
        patch_dict = {
            "blocks": [
                {"search": "This is a test", "replace": "This has been modified", "start_line": 2}
            ]
        }

        # Mock request context
        mock_ctx = MagicMock()

        # Apply edit
        await updater._apply_edit({"content": patch_dict}, "viking://test/test.md", mock_ctx)

        # Verify
        assert written_content is not None
        body_content, metadata = deserialize_full(written_content)
        assert "Hello world" in body_content
        assert "This has been modified" in body_content
        assert "Goodbye" in body_content

    @pytest.mark.asyncio
    async def test_apply_edit_with_simple_string_replacement(self):
        """Test _apply_edit with simple string full replacement."""
        updater = MemoryUpdater()

        # Original content
        original_content = "Old content"
        original_metadata = {"name": "test"}
        original_metadata_with_content = {**original_metadata, "content": original_content}
        original_full_content = serialize_with_metadata(original_metadata_with_content)

        # Mock VikingFS
        mock_viking_fs = MagicMock()
        mock_viking_fs.read_file = AsyncMock(return_value=original_full_content)
        written_content = None

        async def mock_write_file(uri, content, **kwargs):
            nonlocal written_content
            written_content = content

        mock_viking_fs.write_file = mock_write_file
        updater._get_viking_fs = MagicMock(return_value=mock_viking_fs)

        # Simple string replacement
        new_content = "Completely new content"

        # Mock request context
        mock_ctx = MagicMock()

        # Apply edit
        await updater._apply_edit({"content": new_content}, "viking://test/test.md", mock_ctx)

        # Verify
        assert written_content is not None
        body_content, metadata = deserialize_full(written_content)
        assert body_content == new_content
