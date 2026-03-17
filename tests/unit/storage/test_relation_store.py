# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: Apache-2.0

import pytest

from openviking.storage.memory_relation_store import (
    MemoryRelation,
    MemoryRelationStore,
    RelationType,
)


@pytest.fixture
def store():
    return MemoryRelationStore()


@pytest.fixture
def sample_relation():
    return MemoryRelation(
        source_uri="viking://user/u1/memories/preferences/theme_light",
        target_uri="viking://user/u1/memories/preferences/theme_dark",
        relation_type=RelationType.SUPERSEDES,
        metadata={"reason": "user changed preference"},
    )


class TestMemoryRelationStore:
    @pytest.mark.asyncio
    async def test_create_returns_id(self, store, sample_relation):
        rel_id = await store.create(sample_relation)
        assert rel_id == sample_relation.id
        assert store.count() == 1

    @pytest.mark.asyncio
    async def test_create_deduplicates(self, store, sample_relation):
        await store.create(sample_relation)
        dup = MemoryRelation(
            source_uri=sample_relation.source_uri,
            target_uri=sample_relation.target_uri,
            relation_type=sample_relation.relation_type,
        )
        returned_id = await store.create(dup)
        assert returned_id == sample_relation.id
        assert store.count() == 1

    @pytest.mark.asyncio
    async def test_query_outgoing(self, store, sample_relation):
        await store.create(sample_relation)
        results = await store.query(
            sample_relation.source_uri, direction="outgoing"
        )
        assert len(results) == 1
        assert results[0].target_uri == sample_relation.target_uri

    @pytest.mark.asyncio
    async def test_query_incoming(self, store, sample_relation):
        await store.create(sample_relation)
        results = await store.query(
            sample_relation.target_uri, direction="incoming"
        )
        assert len(results) == 1
        assert results[0].source_uri == sample_relation.source_uri

    @pytest.mark.asyncio
    async def test_query_both(self, store, sample_relation):
        await store.create(sample_relation)
        # Query by source
        results_src = await store.query(
            sample_relation.source_uri, direction="both"
        )
        assert len(results_src) == 1
        # Query by target
        results_tgt = await store.query(
            sample_relation.target_uri, direction="both"
        )
        assert len(results_tgt) == 1

    @pytest.mark.asyncio
    async def test_query_by_type(self, store, sample_relation):
        await store.create(sample_relation)
        # Add a different type
        related = MemoryRelation(
            source_uri=sample_relation.source_uri,
            target_uri="viking://user/u1/memories/preferences/font_size",
            relation_type=RelationType.RELATED_TO,
        )
        await store.create(related)
        assert store.count() == 2

        supersedes = await store.query(
            sample_relation.source_uri,
            relation_type=RelationType.SUPERSEDES,
            direction="outgoing",
        )
        assert len(supersedes) == 1
        assert supersedes[0].relation_type == RelationType.SUPERSEDES

    @pytest.mark.asyncio
    async def test_query_no_results(self, store):
        results = await store.query("viking://nonexistent")
        assert results == []

    @pytest.mark.asyncio
    async def test_delete_by_id(self, store, sample_relation):
        await store.create(sample_relation)
        assert store.count() == 1
        deleted = await store.delete(sample_relation.id)
        assert deleted is True
        assert store.count() == 0

    @pytest.mark.asyncio
    async def test_delete_nonexistent(self, store):
        deleted = await store.delete("nonexistent-id")
        assert deleted is False

    @pytest.mark.asyncio
    async def test_delete_by_uri(self, store, sample_relation):
        await store.create(sample_relation)
        # Add another relation referencing the same URI as target
        r2 = MemoryRelation(
            source_uri="viking://user/u1/memories/preferences/other",
            target_uri=sample_relation.target_uri,
            relation_type=RelationType.CONTRADICTS,
        )
        await store.create(r2)
        assert store.count() == 2

        deleted = await store.delete_by_uri(sample_relation.target_uri)
        assert deleted == 2
        assert store.count() == 0

    @pytest.mark.asyncio
    async def test_is_superseded(self, store, sample_relation):
        await store.create(sample_relation)
        assert await store.is_superseded(sample_relation.target_uri) is True
        assert await store.is_superseded(sample_relation.source_uri) is False
        assert await store.is_superseded("viking://nonexistent") is False

    @pytest.mark.asyncio
    async def test_get_superseded_uris(self, store, sample_relation):
        await store.create(sample_relation)
        superseded = await store.get_superseded_uris(sample_relation.source_uri)
        assert superseded == [sample_relation.target_uri]

    @pytest.mark.asyncio
    async def test_from_dict_round_trip(self, sample_relation):
        d = sample_relation.to_dict()
        restored = MemoryRelation.from_dict(d)
        assert restored.source_uri == sample_relation.source_uri
        assert restored.target_uri == sample_relation.target_uri
        assert restored.relation_type == sample_relation.relation_type
        assert restored.id == sample_relation.id

    @pytest.mark.asyncio
    async def test_all_relation_types(self, store):
        for rtype in RelationType:
            r = MemoryRelation(
                source_uri=f"viking://src/{rtype.value}",
                target_uri=f"viking://tgt/{rtype.value}",
                relation_type=rtype,
            )
            await store.create(r)
        assert store.count() == 4
