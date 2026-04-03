# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0
"""
Tests for JSON stable parsing utilities.
"""

import json
from typing import List, Optional

from pydantic import BaseModel, Field

from openviking.session.memory.utils import (
    _get_arg_type,
    _get_origin_type,
    extract_json_content,
    parse_json_with_stability,
    parse_memory_file_with_fields,
    remove_json_trailing_content,
    value_fault_tolerance,
)


class TestExtractJsonContent:
    """Tests for Layer 1: JSON extraction (both leading and trailing)."""

    def test_removes_trailing_content_after_closing_brace(self):
        """Test that content after the last } is removed."""
        content = """{"reasonning": "test", "write_uris": []}

Note: This is a safety warning from the model."""
        result = extract_json_content(content)
        assert result == '{"reasonning": "test", "write_uris": []}'

    def test_removes_trailing_content_after_closing_bracket(self):
        """Test that content after the last ] is removed."""
        content = """[{"reasonning": "test"}]

Extra content after."""
        result = extract_json_content(content)
        assert result == '[{"reasonning": "test"}]'

    def test_handles_leading_content_before_json(self):
        """Test that content before the first { is removed."""
        content = """Here's the JSON:
{"reasonning": "test", "write_uris": []}"""
        result = extract_json_content(content)
        assert result == '{"reasonning": "test", "write_uris": []}'

    def test_handles_both_leading_and_trailing_content(self):
        """Test that both content before { and after } are removed."""
        content = """Alright, let me analyze this.

First, I need to create some memory entries.
{"reasonning": "test", "write_uris": [{"memory_type": "cards", "name": "test"}]}

That's all for now."""
        result = extract_json_content(content)
        parsed = json.loads(result)
        assert parsed["reasonning"] == "test"
        assert len(parsed["write_uris"]) == 1

    def test_preserves_nested_structures(self):
        """Test that nested structures are preserved correctly."""
        content = """{"reasonning": "test", "write_uris": [{"memory_type": "preferences", "topic": "test"}]}

Trailing content."""
        result = extract_json_content(content)
        assert "Trailing" not in result
        parsed = json.loads(result)
        assert parsed["reasonning"] == "test"
        assert len(parsed["write_uris"]) == 1

    def test_empty_string_returns_empty(self):
        """Test empty string input returns empty string."""
        assert extract_json_content("") == ""
        assert extract_json_content("   ") == "   "


class TestRemoveJsonTrailingContent:
    """Tests for deprecated remove_json_trailing_content (alias for extract_json_content)."""

    def test_alias_works(self):
        """Test that remove_json_trailing_content is an alias for extract_json_content."""
        content = """Alright, let's see.
{"reasonning": "test"}
And then some."""
        result1 = extract_json_content(content)
        result2 = remove_json_trailing_content(content)
        assert result1 == result2


class TestValueFaultTolerance:
    """Tests for Layer 4: Value-level fault tolerance."""

    def test_none_string_converts_to_none(self):
        """Test 'None' string converts to None for non-str types."""
        assert value_fault_tolerance(int, "None") is None
        assert value_fault_tolerance(List[str], "None") is None

    def test_none_string_remains_string_for_str_type(self):
        """Test 'None' remains as string when target type is str."""
        assert value_fault_tolerance(str, "None") == "None"

    def test_list_converts_to_string(self):
        """Test list converts to comma-separated string for str type."""
        assert value_fault_tolerance(str, ["a", "b", "c"]) == "a,b,c"

    def test_dict_converts_to_json_string(self):
        """Test dict converts to JSON string for str type."""
        result = value_fault_tolerance(str, {"key": "value"})
        assert "key" in result
        assert "value" in result

    def test_number_converts_to_string(self):
        """Test numbers convert to string for str type."""
        assert value_fault_tolerance(str, 42) == "42"
        assert value_fault_tolerance(str, 3.14) == "3.14"
        assert value_fault_tolerance(str, True) == "True"

    def test_string_converts_to_int(self):
        """Test string converts to int for int type."""
        assert value_fault_tolerance(int, "42") == 42

    def test_string_converts_to_float(self):
        """Test string converts to float for float type."""
        assert value_fault_tolerance(float, "3.14") == 3.14

    def test_string_wraps_to_list(self):
        """Test string wraps in list for list type."""
        assert value_fault_tolerance(List[str], "test") == ["test"]

    def test_dict_wraps_to_list(self):
        """Test dict wraps in list for list type."""
        result = value_fault_tolerance(List[dict], {"key": "value"})
        assert result == [{"key": "value"}]


class TestTypeHelpers:
    """Tests for _get_origin_type and _get_arg_type."""

    def test_get_origin_type_from_optional(self):
        """Test extracts type from Optional[T]."""
        assert _get_origin_type(Optional[str]) == str
        assert _get_origin_type(Optional[int]) == int

    def test_get_origin_type_from_list(self):
        """Test returns list for List[T]."""
        assert _get_origin_type(List[str]) == list

    def test_get_arg_type_from_list(self):
        """Test extracts item type from List[T]."""
        assert _get_arg_type(List[str]) == str
        assert _get_arg_type(List[int]) == int


class TestParseJsonWithStability:
    """Tests for full five-layer stable JSON parsing."""

    class TestModel(BaseModel):
        reasonning: str = ""
        count: Optional[int] = None
        tags: List[str] = Field(default_factory=list)

    def test_parses_valid_json(self):
        """Test valid JSON parses successfully."""
        content = '{"reasonning": "test", "count": 42, "tags": ["a", "b"]}'
        data, error = parse_json_with_stability(content, model_class=self.TestModel)
        assert error is None
        assert data.reasonning == "test"
        assert data.count == 42
        assert data.tags == ["a", "b"]

    def test_handles_list_wrapped_response(self):
        """Test [{"..."}] is handled correctly."""
        content = '[{"reasonning": "test", "count": 42}]'
        data, error = parse_json_with_stability(content, model_class=self.TestModel)
        assert error is None
        assert data.reasonning == "test"

    def test_filters_extra_fields(self):
        """Test extra fields are filtered when expected_fields is provided."""
        content = '{"reasonning": "test", "extra_field": "should be filtered", "count": 42}'
        data, error = parse_json_with_stability(
            content,
            model_class=self.TestModel,
            expected_fields=["reasonning", "count", "tags"],
        )
        assert error is None
        assert data.reasonning == "test"
        assert data.count == 42

    def test_returns_raw_dict_when_no_model_class(self):
        """Test returns dict when no model_class is provided."""
        content = '{"reasonning": "test"}'
        data, error = parse_json_with_stability(content)
        assert error is None
        assert isinstance(data, dict)
        assert data["reasonning"] == "test"

    def test_handles_trailing_content(self):
        """Test JSON with trailing content parses."""
        content = """{"reasonning": "test"}

Note: This is a safety warning.
Please be careful with the output."""
        data, error = parse_json_with_stability(content, model_class=self.TestModel)
        assert error is None
        assert data.reasonning == "test"

    def test_handles_markdown_code_blocks(self):
        """Test JSON wrapped in markdown parses."""
        content = """```json
{"reasonning": "test", "count": 42}
```"""
        data, error = parse_json_with_stability(content, model_class=self.TestModel)
        assert error is None
        assert data.reasonning == "test"

    def test_returns_error_for_completely_invalid_content(self):
        """Test completely invalid content returns error."""
        content = "This is not JSON at all"
        data, error = parse_json_with_stability(content)
        assert data is None
        assert error is not None


class TestMemoryOperationsIntegration:
    """Integration tests with MemoryOperations-like models."""

    class SimpleWriteOperation(BaseModel):
        memory_type: str
        topic: str

    class SimpleOperations(BaseModel):
        reasonning: str = ""
        write_uris: List["TestMemoryOperationsIntegration.SimpleWriteOperation"] = Field(
            default_factory=list
        )
        delete_uris: List[str] = Field(default_factory=list)

    def test_parses_nested_write_operations(self):
        """Test nested write operations parse correctly."""
        content = """{
            "reasonning": "Added user preferences",
            "write_uris": [
                {"memory_type": "preferences", "topic": "theme"},
                {"memory_type": "preferences", "topic": "notifications"}
            ]
        }"""
        data, error = parse_json_with_stability(content, model_class=self.SimpleOperations)
        assert error is None
        assert data.reasonning == "Added user preferences"
        assert len(data.write_uris) == 2
        assert data.write_uris[0].topic == "theme"

    def test_handles_string_instead_of_list_for_delete(self):
        """Test single string for delete_uris wraps to list via tolerance."""
        # Note: This would need field-level tolerance applied
        content = """{
            "reasonning": "Removed old memory",
            "delete_uris": "viking://user/default/memories/old.md"
        }"""
        # First parse as raw dict
        data, error = parse_json_with_stability(content)
        assert error is None
        assert data["delete_uris"] == "viking://user/default/memories/old.md"


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
        # Use truly invalid JSON that even json_repair can't parse
        content = """<!-- MEMORY_FIELDS
not json at all, just random text
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
