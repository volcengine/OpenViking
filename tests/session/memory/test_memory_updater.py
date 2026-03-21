# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: Apache-2.0
"""
Tests for MemoryUpdater.
"""
from datetime import datetime
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from openviking.session.memory.memory_updater import (
    MemoryUpdateResult,
    MemoryUpdater,
)
from openviking.session.memory.memory_types import MemoryTypeRegistry
from openviking.session.memory.memory_data import MemoryTypeSchema, MemoryField, FieldType
from openviking.session.memory.memory_operations import WriteOp
from openviking.session.memory.memory_content import deserialize_full


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
        assert updater._patch_handler is not None
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


class TestApplyWriteWithContentInFields:
    """Tests for _apply_write with content in fields dict."""

    @pytest.mark.asyncio
    async def test_apply_write_extracts_content_from_fields(self):
        """Test that content is extracted from op.fields.content when present."""
        updater = MemoryUpdater()

        # Mock VikingFS
        mock_viking_fs = MagicMock()
        written_content = None

        async def mock_write_file(uri, content, **kwargs):
            nonlocal written_content
            written_content = content

        mock_viking_fs.write_file = mock_write_file
        updater._get_viking_fs = MagicMock(return_value=mock_viking_fs)

        # Create WriteOp with content in fields (this is what LLM produces)
        test_content = "# Test Card\n\nThis is the main content that should be in the file body."
        op = WriteOp(
            memory_type="card",
            fields={
                "name": "test_card",
                "content": test_content,
                "tags": ["test", "important"]
            },
            name="Test Card",
            tags=["test"]
        )

        # Mock request context
        mock_ctx = MagicMock()

        # Apply write
        await updater._apply_write(op, "viking://test/test.md", mock_ctx)

        # Verify content was written and parsed correctly
        assert written_content is not None

        # Deserialize to check
        body_content, metadata = deserialize_full(written_content)

        # The main content should be in the body, not in metadata.fields
        assert body_content == test_content
        assert metadata is not None
        assert "fields" in metadata
        # content should NOT be in metadata.fields
        assert "content" not in metadata["fields"]
        # Other fields should still be there
        assert metadata["fields"]["name"] == "test_card"
        assert metadata["fields"]["tags"] == ["test", "important"]

    @pytest.mark.asyncio
    async def test_apply_write_prefers_fields_content_over_op_content(self):
        """Test that op.fields.content takes priority over op.content."""
        updater = MemoryUpdater()

        # Mock VikingFS
        mock_viking_fs = MagicMock()
        written_content = None

        async def mock_write_file(uri, content, **kwargs):
            nonlocal written_content
            written_content = content

        mock_viking_fs.write_file = mock_write_file
        updater._get_viking_fs = MagicMock(return_value=mock_viking_fs)

        # Create WriteOp with content in both places
        fields_content = "# Content from Fields\n\nThis should be used as it has higher priority."
        op_content = "# Content from Op\n\nThis should NOT be used."

        op = WriteOp(
            memory_type="card",
            fields={
                "name": "test_card",
                "content": fields_content
            },
            content=op_content,
            name="Test Card"
        )

        # Mock request context
        mock_ctx = MagicMock()

        # Apply write
        await updater._apply_write(op, "viking://test/test.md", mock_ctx)

        # Verify
        body_content, metadata = deserialize_full(written_content)
        assert body_content == fields_content
        assert "content" not in metadata["fields"]

    @pytest.mark.asyncio
    async def test_apply_write_falls_back_to_op_content(self):
        """Test that op.content is used when fields.content is not present."""
        updater = MemoryUpdater()

        # Mock VikingFS
        mock_viking_fs = MagicMock()
        written_content = None

        async def mock_write_file(uri, content, **kwargs):
            nonlocal written_content
            written_content = content

        mock_viking_fs.write_file = mock_write_file
        updater._get_viking_fs = MagicMock(return_value=mock_viking_fs)

        # Create WriteOp with content only in op.content
        op_content = "# Content from Op\n\nThis should be used when fields.content is missing."
        op = WriteOp(
            memory_type="card",
            fields={
                "name": "test_card"
            },
            content=op_content,
            name="Test Card"
        )

        # Mock request context
        mock_ctx = MagicMock()

        # Apply write
        await updater._apply_write(op, "viking://test/test.md", mock_ctx)

        # Verify
        body_content, metadata = deserialize_full(written_content)
        assert body_content == op_content

    @pytest.mark.asyncio
    async def test_apply_write_with_no_content(self):
        """Test that write works with no content at all."""
        updater = MemoryUpdater()

        # Mock VikingFS
        mock_viking_fs = MagicMock()
        written_content = None

        async def mock_write_file(uri, content, **kwargs):
            nonlocal written_content
            written_content = content

        mock_viking_fs.write_file = mock_write_file
        updater._get_viking_fs = MagicMock(return_value=mock_viking_fs)

        # Create WriteOp with no content
        op = WriteOp(
            memory_type="card",
            fields={
                "name": "test_card"
            },
            name="Test Card"
        )

        # Mock request context
        mock_ctx = MagicMock()

        # Apply write
        await updater._apply_write(op, "viking://test/test.md", mock_ctx)

        # Verify
        body_content, metadata = deserialize_full(written_content)
        assert body_content == ""
