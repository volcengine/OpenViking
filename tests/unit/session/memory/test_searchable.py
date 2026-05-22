# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0

import pytest

from openviking.session.memory.dataclass import MemoryField
from openviking.session.memory.merge_op.base import FieldType
from openviking.session.memory.memory_type_registry import MemoryTypeRegistry


class TestMemoryFieldSearchable:
    def test_default_is_false(self):
        field = MemoryField(name="test", field_type=FieldType.STRING, description="test")
        assert field.searchable is False

    def test_explicit_true(self):
        field = MemoryField(
            name="content", field_type=FieldType.STRING, description="desc", searchable=True
        )
        assert field.searchable is True

    def test_explicit_false(self):
        field = MemoryField(
            name="call_count", field_type=FieldType.INT64, description="count", searchable=False
        )
        assert field.searchable is False


class TestSearchableYamlParsing:
    @pytest.fixture(autouse=True)
    def setup(self):
        self.registry = MemoryTypeRegistry()

    def test_events_searchable_fields(self):
        schema = self.registry.get("events")
        searchable = {f.name for f in schema.fields if f.searchable}
        assert searchable == {"event_name", "goal", "summary"}

    def test_tools_searchable_fields(self):
        schema = self.registry.get("tools")
        searchable = {f.name for f in schema.fields if f.searchable}
        assert searchable == set()  # all rendered in content_template

    def test_entities_searchable_fields(self):
        schema = self.registry.get("entities")
        searchable = {f.name for f in schema.fields if f.searchable}
        assert searchable == {"category", "name"}

    def test_preferences_searchable_fields(self):
        schema = self.registry.get("preferences")
        searchable = {f.name for f in schema.fields if f.searchable}
        assert searchable == {"user", "topic"}

    def test_profile_searchable_fields(self):
        schema = self.registry.get("profile")
        searchable = {f.name for f in schema.fields if f.searchable}
        assert searchable == set()

    def test_soul_searchable_fields(self):
        schema = self.registry.get("soul")
        searchable = {f.name for f in schema.fields if f.searchable}
        assert searchable == set()  # all rendered in content_template

    def test_skills_searchable_fields(self):
        schema = self.registry.get("skills")
        searchable = {f.name for f in schema.fields if f.searchable}
        assert searchable == set()  # all rendered in content_template


class TestEmbeddingTextConstruction:
    """Test that embedding text only includes searchable fields in JSON format."""

    def test_searchable_fields_as_json(self):
        parsed = {
            "content": "Body content here",
            "tool_name": "web_search",
            "call_count": "42",
            "when_to_use": "When you need web info",
        }
        abstract = parsed.get("content", "")
        searchable_fields = {"when_to_use"}
        embedding_parts = []
        if abstract:
            embedding_parts.append(abstract)
        searchable_data = {}
        for field_name in searchable_fields:
            if field_name == "content":
                continue
            field_value = parsed.get(field_name)
            if field_value is not None:
                field_str = str(field_value) if not isinstance(field_value, str) else field_value
                if field_str.strip():
                    searchable_data[field_name] = field_str.strip()
        if searchable_data:
            import json

            embedding_parts.append(json.dumps(searchable_data, ensure_ascii=False))
        embedding_text = "\n\n".join(embedding_parts)
        assert "web_search" not in embedding_text
        assert "42" not in embedding_text
        assert '"when_to_use": "When you need web info"' in embedding_text
        assert "Body content here" in embedding_text

    def test_excludes_links_and_backlinks(self):
        parsed = {
            "content": "Body content",
            "summary": "A summary",
            "links": [{"from_uri": "a", "to_uri": "b"}],
            "backlinks": [],
        }
        searchable_fields = {"summary"}
        embedding_parts = []
        abstract = parsed.get("content", "")
        if abstract:
            embedding_parts.append(abstract)
        searchable_data = {}
        for field_name in searchable_fields:
            if field_name == "content":
                continue
            field_value = parsed.get(field_name)
            if field_value is not None:
                field_str = str(field_value) if not isinstance(field_value, str) else field_value
                if field_str.strip():
                    searchable_data[field_name] = field_str.strip()
        if searchable_data:
            import json

            embedding_parts.append(json.dumps(searchable_data, ensure_ascii=False))
        embedding_text = "\n\n".join(embedding_parts)
        assert "links" not in embedding_text
        assert "from_uri" not in embedding_text

    def test_no_searchable_data_no_json_appended(self):
        parsed = {
            "content": "Body content only",
            "event_name": "road_trip",
            "ranges": "0-5",
        }
        abstract = parsed.get("content", "")
        searchable_fields = set()  # no searchable fields
        embedding_parts = []
        if abstract:
            embedding_parts.append(abstract)
        searchable_data = {}
        for field_name in searchable_fields:
            if field_name == "content":
                continue
        if searchable_data:
            import json

            embedding_parts.append(json.dumps(searchable_data, ensure_ascii=False))
        embedding_text = "\n\n".join(embedding_parts)
        assert embedding_text == "Body content only"
        assert "{" not in embedding_text
