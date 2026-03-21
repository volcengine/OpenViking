# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: Apache-2.0
"""
Tests for memory utilities - URI generation, etc.
"""

import pytest

from openviking.session.memory.memory_data import (
    MemoryField,
    MemoryTypeSchema,
    FieldType,
    MergeOp,
)
from openviking.session.memory.memory_utils import (
    collect_allowed_directories,
    collect_allowed_path_patterns,
    generate_uri,
    is_uri_allowed,
    is_uri_allowed_for_schema,
    resolve_write_uri,
    resolve_edit_target,
    resolve_delete_target,
    resolve_all_operations,
    validate_uri_template,
)
from openviking.session.memory.memory_operations import (
    MemoryOperations,
    WriteOp,
    EditOp,
    DeleteOp,
)
from openviking.session.memory.memory_types import MemoryTypeRegistry


class TestUriGeneration:
    """Tests for URI generation."""

    def test_generate_uri_preferences(self):
        """Test generating URI for preferences memory type."""
        memory_type = MemoryTypeSchema(
            memory_type="preferences",
            description="User preference memory",
            directory="viking://user/{user_space}/memories/preferences",
            filename_template="{topic}.md",
            fields=[
                MemoryField(
                    name="topic",
                    field_type=FieldType.STRING,
                    description="Preference topic",
                    merge_op=MergeOp.IMMUTABLE,
                ),
                MemoryField(
                    name="content",
                    field_type=FieldType.STRING,
                    description="Preference content",
                    merge_op=MergeOp.PATCH,
                ),
            ],
        )

        uri = generate_uri(
            memory_type,
            {"topic": "Python code style", "content": "..."},
            user_space="default",
        )

        assert uri == "viking://user/default/memories/preferences/Python code style.md"

    def test_generate_uri_tools(self):
        """Test generating URI for tools memory type."""
        memory_type = MemoryTypeSchema(
            memory_type="tools",
            description="Tool usage memory",
            directory="viking://agent/{agent_space}/memories/tools",
            filename_template="{tool_name}.md",
            fields=[
                MemoryField(
                    name="tool_name",
                    field_type=FieldType.STRING,
                    description="Tool name",
                    merge_op=MergeOp.IMMUTABLE,
                ),
            ],
        )

        uri = generate_uri(
            memory_type,
            {"tool_name": "web_search"},
            agent_space="default",
        )

        assert uri == "viking://agent/default/memories/tools/web_search.md"

    def test_generate_uri_only_directory(self):
        """Test generating URI with only directory."""
        memory_type = MemoryTypeSchema(
            memory_type="test",
            description="Test memory",
            directory="viking://user/{user_space}/memories/test",
            filename_template="",
            fields=[],
        )

        uri = generate_uri(memory_type, {}, user_space="alice")

        assert uri == "viking://user/alice/memories/test"

    def test_generate_uri_only_filename(self):
        """Test generating URI with only filename template."""
        memory_type = MemoryTypeSchema(
            memory_type="test",
            description="Test memory",
            directory="",
            filename_template="{name}.md",
            fields=[
                MemoryField(
                    name="name",
                    field_type=FieldType.STRING,
                    description="Name",
                    merge_op=MergeOp.IMMUTABLE,
                ),
            ],
        )

        uri = generate_uri(memory_type, {"name": "test-file"})

        assert uri == "test-file.md"

    def test_generate_uri_missing_variable(self):
        """Test error when required variable is missing."""
        memory_type = MemoryTypeSchema(
            memory_type="preferences",
            description="User preference memory",
            directory="viking://user/{user_space}/memories/preferences",
            filename_template="{topic}.md",
            fields=[],
        )

        with pytest.raises(ValueError, match="Missing template variable"):
            generate_uri(memory_type, {})

    def test_generate_uri_none_value(self):
        """Test error when variable has None value."""
        memory_type = MemoryTypeSchema(
            memory_type="preferences",
            description="User preference memory",
            directory="viking://user/{user_space}/memories/preferences",
            filename_template="{topic}.md",
            fields=[],
        )

        with pytest.raises(ValueError, match="has None value"):
            generate_uri(memory_type, {"topic": None})

    def test_validate_uri_template_valid(self):
        """Test validating a valid URI template."""
        memory_type = MemoryTypeSchema(
            memory_type="preferences",
            description="User preference memory",
            directory="viking://user/{user_space}/memories/preferences",
            filename_template="{topic}.md",
            fields=[
                MemoryField(
                    name="topic",
                    field_type=FieldType.STRING,
                    description="Preference topic",
                    merge_op=MergeOp.IMMUTABLE,
                ),
            ],
        )

        assert validate_uri_template(memory_type) is True

    def test_validate_uri_template_missing_field(self):
        """Test validating a template with missing field."""
        memory_type = MemoryTypeSchema(
            memory_type="preferences",
            description="User preference memory",
            directory="viking://user/{user_space}/memories/preferences",
            filename_template="{missing_field}.md",
            fields=[
                MemoryField(
                    name="topic",
                    field_type=FieldType.STRING,
                    description="Preference topic",
                    merge_op=MergeOp.IMMUTABLE,
                ),
            ],
        )

        assert validate_uri_template(memory_type) is False

    def test_validate_uri_template_no_directory_or_filename(self):
        """Test validating with neither directory nor filename."""
        memory_type = MemoryTypeSchema(
            memory_type="test",
            description="Test memory",
            directory="",
            filename_template="",
            fields=[],
        )

        assert validate_uri_template(memory_type) is False


class TestUriValidation:
    """Tests for URI validation."""

    def test_collect_allowed_directories(self):
        """Test collecting allowed directories from schemas."""
        schemas = [
            MemoryTypeSchema(
                memory_type="preferences",
                description="Preferences",
                directory="viking://user/{user_space}/memories/preferences",
                filename_template="{topic}.md",
                fields=[],
            ),
            MemoryTypeSchema(
                memory_type="tools",
                description="Tools",
                directory="viking://agent/{agent_space}/memories/tools",
                filename_template="{tool_name}.md",
                fields=[],
            ),
            MemoryTypeSchema(
                memory_type="disabled",
                description="Disabled",
                directory="viking://user/default/memories/disabled",
                filename_template="",
                fields=[],
                enabled=False,
            ),
        ]

        dirs = collect_allowed_directories([s for s in schemas if s.enabled], user_space="default", agent_space="default")

        assert dirs == {
            "viking://user/default/memories/preferences",
            "viking://agent/default/memories/tools",
        }

    def test_collect_allowed_path_patterns(self):
        """Test collecting allowed path patterns from schemas."""
        schemas = [
            MemoryTypeSchema(
                memory_type="preferences",
                description="Preferences",
                directory="viking://user/{user_space}/memories/preferences",
                filename_template="{topic}.md",
                fields=[],
            ),
        ]

        patterns = collect_allowed_path_patterns(schemas, user_space="default", agent_space="default")

        assert patterns == {
            "viking://user/default/memories/preferences/{topic}.md",
        }

    def test_is_uri_allowed_by_directory(self):
        """Test URI allowed by matching directory prefix."""
        allowed_dirs = {
            "viking://user/default/memories/preferences",
            "viking://agent/default/memories/tools",
        }
        allowed_patterns = set()

        assert is_uri_allowed(
            "viking://user/default/memories/preferences/test.md",
            allowed_dirs,
            allowed_patterns,
        ) is True

        assert is_uri_allowed(
            "viking://user/default/memories/preferences",
            allowed_dirs,
            allowed_patterns,
        ) is True

        assert is_uri_allowed(
            "viking://user/default/memories/preferences/subdir/test.md",
            allowed_dirs,
            allowed_patterns,
        ) is True

    def test_is_uri_allowed_by_pattern(self):
        """Test URI allowed by matching pattern."""
        allowed_dirs = set()
        allowed_patterns = {
            "viking://user/default/memories/preferences/{topic}.md",
        }

        assert is_uri_allowed(
            "viking://user/default/memories/preferences/Python code style.md",
            allowed_dirs,
            allowed_patterns,
        ) is True

    def test_is_uri_disallowed(self):
        """Test URI not allowed."""
        allowed_dirs = {
            "viking://user/default/memories/preferences",
        }
        allowed_patterns = set()

        assert is_uri_allowed(
            "viking://user/default/memories/other/test.md",
            allowed_dirs,
            allowed_patterns,
        ) is False

        assert is_uri_allowed(
            "viking://user/other/memories/preferences/test.md",
            allowed_dirs,
            allowed_patterns,
        ) is False

    def test_is_uri_allowed_for_schema(self):
        """Test checking URI against schemas."""
        schemas = [
            MemoryTypeSchema(
                memory_type="preferences",
                description="Preferences",
                directory="viking://user/{user_space}/memories/preferences",
                filename_template="{topic}.md",
                fields=[],
            ),
        ]

        assert is_uri_allowed_for_schema(
            "viking://user/default/memories/preferences/test.md",
            schemas,
        ) is True

        assert is_uri_allowed_for_schema(
            "viking://user/default/memories/other/test.md",
            schemas,
        ) is False


class TestUriResolution:
    """Tests for URI resolution methods."""

    @pytest.fixture
    def test_registry(self):
        """Create a test registry with sample schemas."""
        registry = MemoryTypeRegistry()

        # Add preferences schema
        registry.register(MemoryTypeSchema(
            memory_type="preferences",
            description="User preferences",
            directory="viking://user/{user_space}/memories/preferences",
            filename_template="{topic}.md",
            fields=[
                MemoryField(name="topic", field_type=FieldType.STRING, description="Topic"),
            ],
        ))

        # Add tools schema
        registry.register(MemoryTypeSchema(
            memory_type="tools",
            description="Tool memories",
            directory="viking://agent/{agent_space}/memories/tools",
            filename_template="{tool_name}.md",
            fields=[
                MemoryField(name="tool_name", field_type=FieldType.STRING, description="Tool name"),
            ],
        ))

        return registry

    def test_resolve_write_uri(self, test_registry):
        """Test resolving URI for WriteOp."""
        write_op = WriteOp(
            memory_type="preferences",
            fields={"topic": "Python code style"},
            content="Test content",
        )

        uri = resolve_write_uri(write_op, test_registry)

        assert uri == "viking://user/default/memories/preferences/Python code style.md"

    def test_resolve_write_uri_unknown_type(self, test_registry):
        """Test resolving WriteOp with unknown memory type."""
        write_op = WriteOp(
            memory_type="unknown_type",
            fields={},
        )

        with pytest.raises(ValueError, match="Unknown memory type"):
            resolve_write_uri(write_op, test_registry)

    def test_resolve_edit_target(self, test_registry):
        """Test resolving target URI for EditOp."""
        uri = resolve_edit_target(
            "tools",
            {"tool_name": "web_search"},
            test_registry,
        )

        assert uri == "viking://agent/default/memories/tools/web_search.md"

    def test_resolve_delete_target(self, test_registry):
        """Test resolving target URI for DeleteOp."""
        uri = resolve_delete_target(
            "preferences",
            {"topic": "Test topic"},
            test_registry,
        )

        assert uri == "viking://user/default/memories/preferences/Test topic.md"

    def test_resolve_all_operations(self, test_registry):
        """Test resolving all operations at once."""
        operations = MemoryOperations(
            write_uris=[
                WriteOp(
                    memory_type="preferences",
                    fields={"topic": "Write test"},
                    content="Write content",
                ),
            ],
            edit_uris=[
                EditOp(
                    memory_type="tools",
                    fields={"tool_name": "edit_tool"},
                    patches={"content": "Updated"},
                ),
            ],
            delete_uris=[
                DeleteOp(
                    memory_type="preferences",
                    fields={"topic": "Delete me"},
                ),
            ],
        )

        resolved = resolve_all_operations(operations, test_registry)

        assert resolved.has_errors() is False
        assert len(resolved.write_operations) == 1
        assert len(resolved.edit_operations) == 1
        assert len(resolved.delete_operations) == 1

        # Verify resolved URIs
        assert resolved.write_operations[0][1] == "viking://user/default/memories/preferences/Write test.md"
        assert resolved.edit_operations[0][1] == "viking://agent/default/memories/tools/edit_tool.md"
        assert resolved.delete_operations[0][1] == "viking://user/default/memories/preferences/Delete me.md"

    def test_resolve_all_operations_with_errors(self, test_registry):
        """Test resolving operations with errors."""
        operations = MemoryOperations(
            write_uris=[
                WriteOp(
                    memory_type="unknown",
                    fields={},
                ),
            ],
        )

        resolved = resolve_all_operations(operations, test_registry)

        assert resolved.has_errors() is True
        assert len(resolved.errors) == 1
        assert "Failed to resolve write operation" in resolved.errors[0]
