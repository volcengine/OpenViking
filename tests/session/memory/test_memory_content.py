# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: Apache-2.0
"""
Tests for memory_content serialization/deserialization.
"""

from datetime import datetime

import pytest

from openviking.session.memory.memory_content import (
    deserialize_content,
    deserialize_full,
    deserialize_metadata,
    serialize_with_metadata,
)


class TestSerializeWithMetadata:
    """Tests for serialize_with_metadata."""

    def test_basic_serialization(self):
        """Test basic serialization with content and metadata."""
        content = "# User Profile\n\nUser is an AI engineer."
        metadata = {
            "memory_type": "profile",
            "name": "User Profile",
            "tags": ["user", "profile"],
        }

        result = serialize_with_metadata(content, metadata)

        # Check content is present
        assert "# User Profile" in result
        assert "User is an AI engineer." in result

        # Check metadata comment is present
        assert "<!-- MEMORY_FIELDS" in result
        assert '"memory_type": "profile"' in result
        assert '"name": "User Profile"' in result
        assert '"user"' in result
        assert '"profile"' in result

    def test_with_datetime(self):
        """Test serialization with datetime objects."""
        content = "Test content"
        test_time = datetime(2026, 3, 20, 10, 0, 0)
        metadata = {
            "created_at": test_time,
            "updated_at": test_time,
        }

        result = serialize_with_metadata(content, metadata)

        assert '"created_at": "2026-03-20T10:00:00"' in result
        assert '"updated_at": "2026-03-20T10:00:00"' in result

    def test_empty_metadata(self):
        """Test serialization with empty metadata."""
        content = "Test content"
        metadata = {}

        result = serialize_with_metadata(content, metadata)

        # Should just return the content without comment
        assert result == content

    def test_none_metadata_values(self):
        """Test that None values are skipped."""
        content = "Test content"
        metadata = {
            "name": "Test",
            "abstract": None,
            "overview": None,
        }

        result = serialize_with_metadata(content, metadata)

        assert '"name": "Test"' in result
        assert "abstract" not in result
        assert "overview" not in result

    def test_empty_content(self):
        """Test serialization with empty content."""
        content = ""
        metadata = {"name": "Test"}

        result = serialize_with_metadata(content, metadata)

        # Should start with the comment (no leading newlines)
        assert result.startswith("<!-- MEMORY_FIELDS")

    def test_template_mode_with_fields(self):
        """Test serialization with template mode fields."""
        content = """Tool: web_search

Static Description:
"Searches the web for information"
"""
        metadata = {
            "memory_type": "tools",
            "fields": {
                "tool_name": "web_search",
                "static_desc": "Searches the web for information",
                "total_calls": 100,
            },
            "tags": ["tool", "web_search"],
        }

        result = serialize_with_metadata(content, metadata)

        assert "Tool: web_search" in result
        assert '"tool_name": "web_search"' in result
        assert '"total_calls": 100' in result


class TestDeserializeContent:
    """Tests for deserialize_content."""

    def test_extract_content(self):
        """Test extracting content from serialized string."""
        full_content = """# User Profile

User is an AI engineer.

<!-- MEMORY_FIELDS
{
  "memory_type": "profile",
  "name": "User Profile"
}
-->"""

        content = deserialize_content(full_content)

        assert "# User Profile" in content
        assert "User is an AI engineer." in content
        assert "<!-- MEMORY_FIELDS" not in content

    def test_backward_compatibility_no_comment(self):
        """Test that content without metadata comment works."""
        original_content = "# Old Format\n\nJust content without metadata."

        content = deserialize_content(original_content)

        assert content == original_content

    def test_empty_content(self):
        """Test deserialize with empty content."""
        assert deserialize_content("") == ""
        assert deserialize_content(None) == ""  # type: ignore

    def test_comment_at_end_no_leading_newlines(self):
        """Test comment that appears at end without leading newlines."""
        full_content = """# Test
Content
<!-- MEMORY_FIELDS
{"name": "Test"}
-->"""

        content = deserialize_content(full_content)

        assert "# Test" in content
        assert "Content" in content
        assert "<!--" not in content


class TestDeserializeMetadata:
    """Tests for deserialize_metadata."""

    def test_extract_metadata(self):
        """Test extracting metadata from serialized string."""
        full_content = """# User Profile

User is an AI engineer.

<!-- MEMORY_FIELDS
{
  "memory_type": "profile",
  "name": "User Profile",
  "tags": ["user", "profile"]
}
-->"""

        metadata = deserialize_metadata(full_content)

        assert metadata is not None
        assert metadata["memory_type"] == "profile"
        assert metadata["name"] == "User Profile"
        assert metadata["tags"] == ["user", "profile"]

    def test_datetime_deserialization(self):
        """Test that datetime strings are parsed back to datetime objects."""
        full_content = """Test

<!-- MEMORY_FIELDS
{
  "created_at": "2026-03-20T10:00:00",
  "updated_at": "2026-03-20T11:00:00"
}
-->"""

        metadata = deserialize_metadata(full_content)

        assert metadata is not None
        assert isinstance(metadata["created_at"], datetime)
        assert metadata["created_at"].year == 2026
        assert metadata["created_at"].month == 3
        assert metadata["created_at"].day == 20
        assert metadata["created_at"].hour == 10

    def test_no_metadata(self):
        """Test deserialize with no metadata comment."""
        content = "# Just Content\n\nNo comment here."

        metadata = deserialize_metadata(content)

        assert metadata is None

    def test_corrupted_metadata(self):
        """Test that corrupted metadata returns None gracefully."""
        full_content = """Test

<!-- MEMORY_FIELDS
{
  "invalid": json,
  "structure": true
}
-->"""

        metadata = deserialize_metadata(full_content)

        assert metadata is None

    def test_empty_content(self):
        """Test deserialize metadata with empty content."""
        assert deserialize_metadata("") is None
        assert deserialize_metadata(None) is None  # type: ignore


class TestDeserializeFull:
    """Tests for deserialize_full."""

    def test_deserialize_both(self):
        """Test deserialize_full returns both content and metadata."""
        full_content = """# User Profile

User content.

<!-- MEMORY_FIELDS
{
  "name": "Test",
  "tags": ["test"]
}
-->"""

        content, metadata = deserialize_full(full_content)

        assert "# User Profile" in content
        assert metadata is not None
        assert metadata["name"] == "Test"

    def test_backward_compatible(self):
        """Test deserialize_full with old format (no metadata)."""
        content = "# Old Format\n\nJust content."

        extracted_content, metadata = deserialize_full(content)

        assert extracted_content == content
        assert metadata is None


class TestRoundTrip:
    """Tests for round-trip serialization/deserialization."""

    def test_round_trip(self):
        """Test full round-trip works correctly."""
        original_content = "# Test\n\nThis is a test."
        original_metadata = {
            "memory_type": "test",
            "name": "Test Memory",
            "tags": ["test", "example"],
            "created_at": datetime(2026, 3, 20, 10, 0, 0),
        }

        # Serialize
        serialized = serialize_with_metadata(original_content, original_metadata)

        # Deserialize
        content, metadata = deserialize_full(serialized)

        # Verify
        assert content == original_content
        assert metadata is not None
        assert metadata["memory_type"] == "test"
        assert metadata["name"] == "Test Memory"
        assert metadata["tags"] == ["test", "example"]
        assert isinstance(metadata["created_at"], datetime)
        assert metadata["created_at"] == datetime(2026, 3, 20, 10, 0, 0)
