# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0
"""
Tests for memory utilities - URI generation, etc.
"""

import pytest

from openviking.session.memory.dataclass import (
    MemoryField,
    MemoryFile,
    MemoryTypeSchema,
)
from openviking.session.memory.merge_op.base import FieldType, MergeOp
from openviking.session.memory.utils import (
    generate_uri,
    parse_memory_file_with_fields,
    validate_uri_template,
)
from openviking.session.memory.utils.memory_file_utils import MemoryFileUtils


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
            directory="viking://user/{{ user_space }}/memories/tools",
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
            user_space="default",
        )

        assert uri == "viking://user/default/memories/tools/web_search.md"

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

    def test_generate_uri_makes_windows_unsafe_directory_segment_collision_resistant(self):
        """The exact #4308 shape is portable without changing the LLM field."""
        memory_type = MemoryTypeSchema(
            memory_type="events",
            description="Event memory",
            directory="viking://user/{{ user_space }}/memories/events",
            filename_template="2026/07/30/{{ event_name }}.md",
            fields=[
                MemoryField(
                    name="event_name",
                    field_type=FieldType.STRING,
                    merge_op=MergeOp.IMMUTABLE,
                )
            ],
        )
        fields = {"event_name": "Desktop /new report"}

        uri = generate_uri(memory_type, fields, user_space="default")

        assert uri == (
            "viking://user/default/memories/events/2026/07/30/"
            "Desktop~ov~02970756851a43cf/new report.md"
        )
        assert fields == {"event_name": "Desktop /new report"}

    def test_generate_uri_keeps_colliding_windows_names_distinct(self):
        memory_type = MemoryTypeSchema(
            memory_type="scratch",
            description="Scratch memory",
            directory="viking://user/{{ user_space }}/memories/scratch",
            filename_template="{{ name }}/item",
            fields=[
                MemoryField(
                    name="name",
                    field_type=FieldType.STRING,
                    merge_op=MergeOp.IMMUTABLE,
                )
            ],
        )

        uris = {
            name: generate_uri(memory_type, {"name": name})
            for name in ("Desktop", "Desktop ", "Desktop.", "Desktop~notes", "_", "   ")
        }

        assert len(set(uris.values())) == len(uris)
        assert uris["Desktop"].endswith("/Desktop/item")
        assert uris["Desktop "].endswith("/Desktop~ov~02970756851a43cf/item")
        assert uris["Desktop."].endswith("/Desktop~ov~3c65b627123c925e/item")
        assert uris["Desktop~notes"].endswith("/Desktop~notes/item")
        assert uris["_"].endswith("/_/item")
        assert uris["   "].endswith("/_~ov~0aad7da77d2ed59c/item")

    @pytest.mark.parametrize(
        ("name", "expected_filename"),
        [
            ("CON.md", "_CON~ov~ab20d2f97c036c47.md"),
            ("bad:name.md", "bad_name~ov~a5895459cfd9b57c.md"),
        ],
    )
    def test_generate_uri_rewrites_other_windows_invalid_names_and_keeps_extension(
        self,
        name,
        expected_filename,
    ):
        memory_type = MemoryTypeSchema(
            memory_type="scratch",
            description="Scratch memory",
            directory="viking://user/{{ user_space }}/memories/scratch",
            filename_template="{{ name }}",
            fields=[],
        )

        uri = generate_uri(memory_type, {"name": name})

        assert uri.endswith(f"/{expected_filename}")

    def test_generate_uri_reserved_marker_cannot_alias_generated_name(self):
        memory_type = MemoryTypeSchema(
            memory_type="scratch",
            description="Scratch memory",
            directory="viking://user/{{ user_space }}/memories/scratch",
            filename_template="{{ name }}/item",
            fields=[],
        )
        generated_uri = generate_uri(memory_type, {"name": "Desktop "})
        generated_name = generated_uri.rsplit("/", 2)[-2]

        literal_uri = generate_uri(memory_type, {"name": generated_name})

        assert literal_uri != generated_uri

    def test_generate_uri_portability_applies_to_every_template_segment(self):
        memory_type = MemoryTypeSchema(
            memory_type="preferences",
            description="Preference memory",
            directory="viking://user/{{ user_space }}/memories/preferences",
            filename_template="{{ user }}/{{ topic }}",
            fields=[],
        )
        fields = {"user": "Alice ", "topic": "CON"}

        first_uri = generate_uri(memory_type, fields, user_space="default")
        second_uri = generate_uri(memory_type, fields, user_space="default")

        assert first_uri == second_uri
        assert first_uri == (
            "viking://user/default/memories/preferences/"
            "Alice~ov~11f75fc2e111eb7d/_CON~ov~a3dbc4b644a9a2c5"
        )
        assert fields == {"user": "Alice ", "topic": "CON"}

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

    def test_write_preserves_memory_type_in_memory_fields_comment(self):
        memory_file = MemoryFile(
            uri="viking://user/default/memories/preferences/code_style.md",
            memory_type="preferences",
            content="Prefers concise responses.",
            extra_fields={"topic": "code_style"},
        )

        written = MemoryFileUtils.write(memory_file)
        parsed = parse_memory_file_with_fields(written)

        assert parsed["memory_type"] == "preferences"
        assert parsed["topic"] == "code_style"
        assert parsed["version"] == 1
        assert parsed["content"] == "Prefers concise responses."

    def test_write_strips_user_identity_fields_from_memory_fields_comment(self):
        memory_file = MemoryFile(
            uri="viking://user/alice/memories/preferences/code_style.md",
            memory_type="preferences",
            content="Prefers concise responses.",
            extra_fields={
                "topic": "code_style",
                "user_id": "alice",
                "user_ids": ["alice", "bob"],
            },
        )

        written = MemoryFileUtils.write(memory_file)
        parsed = parse_memory_file_with_fields(written)

        assert "user_id" not in parsed
        assert "user_ids" not in parsed
        assert parsed["version"] == 1
        assert parsed["topic"] == "code_style"

    def test_read_preserves_markdown_links_in_content(self):
        raw_content = """2023-08-22 ChatLog\n\n[Calvin]: Worked with [Frank Ocean](../../../../entities/personal/calvin.md).\n\n<!-- MEMORY_FIELDS\n{\"memory_type\": \"events\", \"links\": [{\"to_uri\": \"viking://user/Calvin/memories/entities/personal/calvin.md\", \"link_type\": \"related_to\", \"match_text\": \"Frank\"}]}\n-->"""

        memory_file = MemoryFileUtils.read(
            raw_content,
            uri="viking://user/Calvin/memories/events/2023/08/22/collab_with_frank_ocean.md",
        )

        assert "[Frank Ocean](../../../../entities/personal/calvin.md)" in memory_file.content

    def test_memory_file_plain_content_strips_markdown_links(self):
        memory_file = MemoryFile(
            uri="viking://user/Calvin/memories/events/2023/08/22/collab_with_frank_ocean.md",
            content="Worked with [Frank Ocean](../../../../entities/personal/calvin.md).",
            links=[
                {
                    "to_uri": "viking://user/Calvin/memories/entities/personal/calvin.md",
                    "link_type": "related_to",
                    "match_text": "Frank",
                }
            ],
        )

        assert memory_file.plain_content() == "Worked with Frank Ocean."

    def test_write_does_not_put_uri_in_memory_fields_metadata(self):
        memory_file = MemoryFile(
            uri="viking://user/default/memories/experiences/source.md",
            memory_type="experiences",
            content="Source content mentions Target.",
            extra_fields={"_uri": "viking://stale/should-not-persist"},
            links=[
                {
                    "from_uri": "viking://user/default/memories/experiences/source.md",
                    "to_uri": "viking://user/default/memories/trajectories/target.md",
                    "link_type": "derived_from",
                    "match_text": "Target",
                }
            ],
        )

        written = MemoryFileUtils.write(memory_file)
        parsed = parse_memory_file_with_fields(written)

        assert "_uri" not in parsed
        assert "[Target](../trajectories/target.md)" in written
