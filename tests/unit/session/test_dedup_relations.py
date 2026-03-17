# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: Apache-2.0
"""Integration test: dedup MERGE -> supersedes relation -> retrieval prefers newer memory."""

from unittest.mock import MagicMock

import pytest

from openviking.core.context import Context
from openviking.session.memory_deduplicator import (
    DedupDecision,
    DedupResult,
    ExistingMemoryAction,
    MemoryActionDecision,
    MemoryDeduplicator,
)
from openviking.storage.memory_relation_store import (
    MemoryRelationStore,
    RelationType,
)


@pytest.fixture
def relation_store():
    return MemoryRelationStore()


@pytest.fixture
def old_memory():
    ctx = Context(uri="viking://user/u1/memories/preferences/theme_dark")
    ctx.abstract = "User prefers dark mode"
    ctx.meta = {"_dedup_score": 0.95}
    return ctx


@pytest.fixture
def candidate_memory():
    """Mock CandidateMemory with the fields the deduplicator needs."""
    m = MagicMock()
    m.uri = ""
    m.category.value = "preferences"
    m.abstract = "User prefers light mode"
    m.content = "The user switched to light mode"
    m.overview = ""
    return m


class TestDedupRelationRecording:
    @pytest.mark.asyncio
    async def test_merge_creates_supersedes_relation(
        self, relation_store, old_memory, candidate_memory
    ):
        """When dedup decides MERGE, a supersedes relation should be created."""
        result = DedupResult(
            decision=DedupDecision.NONE,
            candidate=candidate_memory,
            similar_memories=[old_memory],
            actions=[
                ExistingMemoryAction(
                    memory=old_memory,
                    decision=MemoryActionDecision.MERGE,
                    reason="preference update",
                )
            ],
        )

        dedup = MemoryDeduplicator.__new__(MemoryDeduplicator)
        dedup.relation_store = relation_store

        await dedup._record_dedup_relations(result)

        assert relation_store.count() == 1
        rels = await relation_store.query(old_memory.uri, direction="incoming")
        assert len(rels) == 1
        assert rels[0].relation_type == RelationType.SUPERSEDES
        assert rels[0].target_uri == old_memory.uri

    @pytest.mark.asyncio
    async def test_delete_creates_contradicts_relation(
        self, relation_store, old_memory, candidate_memory
    ):
        """When dedup decides DELETE, a contradicts relation should be created."""
        result = DedupResult(
            decision=DedupDecision.CREATE,
            candidate=candidate_memory,
            similar_memories=[old_memory],
            actions=[
                ExistingMemoryAction(
                    memory=old_memory,
                    decision=MemoryActionDecision.DELETE,
                    reason="contradiction detected",
                )
            ],
        )

        dedup = MemoryDeduplicator.__new__(MemoryDeduplicator)
        dedup.relation_store = relation_store

        await dedup._record_dedup_relations(result)

        assert relation_store.count() == 1
        rels = await relation_store.query(old_memory.uri, direction="incoming")
        assert len(rels) == 1
        assert rels[0].relation_type == RelationType.CONTRADICTS

    @pytest.mark.asyncio
    async def test_no_store_no_crash(self, old_memory, candidate_memory):
        """Without a relation store, recording should be a no-op."""
        result = DedupResult(
            decision=DedupDecision.NONE,
            candidate=candidate_memory,
            similar_memories=[old_memory],
            actions=[
                ExistingMemoryAction(
                    memory=old_memory,
                    decision=MemoryActionDecision.MERGE,
                    reason="test",
                )
            ],
        )

        dedup = MemoryDeduplicator.__new__(MemoryDeduplicator)
        dedup.relation_store = None

        # Should not raise
        await dedup._record_dedup_relations(result)

    @pytest.mark.asyncio
    async def test_skip_creates_no_relations(self, relation_store, old_memory, candidate_memory):
        """SKIP decisions should not create any relations."""
        result = DedupResult(
            decision=DedupDecision.SKIP,
            candidate=candidate_memory,
            similar_memories=[old_memory],
            actions=None,
        )

        dedup = MemoryDeduplicator.__new__(MemoryDeduplicator)
        dedup.relation_store = relation_store

        await dedup._record_dedup_relations(result)

        assert relation_store.count() == 0

    @pytest.mark.asyncio
    async def test_superseded_memory_detectable(self, relation_store, old_memory, candidate_memory):
        """After MERGE, the old memory should be detected as superseded."""
        result = DedupResult(
            decision=DedupDecision.NONE,
            candidate=candidate_memory,
            similar_memories=[old_memory],
            actions=[
                ExistingMemoryAction(
                    memory=old_memory,
                    decision=MemoryActionDecision.MERGE,
                    reason="preference update",
                )
            ],
        )

        dedup = MemoryDeduplicator.__new__(MemoryDeduplicator)
        dedup.relation_store = relation_store

        await dedup._record_dedup_relations(result)

        # The old memory should now be flagged as superseded
        assert await relation_store.is_superseded(old_memory.uri) is True
