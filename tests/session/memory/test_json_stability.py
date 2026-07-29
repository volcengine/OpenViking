# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0
"""
Tests for JSON stable parsing utilities.
"""

import json
from typing import List, Optional
from unittest.mock import patch

from pydantic import BaseModel, Field

from openviking.session.memory.utils import (
    JsonUtils,
    _get_arg_type,
    _get_origin_type,
    extract_json_content,
    parse_json_with_stability,
    parse_memory_file_with_fields,
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
        assert _get_origin_type(Optional[str]) is str
        assert _get_origin_type(Optional[int]) is int

    def test_get_origin_type_from_list(self):
        """Test returns list for List[T]."""
        assert _get_origin_type(List[str]) is list

    def test_get_arg_type_from_list(self):
        """Test extracts item type from List[T]."""
        assert _get_arg_type(List[str]) is str
        assert _get_arg_type(List[int]) is int


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

    def test_handles_empty_list_for_operations_model(self):
        """A bare [] from the operations model (opted in via _allow_empty_list_response)
        is a valid 'no operations' outcome: mapped to an empty object, not an error."""

        class OperationsLike(BaseModel):
            reasonning: str = ""
            tags: List[str] = Field(default_factory=list)

        OperationsLike._allow_empty_list_response = True

        data, error = parse_json_with_stability("[]", model_class=OperationsLike)
        assert error is None
        assert data.tags == []

    def test_empty_list_fails_loud_without_opt_in(self):
        """A bare [] for any model that has NOT opted in stays fail-loud, even if it
        happens to expose an is_empty() convenience method."""

        class HasIsEmptyButNotOptedIn(BaseModel):
            tags: List[str] = Field(default_factory=list)

            def is_empty(self) -> bool:
                return not self.tags

        for model in (self.TestModel, HasIsEmptyButNotOptedIn):
            data, error = parse_json_with_stability("[]", model_class=model)
            assert data is None
            assert error is not None

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


class TestJsonUtilsLoads:
    """Tests for JsonUtils.loads convenience parsing."""

    class TestModel(BaseModel):
        reasonning: str
        count: Optional[int] = None

    def test_loads_returns_raw_dict_without_model(self):
        """Test raw JSON loading still returns a dict without a model class."""
        data = JsonUtils.loads('{"reasonning": "test", "count": 42}')
        assert data == {"reasonning": "test", "count": 42}

    def test_loads_validates_pydantic_model(self):
        """Test model class loading uses a TypeAdapter instance."""
        data = JsonUtils.loads('{"reasonning": "test", "count": "42"}', self.TestModel)
        assert isinstance(data, self.TestModel)
        assert data.reasonning == "test"
        assert data.count == 42


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
        delete_ids: List[str] = Field(default_factory=list)

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
        """Test single string for delete_ids wraps to list via tolerance."""
        # Note: This would need field-level tolerance applied
        content = """{
            "reasonning": "Removed old memory",
            "delete_ids": "viking://user/default/memories/old.md"
        }"""
        # First parse as raw dict
        data, error = parse_json_with_stability(content)
        assert error is None
        assert data["delete_ids"] == "viking://user/default/memories/old.md"

    def test_recoverable_invalid_list_item_logs_below_error(self):
        """Test recoverable invalid list items do not emit error-level logs."""

        class SearchReplaceBlock(BaseModel):
            search: str
            replace: str

        class StrPatch(BaseModel):
            blocks: List[SearchReplaceBlock] = Field(default_factory=list)

        class PreferenceItem(BaseModel):
            content: str | StrPatch | None = None
            page_id: int | None = None

        class PreferenceOperations(BaseModel):
            preferences: List[PreferenceItem] = Field(default_factory=list)

        content = json.dumps(
            {
                "preferences": [
                    {
                        "content": {
                            "blocks": [
                                {"search": "old", "replace": "new"},
                                {"page_id": 8},
                            ]
                        },
                        "page_id": 8,
                    }
                ]
            }
        )

        with patch("openviking.session.memory.utils.json_parser.tracer.error") as mock_error:
            with patch("openviking.session.memory.utils.json_parser.tracer.info") as mock_info:
                data, error = parse_json_with_stability(content, model_class=PreferenceOperations)

        assert error is None
        assert data.preferences == []
        assert mock_error.call_count == 0
        assert mock_info.call_count >= 1

    def test_recoverable_model_fallback_does_not_log_exception(self):
        """Test recoverable model fallback avoids exception-level logging."""

        class SearchReplaceBlock(BaseModel):
            search: str
            replace: str

        class StrPatch(BaseModel):
            blocks: List[SearchReplaceBlock] = Field(default_factory=list)

        class PreferenceItem(BaseModel):
            content: str | StrPatch | None = None
            page_id: int | None = None

        class PreferenceOperations(BaseModel):
            preferences: List[PreferenceItem] = Field(default_factory=list)

        content = json.dumps(
            {
                "preferences": [
                    {
                        "content": {
                            "blocks": [
                                {"search": "old", "replace": "new"},
                                {"page_id": 8},
                            ]
                        },
                        "page_id": 8,
                    }
                ]
            }
        )

        with patch(
            "openviking.session.memory.utils.json_parser.logger.exception"
        ) as mock_exception:
            data, error = parse_json_with_stability(content, model_class=PreferenceOperations)

        assert error is None
        assert data.preferences == []
        assert mock_exception.call_count == 0


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


class TestRequiredIdentityDefaults:
    """Regression tests: a missing required field must not empty the whole list.

    Models often get the memory content right while omitting a boilerplate
    identity field (``user``, ``category``, ``page_id``).  Before the fix,
    pydantic rejected such an item and the per-field tolerance fallback
    returned an empty list together with ``error=None`` - extraction reported
    success and stored nothing.
    """

    class _PreferenceItem(BaseModel):
        page_id: int
        user: str
        topic: str
        content: Optional[str] = None

    class _EntityItem(BaseModel):
        page_id: int
        category: str
        name: str
        content: Optional[str] = None

    class _Operations(BaseModel):
        preferences: List["TestRequiredIdentityDefaults._PreferenceItem"] = Field(
            default_factory=list
        )
        entities: List["TestRequiredIdentityDefaults._EntityItem"] = Field(default_factory=list)

    def test_missing_user_field_does_not_drop_preference(self):
        """A preference without `user` must survive instead of being dropped."""
        content = json.dumps(
            {"preferences": [{"page_id": 101, "topic": "editor", "content": "spaces"}]}
        )
        result, error = parse_json_with_stability(content, self._Operations)

        assert error is None
        assert len(result.preferences) == 1, "item was silently dropped"
        assert result.preferences[0].topic == "editor"
        assert result.preferences[0].user == "default"

    def test_missing_category_field_does_not_drop_entity(self):
        """An entity without `category` must survive."""
        content = json.dumps(
            {"entities": [{"page_id": 102, "name": "widget", "content": "a widget"}]}
        )
        result, error = parse_json_with_stability(content, self._Operations)

        assert error is None
        assert len(result.entities) == 1
        assert result.entities[0].name == "widget"
        assert result.entities[0].category == "general"

    def test_missing_page_id_is_generated(self):
        """Items without page_id get generated ids >= 100, unique per item."""
        content = json.dumps(
            {
                "preferences": [
                    {"user": "alice", "topic": "theme", "content": "dark"},
                    {"user": "alice", "topic": "font", "content": "mono"},
                ]
            }
        )
        result, error = parse_json_with_stability(content, self._Operations)

        assert error is None
        assert len(result.preferences) == 2
        page_ids = [p.page_id for p in result.preferences]
        assert all(pid >= 100 for pid in page_ids)
        assert len(set(page_ids)) == 2, "generated page_ids must be unique"

    def test_existing_values_are_never_overwritten(self):
        """Supplied values must win over the conventional defaults."""
        content = json.dumps(
            {
                "preferences": [{"page_id": 7, "user": "bob", "topic": "shell", "content": "fish"}],
                "entities": [{"page_id": 8, "category": "hardware", "name": "printer"}],
            }
        )
        result, error = parse_json_with_stability(content, self._Operations)

        assert error is None
        assert result.preferences[0].user == "bob"
        assert result.preferences[0].page_id == 7
        assert result.entities[0].category == "hardware"
        assert result.entities[0].page_id == 8

    def test_fully_valid_payload_is_unchanged(self):
        """A complete payload must pass through untouched (no regression)."""
        content = json.dumps(
            {"preferences": [{"page_id": 100, "user": "carol", "topic": "lang", "content": "go"}]}
        )
        result, error = parse_json_with_stability(content, self._Operations)

        assert error is None
        assert len(result.preferences) == 1
        assert result.preferences[0].user == "carol"
        assert result.preferences[0].content == "go"

    def test_generated_page_id_does_not_collide_with_supplied_one(self):
        """A generated id must never reuse an id the model already supplied.

        Collisions make link resolution order-dependent, because links address
        pages by id alone and two pages sharing an id are indistinguishable.
        """
        content = json.dumps(
            {
                "preferences": [
                    # Model supplied exactly the first id the allocator would pick.
                    {"page_id": 100, "user": "dana", "topic": "editor", "content": "vim"},
                    {"user": "dana", "topic": "shell", "content": "zsh"},
                ]
            }
        )
        result, error = parse_json_with_stability(content, self._Operations)

        assert error is None
        assert len(result.preferences) == 2
        page_ids = [p.page_id for p in result.preferences]
        assert len(set(page_ids)) == 2, f"page_id collision: {page_ids}"
        assert 100 in page_ids, "supplied id must be preserved"

    def test_generated_ids_skip_all_supplied_ids_across_types(self):
        """Allocation is global: ids taken in one memory type block another."""
        content = json.dumps(
            {
                "preferences": [{"page_id": 101, "user": "e", "topic": "t", "content": "c"}],
                "entities": [
                    {"page_id": 100, "category": "hw", "name": "printer"},
                    {"category": "hw", "name": "scanner"},
                ],
            }
        )
        result, error = parse_json_with_stability(content, self._Operations)

        assert error is None
        all_ids = [p.page_id for p in result.preferences] + [e.page_id for e in result.entities]
        assert len(set(all_ids)) == len(all_ids), f"collision across types: {all_ids}"
        assert {100, 101} <= set(all_ids), "supplied ids must be preserved"

    def test_string_page_ids_are_honoured_when_avoiding_collisions(self):
        """A supplied id given as a string still blocks that number."""
        content = json.dumps(
            {
                "preferences": [
                    {"page_id": "100", "user": "f", "topic": "t", "content": "c"},
                    {"user": "f", "topic": "u", "content": "d"},
                ]
            }
        )
        result, error = parse_json_with_stability(content, self._Operations)

        assert error is None
        page_ids = [int(p.page_id) for p in result.preferences]
        assert len(set(page_ids)) == 2, f"string id not honoured: {page_ids}"
