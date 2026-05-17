# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0

import pytest

from openviking.session.memory.dataclass import (
    LinkType,
    MemoryTypeSchema,
    ResolvedOperation,
    ResolvedOperations,
    StoredLink,
    WikiLink,
)


class TestLinkType:
    def test_default_value_defined(self):
        assert LinkType.RELATED_TO == "related_to"


class TestWikiLink:
    def test_minimal_fields(self):
        link = WikiLink(f=1, t=2, match_text=None)
        assert link.f == 1
        assert link.t == 2
        assert link.link_type == "related_to"
        assert link.weight == 0.5
        assert link.match_text is None

    def test_full_fields(self):
        link = WikiLink(
            f=1,
            t=3,
            link_type="belongs_to",
            weight=0.9,
            match_text="Caroline",
            description="Preference belongs to Caroline",
        )
        assert link.link_type == "belongs_to"
        assert link.match_text == "Caroline"
        assert link.weight == 0.9

    def test_weight_is_preserved_when_valid(self):
        link = WikiLink(f=1, t=2, weight=0.8, match_text=None)
        assert link.weight == 0.8

    def test_weight_is_clamped_to_zero_when_negative(self):
        link = WikiLink(f=1, t=2, weight=-0.2, match_text=None)
        assert link.weight == 0.0

    def test_weight_is_clamped_to_one_when_above_range(self):
        link = WikiLink(f=1, t=2, weight=1.7, match_text=None)
        assert link.weight == 1.0

    def test_weight_defaults_to_mid_value_when_invalid(self):
        link = WikiLink.model_validate({"f": 1, "t": 2, "weight": "not-a-number", "match_text": None})
        assert link.weight == 0.5

    def test_json_schema_requires_match_text_field(self):
        schema = WikiLink.model_json_schema()

        assert "match_text" in schema["required"]
        assert any(option.get("type") == "string" for option in schema["properties"]["match_text"]["anyOf"])
        assert any(option.get("type") == "null" for option in schema["properties"]["match_text"]["anyOf"])

    def test_weight_schema_mentions_ranking_priority(self):
        schema = WikiLink.model_json_schema()
        description = schema["properties"]["weight"]["description"]
        assert "ranking score" in description
        assert "same anchor or attention" in description


class TestStoredLink:
    def test_link(self):
        link = StoredLink(
            from_uri="viking://a",
            to_uri="viking://b",
            link_type="belongs_to",
            weight=0.9,
            created_at="2026-05-09T10:00:00+00:00",
        )
        assert link.from_uri == "viking://a"
        assert link.to_uri == "viking://b"
        assert link.link_type == "belongs_to"

    def test_model_dump(self):
        link = StoredLink(
            from_uri="viking://a",
            to_uri="viking://b",
            link_type="related_to",
            created_at="2026-05-09T10:00:00+00:00",
        )
        d = link.model_dump()
        assert d["from_uri"] == "viking://a"
        assert d["to_uri"] == "viking://b"
        assert d["link_type"] == "related_to"
        assert "direction" not in d


class TestResolvedOperationsLinks:
    def test_default_empty_links(self):
        ops = ResolvedOperations(
            upsert_operations=[],
            delete_file_contents=[],
            errors=[],
        )
        assert ops.resolved_links == []

    def test_with_resolved_links(self):
        link = StoredLink(
            from_uri="viking://a",
            to_uri="viking://b",
            link_type="related_to",
            created_at="2026-05-09T10:00:00+00:00",
        )
        ops = ResolvedOperations(
            upsert_operations=[],
            delete_file_contents=[],
            errors=[],
            resolved_links=[link],
        )
        assert len(ops.resolved_links) == 1


class TestResolvedOperationPageId:
    def test_default_none(self):
        op = ResolvedOperation(
            old_memory_file_content=None,
            memory_fields={},
            memory_type="preferences",
            uris=[],
        )
        assert op.page_id is None

    def test_with_page_id(self):
        op = ResolvedOperation(
            old_memory_file_content=None,
            memory_fields={},
            memory_type="preferences",
            uris=[],
            page_id=100,
        )
        assert op.page_id == 100
