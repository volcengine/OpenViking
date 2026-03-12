# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: Apache-2.0

from unittest.mock import AsyncMock, MagicMock

import pytest

from openviking.session.memory_deduplicator import MemoryDeduplicator
from openviking.session.memory_extractor import CandidateMemory, MemoryCategory
from openviking_cli.session.user_id import UserIdentifier


class _DummyEmbedResult:
    def __init__(self, dense_vector):
        self.dense_vector = dense_vector


class _DummyEmbedder:
    def embed(self, _text):
        return _DummyEmbedResult([0.1, 0.2, 0.3])


def _make_user() -> UserIdentifier:
    return UserIdentifier("acc1", "test_user", "test_agent")


def _make_candidate(category: MemoryCategory = MemoryCategory.PREFERENCES) -> CandidateMemory:
    return CandidateMemory(
        category=category,
        abstract="User prefers concise summaries",
        overview="User asks for concise answers frequently.",
        content="The user prefers concise summaries over long explanations.",
        source_session="session_test",
        user=_make_user(),
        language="en",
    )


def _make_existing_user_memory(uri_suffix: str = "existing.md") -> dict:
    user_space = _make_user().user_space_name()
    return {
        "id": f"uri_{uri_suffix}",
        "uri": f"viking://user/{user_space}/memories/preferences/{uri_suffix}",
        "context_type": "memory",
        "level": 2,
        "account_id": "acc1",
        "owner_space": user_space,
        "abstract": "Existing preference memory",
        "category": "preferences",
        "_score": 0.85,
    }


def _make_existing_agent_memory(uri_suffix: str = "case1.md") -> dict:
    user = _make_user()
    agent_space = user.agent_space_name()
    return {
        "id": f"uri_{uri_suffix}",
        "uri": f"viking://agent/{agent_space}/memories/cases/{uri_suffix}",
        "context_type": "memory",
        "level": 2,
        "account_id": "acc1",
        "owner_space": agent_space,
        "abstract": "Existing case memory",
        "category": "cases",
        "_score": 0.90,
    }


@pytest.mark.asyncio
class TestFindSimilarMemoriesURIConversion:
    async def test_user_uri_converted_to_temp_uri(self):
        vikingdb = MagicMock()
        vikingdb.get_embedder.return_value = _DummyEmbedder()
        vikingdb.search_similar_memories = AsyncMock(
            return_value=[_make_existing_user_memory("pref1.md")]
        )

        dedup = MemoryDeduplicator(vikingdb=vikingdb)
        candidate = _make_candidate()

        user_temp_uri = "viking://user/temp_user_123"
        similar = await dedup._find_similar_memories(
            candidate,
            user_temp_uri=user_temp_uri,
            agent_temp_uri=None,
        )

        assert len(similar) == 1
        user_space = _make_user().user_space_name()
        original_uri = f"viking://user/{user_space}/memories/preferences/pref1.md"
        expected_uri = f"{user_temp_uri}/memories/preferences/pref1.md"
        assert similar[0].uri == expected_uri
        assert similar[0].uri != original_uri

    async def test_agent_uri_converted_to_temp_uri(self):
        vikingdb = MagicMock()
        vikingdb.get_embedder.return_value = _DummyEmbedder()
        vikingdb.search_similar_memories = AsyncMock(
            return_value=[_make_existing_agent_memory("case1.md")]
        )

        dedup = MemoryDeduplicator(vikingdb=vikingdb)
        candidate = _make_candidate(category=MemoryCategory.CASES)

        agent_temp_uri = "viking://agent/temp_agent_456"
        similar = await dedup._find_similar_memories(
            candidate,
            user_temp_uri=None,
            agent_temp_uri=agent_temp_uri,
        )

        assert len(similar) == 1
        user = _make_user()
        agent_space = user.agent_space_name()
        original_uri = f"viking://agent/{agent_space}/memories/cases/case1.md"
        expected_uri = f"{agent_temp_uri}/memories/cases/case1.md"
        assert similar[0].uri == expected_uri
        assert similar[0].uri != original_uri

    async def test_no_conversion_when_no_temp_uri(self):
        vikingdb = MagicMock()
        vikingdb.get_embedder.return_value = _DummyEmbedder()
        vikingdb.search_similar_memories = AsyncMock(
            return_value=[_make_existing_user_memory("pref1.md")]
        )

        dedup = MemoryDeduplicator(vikingdb=vikingdb)
        candidate = _make_candidate()

        similar = await dedup._find_similar_memories(
            candidate,
            user_temp_uri=None,
            agent_temp_uri=None,
        )

        assert len(similar) == 1
        user_space = _make_user().user_space_name()
        expected_uri = f"viking://user/{user_space}/memories/preferences/pref1.md"
        assert similar[0].uri == expected_uri

    async def test_mixed_uris_only_convert_matching_type(self):
        user_space = _make_user().user_space_name()
        agent_space = _make_user().agent_space_name()

        vikingdb = MagicMock()
        vikingdb.get_embedder.return_value = _DummyEmbedder()
        vikingdb.search_similar_memories = AsyncMock(
            return_value=[
                _make_existing_user_memory("pref1.md"),
                _make_existing_agent_memory("case1.md"),
            ]
        )

        dedup = MemoryDeduplicator(vikingdb=vikingdb)
        candidate = _make_candidate()

        user_temp_uri = "viking://user/temp_user_123"
        similar = await dedup._find_similar_memories(
            candidate,
            user_temp_uri=user_temp_uri,
            agent_temp_uri=None,
        )

        assert len(similar) == 2
        uris = {m.uri for m in similar}
        assert f"{user_temp_uri}/memories/preferences/pref1.md" in uris
        assert f"viking://agent/{agent_space}/memories/cases/case1.md" in uris

    async def test_uri_conversion_preserves_meta_and_score(self):
        vikingdb = MagicMock()
        vikingdb.get_embedder.return_value = _DummyEmbedder()
        vikingdb.search_similar_memories = AsyncMock(
            return_value=[_make_existing_user_memory("pref1.md")]
        )

        dedup = MemoryDeduplicator(vikingdb=vikingdb)
        candidate = _make_candidate()

        user_temp_uri = "viking://user/temp_user_123"
        similar = await dedup._find_similar_memories(
            candidate,
            user_temp_uri=user_temp_uri,
            agent_temp_uri=None,
        )

        assert len(similar) == 1
        assert similar[0].meta is not None
        assert similar[0].meta.get("_dedup_score") == 0.85


class TestExtractFacetKey:
    def test_extract_with_chinese_colon(self):
        result = MemoryDeduplicator._extract_facet_key("饮食偏好：喜欢吃苹果和草莓")
        assert result == "饮食偏好"

    def test_extract_with_english_colon(self):
        result = MemoryDeduplicator._extract_facet_key("User preference: dark mode enabled")
        assert result == "user preference"

    def test_extract_with_hyphen(self):
        result = MemoryDeduplicator._extract_facet_key("Coding style - prefer type hints")
        assert result == "coding style"

    def test_extract_with_em_dash(self):
        result = MemoryDeduplicator._extract_facet_key("Work schedule — remote on Fridays")
        assert result == "work schedule"

    def test_extract_with_no_separator_returns_prefix(self):
        result = MemoryDeduplicator._extract_facet_key(
            "This is a long abstract without any separator"
        )
        assert len(result) <= 24
        assert result == "this is a long abstract"

    def test_extract_with_empty_string(self):
        result = MemoryDeduplicator._extract_facet_key("")
        assert result == ""

    def test_extract_with_none(self):
        result = MemoryDeduplicator._extract_facet_key(None)
        assert result == ""

    def test_extract_normalizes_whitespace(self):
        result = MemoryDeduplicator._extract_facet_key("  Multiple   spaces  :  value  ")
        assert result == "multiple spaces"

    def test_extract_with_short_text_no_separator(self):
        result = MemoryDeduplicator._extract_facet_key("Short")
        assert result == "short"

    def test_extract_returns_lowercase(self):
        result = MemoryDeduplicator._extract_facet_key("FOOD PREFERENCE: pizza")
        assert result == "food preference"

    def test_extract_with_separator_at_start(self):
        result = MemoryDeduplicator._extract_facet_key(": starts with separator")
        assert result == ": starts with"

    def test_extract_with_multiple_separators_uses_first(self):
        result = MemoryDeduplicator._extract_facet_key("Topic: Subtopic - Detail")
        assert result == "topic"


class TestCosineSimilarity:
    def test_identical_vectors(self):
        vec = [1.0, 2.0, 3.0]
        result = MemoryDeduplicator._cosine_similarity(vec, vec)
        assert abs(result - 1.0) < 1e-9

    def test_orthogonal_vectors(self):
        vec_a = [1.0, 0.0]
        vec_b = [0.0, 1.0]
        result = MemoryDeduplicator._cosine_similarity(vec_a, vec_b)
        assert abs(result) < 1e-9

    def test_opposite_vectors(self):
        vec_a = [1.0, 2.0, 3.0]
        vec_b = [-1.0, -2.0, -3.0]
        result = MemoryDeduplicator._cosine_similarity(vec_a, vec_b)
        assert abs(result + 1.0) < 1e-9

    def test_different_length_vectors(self):
        vec_a = [1.0, 2.0, 3.0]
        vec_b = [1.0, 2.0]
        result = MemoryDeduplicator._cosine_similarity(vec_a, vec_b)
        assert result == 0.0

    def test_zero_vector_a(self):
        vec_a = [0.0, 0.0, 0.0]
        vec_b = [1.0, 2.0, 3.0]
        result = MemoryDeduplicator._cosine_similarity(vec_a, vec_b)
        assert result == 0.0

    def test_zero_vector_b(self):
        vec_a = [1.0, 2.0, 3.0]
        vec_b = [0.0, 0.0, 0.0]
        result = MemoryDeduplicator._cosine_similarity(vec_a, vec_b)
        assert result == 0.0

    def test_both_zero_vectors(self):
        vec_a = [0.0, 0.0, 0.0]
        vec_b = [0.0, 0.0, 0.0]
        result = MemoryDeduplicator._cosine_similarity(vec_a, vec_b)
        assert result == 0.0

    def test_partial_similarity(self):
        vec_a = [1.0, 0.0, 0.0]
        vec_b = [1.0, 1.0, 0.0]
        result = MemoryDeduplicator._cosine_similarity(vec_a, vec_b)
        expected = 1.0 / (2.0**0.5)
        assert abs(result - expected) < 1e-9

    def test_negative_values(self):
        vec_a = [1.0, -2.0, 3.0]
        vec_b = [-1.0, 2.0, 3.0]
        result = MemoryDeduplicator._cosine_similarity(vec_a, vec_b)
        assert 0 < result < 1

    def test_single_element_vectors(self):
        vec_a = [5.0]
        vec_b = [3.0]
        result = MemoryDeduplicator._cosine_similarity(vec_a, vec_b)
        assert abs(result - 1.0) < 1e-9

    def test_large_vectors(self):
        vec_a = [float(i) for i in range(100)]
        vec_b = [float(i * 2) for i in range(100)]
        result = MemoryDeduplicator._cosine_similarity(vec_a, vec_b)
        assert abs(result - 1.0) < 1e-6
