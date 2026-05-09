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
    def test_all_types_defined(self):
        assert LinkType.RELATED_TO == "related_to"
        assert LinkType.BELONGS_TO == "belongs_to"
        assert LinkType.CAUSED_BY == "caused_by"
        assert LinkType.DERIVED_FROM == "derived_from"
        assert LinkType.CONTRADICTS == "contradicts"
        assert LinkType.EVOLVED_FROM == "evolved_from"


class TestWikiLink:
    def test_minimal_fields(self):
        link = WikiLink(f=1, t=2)
        assert link.f == 1
        assert link.t == 2
        assert link.link_type == LinkType.RELATED_TO
        assert link.weight == 1.0

    def test_full_fields(self):
        link = WikiLink(
            f=1,
            t=3,
            link_type=LinkType.BELONGS_TO,
            weight=0.9,
            match_text="Caroline",
            description="Preference belongs to Caroline",
        )
        assert link.match_text == "Caroline"
        assert link.weight == 0.9


class TestStoredLink:
    def test_forward_link(self):
        link = StoredLink(
            from_uri="viking://a",
            to_uri="viking://b",
            direction="links",
            link_type=LinkType.BELONGS_TO,
            weight=0.9,
            created_at="2026-05-09T10:00:00+00:00",
        )
        assert link.direction == "links"

    def test_backward_link(self):
        link = StoredLink(
            from_uri="viking://a",
            to_uri="viking://b",
            direction="backlinks",
            link_type=LinkType.BELONGS_TO,
            weight=0.9,
            created_at="2026-05-09T10:00:00+00:00",
        )
        assert link.direction == "backlinks"

    def test_model_dump(self):
        link = StoredLink(
            from_uri="viking://a",
            to_uri="viking://b",
            direction="links",
            link_type=LinkType.RELATED_TO,
            created_at="2026-05-09T10:00:00+00:00",
        )
        d = link.model_dump()
        assert d["from_uri"] == "viking://a"
        assert d["direction"] == "links"
        assert d["link_type"] == LinkType.RELATED_TO


class TestMemoryTypeSchemaLinkEnabled:
    def test_default_link_enabled(self):
        schema = MemoryTypeSchema(memory_type="test")
        assert schema.link_enabled is True

    def test_link_enabled_false(self):
        schema = MemoryTypeSchema(memory_type="test", link_enabled=False)
        assert schema.link_enabled is False


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
            direction="links",
            link_type=LinkType.RELATED_TO,
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
