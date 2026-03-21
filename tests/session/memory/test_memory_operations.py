# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: Apache-2.0
"""
Tests for memory operations.
"""


from openviking.session.memory.memory_operations import (
    DeleteOp,
    EditOp,
    MemoryOperations,
    WriteOp,
)


class TestMemoryOperations:
    """Tests for memory operations."""

    def test_create_write_op(self):
        """Test creating a write operation."""
        op = WriteOp(
            memory_type="profile",
            content="Test content",
            fields={},
        )

        assert op.memory_type == "profile"
        assert op.content == "Test content"

    def test_create_edit_op(self):
        """Test creating an edit operation."""
        op = EditOp(
            memory_type="profile",
            fields={"name": "test"},
            patches={"content": "Updated content"},
        )

        assert op.memory_type == "profile"
        assert op.fields == {"name": "test"}
        assert op.patches == {"content": "Updated content"}

    def test_create_delete_op(self):
        """Test creating a delete operation."""
        op = DeleteOp(
            memory_type="profile",
            fields={"name": "to_delete"},
        )

        assert op.memory_type == "profile"
        assert op.fields == {"name": "to_delete"}

    def test_memory_operations_empty(self):
        """Test empty MemoryOperations."""
        ops = MemoryOperations()
        assert ops.is_empty() is True

    def test_memory_operations_with_write(self):
        """Test MemoryOperations with write."""
        ops = MemoryOperations(
            write_uris=[
                WriteOp(
                    memory_type="test",
                    content="test",
                    fields={},
                )
            ],
        )
        assert ops.is_empty() is False
        assert len(ops.write_uris) == 1

    def test_memory_operations_with_edit(self):
        """Test MemoryOperations with edit."""
        ops = MemoryOperations(
            edit_uris=[
                EditOp(
                    memory_type="test",
                    fields={"id": "123"},
                    patches={},
                )
            ],
        )
        assert ops.is_empty() is False
        assert len(ops.edit_uris) == 1

    def test_memory_operations_with_delete(self):
        """Test MemoryOperations with delete."""
        ops = MemoryOperations(
            delete_uris=[DeleteOp(memory_type="test", fields={"id": "123"})],
        )
        assert ops.is_empty() is False
        assert len(ops.delete_uris) == 1

    def test_memory_operations_all_types(self):
        """Test MemoryOperations with all operation types."""
        ops = MemoryOperations(
            write_uris=[
                WriteOp(
                    memory_type="test",
                    content="test",
                    fields={},
                )
            ],
            edit_uris=[
                EditOp(memory_type="test", fields={"id": "123"}, patches={})
            ],
            delete_uris=[DeleteOp(memory_type="test", fields={"id": "123"})],
        )
        assert ops.is_empty() is False
        assert len(ops.write_uris) == 1
        assert len(ops.edit_uris) == 1
        assert len(ops.delete_uris) == 1
