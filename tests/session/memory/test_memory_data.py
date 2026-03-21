# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: Apache-2.0
"""
Tests for memory data structures.
"""

from datetime import datetime

from openviking.session.memory.memory_data import (
    FieldType,
    MemoryData,
    MemoryField,
    MemoryType,
    MergeOp,
)


class TestMemoryField:
    """Tests for MemoryField."""

    def test_create_basic(self):
        """Test creating a basic memory field."""
        field = MemoryField(
            name="test_field",
            field_type=FieldType.STRING,
            description="Test description",
        )

        assert field.name == "test_field"
        assert field.field_type == FieldType.STRING
        assert field.description == "Test description"
        assert field.merge_op == MergeOp.PATCH

    def test_create_with_merge_op(self):
        """Test creating a field with merge_op."""
        field = MemoryField(
            name="id",
            field_type=FieldType.STRING,
            description="Primary key",
            merge_op=MergeOp.IMMUTABLE,
        )

        assert field.name == "id"
        assert field.merge_op == MergeOp.IMMUTABLE


class TestMemoryType:
    """Tests for MemoryType."""

    def test_create_basic(self):
        """Test creating a basic memory type."""
        fields = [
            MemoryField(name="name", field_type=FieldType.STRING),
            MemoryField(name="content", field_type=FieldType.STRING),
        ]

        memory_type = MemoryType(
            name="profile",
            description="User profile",
            fields=fields,
            filename_template="profile.md",
            directory="viking://user/{user_space}/memories",
        )

        assert memory_type.name == "profile"
        assert len(memory_type.fields) == 2


class TestMemoryData:
    """Tests for MemoryData."""

    def test_create_basic(self):
        """Test creating basic memory data."""
        memory = MemoryData(
            memory_type="profile",
            uri="viking://user/test/memories/profile.md",
            content="User profile content",
        )

        assert memory.memory_type == "profile"
        assert memory.uri == "viking://user/test/memories/profile.md"
        assert memory.content == "User profile content"

    def test_with_fields(self):
        """Test memory data with fields."""
        memory = MemoryData(
            memory_type="preferences",
            fields={"topic": "code_style", "preference": "no type hints"},
        )

        assert memory.get_field("topic") == "code_style"
        assert memory.get_field("preference") == "no type hints"

    def test_set_field(self):
        """Test setting a field."""
        memory = MemoryData(memory_type="test")
        memory.set_field("key", "value")

        assert memory.get_field("key") == "value"

    def test_with_timestamps(self):
        """Test memory data with timestamps."""
        now = datetime.utcnow()
        memory = MemoryData(
            memory_type="test",
            created_at=now,
            updated_at=now,
        )

        assert memory.created_at == now
        assert memory.updated_at == now
