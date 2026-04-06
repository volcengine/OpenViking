# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0
"""
Tests for memory utilities - URI generation, etc.
"""

import pytest

from openviking.session.memory.dataclass import (
    MemoryField,
    MemoryOperations,
    MemoryTypeSchema,
)
from openviking.session.memory.memory_type_registry import MemoryTypeRegistry
from openviking.session.memory.merge_op.base import FieldType, MergeOp
from openviking.session.memory.utils import (
    collect_allowed_directories,
    collect_allowed_path_patterns,
    generate_uri,
    is_uri_allowed,
    is_uri_allowed_for_schema,
    parse_memory_file_with_fields,
    resolve_all_operations,
    validate_uri_template,
)


class TestUriGeneration:
    """Tests for URI generation."""

    def test_generate_uri_preferences(self):
        """Test generating URI for preferences memory type."""
        memory_type = MemoryTypeSchema(
            memory_type="preferences",
            description="User preference memory",
            directory="viking://user/{{ user_space }}/memories/preferences",
            filename_template="{{ topic }}.md",
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
            directory="viking://agent/{{ agent_space }}/memories/tools",
            filename_template="{{ tool_name }}.md",
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
            directory="viking://user/{{ user_space }}/memories/test",
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
            filename_template="{{ name }}.md",
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
            directory="viking://user/{{ user_space }}/memories/preferences",
            filename_template="{{ topic }}.md",
            fields=[],
        )

        with pytest.raises(ValueError, match="Missing template variable"):
            generate_uri(memory_type, {})

    def test_generate_uri_none_value(self):
        """Test error when variable has None value."""
        memory_type = MemoryTypeSchema(
            memory_type="preferences",
            description="User preference memory",
            directory="viking://user/{{ user_space }}/memories/preferences",
            filename_template="{{ topic }}.md",
            fields=[],
        )

        with pytest.raises(ValueError, match="has None value"):
            generate_uri(memory_type, {"topic": None})

    def test_validate_uri_template_valid(self):
        """Test validating a valid URI template."""
        memory_type = MemoryTypeSchema(
            memory_type="preferences",
            description="User preference memory",
            directory="viking://user/{{ user_space }}/memories/preferences",
            filename_template="{{ topic }}.md",
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
            directory="viking://user/{{ user_space }}/memories/preferences",
            filename_template="{{ missing_field }}.md",
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
                directory="viking://user/{{ user_space }}/memories/preferences",
                filename_template="{{ topic }}.md",
                fields=[],
            ),
            MemoryTypeSchema(
                memory_type="tools",
                description="Tools",
                directory="viking://agent/{{ agent_space }}/memories/tools",
                filename_template="{{ tool_name }}.md",
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

        dirs = collect_allowed_directories(
            [s for s in schemas if s.enabled], user_space="default", agent_space="default"
        )

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
                directory="viking://user/{{ user_space }}/memories/preferences",
                filename_template="{{ topic }}.md",
                fields=[],
            ),
        ]

        patterns = collect_allowed_path_patterns(
            schemas, user_space="default", agent_space="default"
        )

        assert patterns == {
            "viking://user/default/memories/preferences/{{ topic }}.md",
        }

    def test_is_uri_allowed_by_directory(self):
        """Test URI allowed by matching directory prefix."""
        allowed_dirs = {
            "viking://user/default/memories/preferences",
            "viking://agent/default/memories/tools",
        }
        allowed_patterns = set()

        assert (
            is_uri_allowed(
                "viking://user/default/memories/preferences/test.md",
                allowed_dirs,
                allowed_patterns,
            )
            is True
        )

        assert (
            is_uri_allowed(
                "viking://user/default/memories/preferences",
                allowed_dirs,
                allowed_patterns,
            )
            is True
        )

        assert (
            is_uri_allowed(
                "viking://user/default/memories/preferences/subdir/test.md",
                allowed_dirs,
                allowed_patterns,
            )
            is True
        )

    def test_is_uri_allowed_by_pattern(self):
        """Test URI allowed by matching pattern."""
        allowed_dirs = set()
        allowed_patterns = {
            "viking://user/default/memories/preferences/{{ topic }}.md",
        }

        assert (
            is_uri_allowed(
                "viking://user/default/memories/preferences/Python code style.md",
                allowed_dirs,
                allowed_patterns,
            )
            is True
        )

    def test_is_uri_disallowed(self):
        """Test URI not allowed."""
        allowed_dirs = {
            "viking://user/default/memories/preferences",
        }
        allowed_patterns = set()

        assert (
            is_uri_allowed(
                "viking://user/default/memories/other/test.md",
                allowed_dirs,
                allowed_patterns,
            )
            is False
        )

        assert (
            is_uri_allowed(
                "viking://user/other/memories/preferences/test.md",
                allowed_dirs,
                allowed_patterns,
            )
            is False
        )

    def test_is_uri_allowed_for_schema(self):
        """Test checking URI against schemas."""
        schemas = [
            MemoryTypeSchema(
                memory_type="preferences",
                description="Preferences",
                directory="viking://user/{{ user_space }}/memories/preferences",
                filename_template="{{ topic }}.md",
                fields=[],
            ),
        ]

        assert (
            is_uri_allowed_for_schema(
                "viking://user/default/memories/preferences/test.md",
                schemas,
            )
            is True
        )

        assert (
            is_uri_allowed_for_schema(
                "viking://user/default/memories/other/test.md",
                schemas,
            )
            is False
        )


class TestUriResolution:
    """Tests for URI resolution methods."""

    @pytest.fixture
    def test_registry(self):
        """Create a test registry with sample schemas."""
        registry = MemoryTypeRegistry()

        # Add preferences schema
        registry.register(
            MemoryTypeSchema(
                memory_type="preferences",
                description="User preferences",
                directory="viking://user/{{ user_space }}/memories/preferences",
                filename_template="{{ topic }}.md",
                fields=[
                    MemoryField(name="topic", field_type=FieldType.STRING, description="Topic"),
                ],
            )
        )

        # Add tools schema
        registry.register(
            MemoryTypeSchema(
                memory_type="tools",
                description="Tool memories",
                directory="viking://agent/{{ agent_space }}/memories/tools",
                filename_template="{{ tool_name }}.md",
                fields=[
                    MemoryField(
                        name="tool_name", field_type=FieldType.STRING, description="Tool name"
                    ),
                ],
            )
        )

        return registry

    def test_resolve_all_operations(self, test_registry):
        """Test resolving all operations at once."""
        operations = MemoryOperations(
            write_uris=[
                {
                    "memory_type": "preferences",
                    "topic": "Write test",
                    "content": "Write content",
                },
            ],
            edit_uris=[
                {
                    "memory_type": "tools",
                    "tool_name": "edit_tool",
                    "content": "Updated",
                },
            ],
            delete_uris=[
                "viking://user/default/memories/preferences/Delete me.md",
            ],
        )

        resolved = resolve_all_operations(operations, test_registry)

        assert resolved.has_errors() is False
        # All operations are now unified into operations list
        assert len(resolved.operations) == 2
        assert len(resolved.delete_operations) == 1

        # Verify resolved URIs - both write and edit go to operations list
        uris = [op.uri for op in resolved.operations]
        assert "viking://user/default/memories/preferences/Write test.md" in uris
        assert "viking://agent/default/memories/tools/edit_tool.md" in uris
        assert (
            resolved.delete_operations[0][1]
            == "viking://user/default/memories/preferences/Delete me.md"
        )

    def test_resolve_all_operations_with_errors(self, test_registry):
        """Test resolving operations with errors."""
        operations = MemoryOperations(
            write_uris=[
                {
                    "memory_type": "unknown",
                },
            ],
        )

        resolved = resolve_all_operations(operations, test_registry)

        assert resolved.has_errors() is True
        assert len(resolved.errors) == 1
        assert "Failed to resolve" in resolved.errors[0]


class TestParseMemoryFileWithFields:
    """Tests for parse_memory_file_with_fields function."""

    def test_parses_memory_fields_comment(self):
        """Test parsing MEMORY_FIELDS HTML comment."""
        content = """<!-- MEMORY_FIELDS
{
  "tool_name": "web_search",
  "static_desc": "Searches the web for information",
  "total_calls": 100,
  "success_count": 92
}
-->
Here is the actual file content.
It has multiple lines."""
        result = parse_memory_file_with_fields(content)
        assert result["tool_name"] == "web_search"
        assert result["static_desc"] == "Searches the web for information"
        assert result["total_calls"] == 100
        assert result["success_count"] == 92
        assert "Here is the actual file content" in result["content"]
        assert "<!-- MEMORY_FIELDS" not in result["content"]

    def test_returns_only_content_when_no_comment(self):
        """Test returns only content when no MEMORY_FIELDS comment."""
        content = "Just plain file content\nwithout any special comments"
        result = parse_memory_file_with_fields(content)
        assert list(result.keys()) == ["content"]
        assert result["content"] == content

    def test_handles_empty_content(self):
        """Test handles empty string input."""
        result = parse_memory_file_with_fields("")
        assert result["content"] == ""

    def test_handles_invalid_json_in_comment(self):
        """Test handles invalid JSON in MEMORY_FIELDS comment gracefully."""
        content = """<!-- MEMORY_FIELDS
{
  "tool_name": "web_search",
  invalid json here
}
-->
File content"""
        result = parse_memory_file_with_fields(content)
        assert "File content" in result["content"]
        # No extra fields added
        assert "not" not in result

    def test_removes_comment_from_content(self):
        """Test that the comment is completely removed from content."""
        content = """Before comment
<!-- MEMORY_FIELDS {"test": "value"} -->
After comment"""
        result = parse_memory_file_with_fields(content)
        assert "<!-- MEMORY_FIELDS" not in result["content"]
        assert "Before comment" in result["content"]
        assert "After comment" in result["content"]
        assert result["test"] == "value"

    def test_fields_on_same_line(self):
        """Test MEMORY_FIELDS on single line."""
        content = """<!-- MEMORY_FIELDS {"tool_name": "test", "value": 42} -->
Content"""
        result = parse_memory_file_with_fields(content)
        assert result["tool_name"] == "test"
        assert result["value"] == 42
        assert result["content"] == "Content"
