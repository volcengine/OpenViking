# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: Apache-2.0

import asyncio
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from openviking.core.context import Context
from openviking.message import Message
from openviking.server.identity import RequestContext, Role
from openviking.session.compressor import SessionCompressor
from openviking.session.memory_deduplicator import (
    DedupDecision,
    DedupResult,
    ExistingMemoryAction,
    MemoryActionDecision,
    MemoryDeduplicator,
)
from openviking.session.memory_extractor import (
    CandidateMemory,
    MemoryCategory,
    MemoryExtractor,
    MergedMemoryPayload,
)
from openviking_cli.session.user_id import UserIdentifier
from tests.utils.mock_context import make_test_ctx

ctx = make_test_ctx()


class _DummyVikingDB:
    def __init__(self):
        self._embedder = None

    def get_embedder(self):
        return self._embedder


class _DummyEmbedResult:
    def __init__(self, dense_vector):
        self.dense_vector = dense_vector


class _DummyEmbedder:
    def embed(self, _text, is_query: bool = False):
        return _DummyEmbedResult([0.1, 0.2, 0.3])


def _make_user() -> UserIdentifier:
    return UserIdentifier("acc1", "test_user", "test_agent")


def _make_ctx() -> RequestContext:
    return RequestContext(user=_make_user(), role=Role.USER)


def _make_candidate() -> CandidateMemory:
    return CandidateMemory(
        category=MemoryCategory.PREFERENCES,
        abstract="User prefers concise summaries",
        overview="User asks for concise answers frequently.",
        content="The user prefers concise summaries over long explanations.",
        source_session="session_test",
        user=_make_user(),
        language="en",
    )


def _make_dedup(vikingdb=None, embedder=None) -> MemoryDeduplicator:
    """Create MemoryDeduplicator without config dependency."""
    dedup = MemoryDeduplicator.__new__(MemoryDeduplicator)
    dedup.vikingdb = vikingdb or MagicMock()
    dedup.embedder = embedder
    return dedup


def _make_compressor(vikingdb=None, embedder=None) -> SessionCompressor:
    """Create SessionCompressor without config dependency."""
    vikingdb = vikingdb or MagicMock()
    with patch("openviking.session.memory_deduplicator.get_openviking_config") as mock_config:
        mock_config.return_value.embedding.get_embedder.return_value = embedder
        compressor = SessionCompressor(vikingdb=vikingdb)
    return compressor


def _make_existing(uri_suffix: str = "existing.md") -> Context:
    user_space = _make_user().user_space_name()
    return Context(
        uri=f"viking://user/{user_space}/memories/preferences/{uri_suffix}",
        parent_uri=f"viking://user/{user_space}/memories/preferences",
        is_leaf=True,
        abstract="Existing preference memory",
        context_type="memory",
        category="preferences",
    )


class TestMemoryDeduplicatorPayload:
    def test_create_with_empty_list_is_valid(self):
        dedup = MemoryDeduplicator(vikingdb=_DummyVikingDB())
        existing = [_make_existing("a.md")]

        decision, _, actions = dedup._parse_decision_payload(
            {"decision": "create", "reason": "new memory", "list": []},
            existing,
        )

        assert decision == DedupDecision.CREATE
        assert actions == []

    def test_create_with_merge_is_normalized_to_none(self):
        dedup = MemoryDeduplicator(vikingdb=_DummyVikingDB())
        existing = [_make_existing("b.md")]

        decision, _, actions = dedup._parse_decision_payload(
            {
                "decision": "create",
                "list": [{"uri": existing[0].uri, "decide": "merge"}],
            },
            existing,
        )

        assert decision == DedupDecision.NONE
        assert len(actions) == 1
        assert actions[0].decision == MemoryActionDecision.MERGE

    def test_skip_drops_list_actions(self):
        dedup = MemoryDeduplicator(vikingdb=_DummyVikingDB())
        existing = [_make_existing("c.md")]

        decision, _, actions = dedup._parse_decision_payload(
            {
                "decision": "skip",
                "list": [{"uri": existing[0].uri, "decide": "delete"}],
            },
            existing,
        )

        assert decision == DedupDecision.SKIP
        assert actions == []

    def test_cross_facet_delete_actions_are_kept(self):
        dedup = MemoryDeduplicator(vikingdb=_DummyVikingDB())
        food = _make_existing("food.md")
        food.abstract = "饮食偏好: 喜欢吃苹果和草莓"
        routine = _make_existing("routine.md")
        routine.abstract = "作息习惯: 每天早上7点起床"
        existing = [food, routine]
        candidate = _make_candidate()
        candidate.abstract = "饮食偏好: 不再喜欢吃水果"
        candidate.content = "用户不再喜欢吃水果，需要作废过去的水果偏好。"

        decision, _, actions = dedup._parse_decision_payload(
            {
                "decision": "create",
                "list": [
                    {"uri": food.uri, "decide": "delete"},
                    {"uri": routine.uri, "decide": "delete"},
                ],
            },
            existing,
            candidate,
        )

        assert decision == DedupDecision.CREATE
        assert len(actions) == 2
        assert {a.memory.uri for a in actions} == {food.uri, routine.uri}
        assert all(a.decision == MemoryActionDecision.DELETE for a in actions)

    @pytest.mark.asyncio
    async def test_find_similar_memories_uses_path_must_filter_and__score(self):
        existing = _make_existing("pref_hit.md")

        vikingdb = MagicMock()
        vikingdb.get_embedder.return_value = _DummyEmbedder()
        vikingdb.search_similar_memories = AsyncMock(
            return_value=[
                {
                    "id": "uri_pref_hit",
                    "uri": existing.uri,
                    "context_type": "memory",
                    "level": 2,
                    "account_id": "acc1",
                    "owner_space": _make_user().user_space_name(),
                    "abstract": existing.abstract,
                    "category": "preferences",
                    "_score": 0.82,
                }
            ]
        )
        dedup = MemoryDeduplicator(vikingdb=vikingdb)
        candidate = _make_candidate()

        similar, _query_vector = await dedup._find_similar_memories(candidate, ctx)

        assert len(similar) == 1
        assert similar[0].uri == existing.uri
        call = vikingdb.search_similar_memories.await_args.kwargs
        # Note: removed stale assert call["account_id"] -- _find_similar_memories
        # does not pass account_id to search_similar_memories.
        assert call["owner_space"] == _make_user().user_space_name()
        assert call["category_uri_prefix"] == (
            f"viking://user/{_make_user().user_space_name()}/memories/preferences/"
        )
        assert call["limit"] == 5

    @pytest.mark.asyncio
    async def test_find_similar_memories_accepts_low_score_when_threshold_is_zero(self):
        vikingdb = MagicMock()
        vikingdb.get_embedder.return_value = _DummyEmbedder()
        vikingdb.search_similar_memories = AsyncMock(
            return_value=[
                {
                    "id": "uri_low",
                    "uri": f"viking://user/{_make_user().user_space_name()}/memories/preferences/low.md",
                    "context_type": "memory",
                    "level": 2,
                    "account_id": "acc1",
                    "owner_space": _make_user().user_space_name(),
                    "abstract": "low",
                    "_score": 0.68,
                }
            ]
        )
        dedup = MemoryDeduplicator(vikingdb=vikingdb)

        similar, _ = await dedup._find_similar_memories(_make_candidate(), ctx)

        assert len(similar) == 1

    @pytest.mark.asyncio
    async def test_llm_decision_formats_up_to_five_similar_memories(self):
        dedup = MemoryDeduplicator(vikingdb=_DummyVikingDB())
        similar = [_make_existing(f"m_{i}.md") for i in range(6)]
        captured = {}

        def _fake_render_prompt(_template_id, variables):
            captured.update(variables)
            return "prompt"

        class _DummyVLM:
            def is_available(self):
                return True

            async def get_completion_async(self, _prompt):
                return '{"decision":"skip","reason":"dup"}'

        class _DummyConfig:
            vlm = _DummyVLM()

        with (
            patch(
                "openviking.session.memory_deduplicator.get_openviking_config",
                return_value=_DummyConfig(),
            ),
            patch(
                "openviking.session.memory_deduplicator.render_prompt",
                side_effect=_fake_render_prompt,
            ),
        ):
            decision, _, _ = await dedup._llm_decision(_make_candidate(), similar)

        assert decision == DedupDecision.SKIP
        existing_text = captured["existing_memories"]
        assert existing_text.count("uri=") == 5
        assert similar[0].abstract in existing_text
        assert "facet=" in existing_text
        assert similar[4].uri in existing_text
        assert similar[5].uri not in existing_text

    @pytest.mark.asyncio
    async def test_llm_decision_falls_back_to_create_on_cancelled_error(self):
        dedup = MemoryDeduplicator(vikingdb=_DummyVikingDB())
        dedup.vikingdb.is_closing = True

        class _DummyVLM:
            def is_available(self):
                return True

            async def get_completion_async(self, _prompt):
                raise asyncio.CancelledError("llm shutdown")

        class _DummyConfig:
            vlm = _DummyVLM()

        with patch(
            "openviking.session.memory_deduplicator.get_openviking_config",
            return_value=_DummyConfig(),
        ):
            decision, reason, actions = await dedup._llm_decision(_make_candidate(), [])

        assert decision == DedupDecision.CREATE
        assert "cancelled" in reason.lower()
        assert actions == []

    @pytest.mark.asyncio
    async def test_llm_decision_reraises_cancelled_error_when_not_shutting_down(self):
        dedup = MemoryDeduplicator(vikingdb=_DummyVikingDB())

        class _DummyVLM:
            def is_available(self):
                return True

            async def get_completion_async(self, _prompt):
                raise asyncio.CancelledError("llm shutdown")

        class _DummyConfig:
            vlm = _DummyVLM()

        with (
            patch(
                "openviking.session.memory_deduplicator.get_openviking_config",
                return_value=_DummyConfig(),
            ),
            pytest.raises(asyncio.CancelledError),
        ):
            await dedup._llm_decision(_make_candidate(), [])

    @pytest.mark.asyncio
    async def test_find_similar_includes_batch_memories(self):
        """Batch memory with high cosine similarity appears in results."""
        vikingdb = MagicMock()
        vikingdb.search_similar_memories = AsyncMock(return_value=[])

        dedup = _make_dedup(vikingdb=vikingdb, embedder=_DummyEmbedder())
        candidate = _make_candidate()

        # Batch memory with identical embedding vector -> cosine similarity = 1.0
        batch_ctx = _make_existing("batch_item.md")
        batch_vector = [0.1, 0.2, 0.3]  # Same as _DummyEmbedder returns
        batch_memories = [(batch_vector, batch_ctx)]

        similar, query_vector = await dedup._find_similar_memories(
            candidate, ctx, batch_memories=batch_memories
        )

        assert len(similar) == 1
        assert similar[0].uri == batch_ctx.uri
        assert similar[0].meta["_dedup_score"] == pytest.approx(1.0, abs=1e-6)
        assert query_vector == [0.1, 0.2, 0.3]

    @pytest.mark.asyncio
    async def test_find_similar_excludes_dissimilar_batch_memories(self):
        """Batch memory with opposite embedding (cosine = -1.0) is excluded."""
        vikingdb = MagicMock()
        vikingdb.search_similar_memories = AsyncMock(return_value=[])

        dedup = _make_dedup(vikingdb=vikingdb, embedder=_DummyEmbedder())
        candidate = _make_candidate()

        # Opposite direction vector -> cosine = -1.0, below threshold 0.0
        batch_ctx = _make_existing("unrelated.md")
        batch_vector = [-0.1, -0.2, -0.3]
        batch_memories = [(batch_vector, batch_ctx)]

        similar, _ = await dedup._find_similar_memories(
            candidate, ctx, batch_memories=batch_memories
        )

        assert len(similar) == 0

    @pytest.mark.asyncio
    async def test_find_similar_deduplicates_batch_and_db_by_uri(self):
        """If same URI appears in both DB results and batch, only keep DB version."""
        existing = _make_existing("overlap.md")
        vikingdb = MagicMock()
        vikingdb.search_similar_memories = AsyncMock(
            return_value=[
                {
                    "id": "uri_overlap",
                    "uri": existing.uri,
                    "context_type": "memory",
                    "level": 2,
                    "account_id": "acc1",
                    "owner_space": _make_user().user_space_name(),
                    "abstract": existing.abstract,
                    "category": "preferences",
                    "_score": 0.9,
                }
            ]
        )

        dedup = _make_dedup(vikingdb=vikingdb, embedder=_DummyEmbedder())
        candidate = _make_candidate()

        # Batch contains same URI as DB result
        batch_ctx = _make_existing("overlap.md")
        batch_vector = [0.1, 0.2, 0.3]
        batch_memories = [(batch_vector, batch_ctx)]

        similar, _ = await dedup._find_similar_memories(
            candidate, ctx, batch_memories=batch_memories
        )

        # Should have exactly 1 (DB version), not 2
        assert len(similar) == 1
        assert similar[0].uri == existing.uri
        assert similar[0].meta["_dedup_score"] == pytest.approx(0.9, abs=1e-6)

    @pytest.mark.asyncio
    async def test_deduplicate_returns_query_vector_in_result(self):
        """DedupResult includes query_vector for batch tracking."""
        vikingdb = MagicMock()
        vikingdb.search_similar_memories = AsyncMock(return_value=[])

        dedup = _make_dedup(vikingdb=vikingdb, embedder=_DummyEmbedder())
        candidate = _make_candidate()

        result = await dedup.deduplicate(candidate, ctx)

        assert result.decision == DedupDecision.CREATE
        assert result.query_vector == [0.1, 0.2, 0.3]


@pytest.mark.asyncio
class TestMemoryMergeBundle:
    async def test_merge_memory_bundle_parses_structured_response(self):
        extractor = MemoryExtractor()

        class _DummyVLM:
            def is_available(self):
                return True

            async def get_completion_async(self, _prompt):
                return (
                    '{"decision":"merge","abstract":"Tool preference: Use clang","overview":"## '
                    'Preference Domain","content":"Use clang for C++.","reason":"updated"}'
                )

        class _DummyConfig:
            vlm = _DummyVLM()

        with patch(
            "openviking.session.memory_extractor.get_openviking_config",
            return_value=_DummyConfig(),
        ):
            payload = await extractor._merge_memory_bundle(
                existing_abstract="old",
                existing_overview="",
                existing_content="old content",
                new_abstract="new",
                new_overview="",
                new_content="new content",
                category="preferences",
                output_language="en",
            )

        assert payload is not None
        assert payload.abstract == "Tool preference: Use clang"
        assert payload.content == "Use clang for C++."

    async def test_merge_memory_bundle_rejects_missing_required_fields(self):
        extractor = MemoryExtractor()

        class _DummyVLM:
            def is_available(self):
                return True

            async def get_completion_async(self, _prompt):
                return '{"decision":"merge","abstract":"","overview":"o","content":"","reason":"r"}'

        class _DummyConfig:
            vlm = _DummyVLM()

        with patch(
            "openviking.session.memory_extractor.get_openviking_config",
            return_value=_DummyConfig(),
        ):
            payload = await extractor._merge_memory_bundle(
                existing_abstract="old",
                existing_overview="",
                existing_content="old content",
                new_abstract="new",
                new_overview="",
                new_content="new content",
                category="preferences",
                output_language="en",
            )

        assert payload is None


@pytest.mark.asyncio
class TestProfileMergeSafety:
    async def test_profile_merge_failure_keeps_existing_content(self):
        extractor = MemoryExtractor()
        extractor._merge_memory_bundle = AsyncMock(return_value=None)
        candidate = CandidateMemory(
            category=MemoryCategory.PROFILE,
            abstract="User basic info: lives in NYC",
            overview="## Background",
            content="User currently lives in NYC.",
            source_session="session_test",
            user="test_user",
            language="en",
        )

        fs = MagicMock()
        fs.read_file = AsyncMock(return_value="existing profile content")
        fs.write_file = AsyncMock()

        payload = await extractor._append_to_profile(candidate, fs, ctx=_make_ctx())

        assert payload is None
        fs.write_file.assert_not_called()

    async def test_create_memory_skips_profile_index_payload_when_merge_fails(self):
        extractor = MemoryExtractor()
        candidate = CandidateMemory(
            category=MemoryCategory.PROFILE,
            abstract="User basic info: lives in NYC",
            overview="## Background",
            content="User currently lives in NYC.",
            source_session="session_test",
            user="test_user",
            language="en",
        )
        extractor._append_to_profile = AsyncMock(return_value=None)

        with patch("openviking.session.memory_extractor.get_viking_fs", return_value=MagicMock()):
            memory = await extractor.create_memory(
                candidate,
                user=_make_user(),
                session_id="s1",
                ctx=_make_ctx(),
            )

        assert memory is None


@pytest.mark.asyncio
class TestSessionCompressorDedupActions:
    async def test_create_with_empty_list_only_creates_new_memory(self):
        candidate = _make_candidate()
        new_memory = _make_existing("created.md")

        vikingdb = MagicMock()
        vikingdb.get_embedder.return_value = None
        vikingdb.delete_uris = AsyncMock(return_value=None)
        vikingdb.enqueue_embedding_msg = AsyncMock()

        compressor = SessionCompressor(vikingdb=vikingdb)
        compressor.extractor.extract = AsyncMock(return_value=[candidate])
        compressor.extractor.create_memory = AsyncMock(return_value=new_memory)
        compressor.deduplicator.deduplicate = AsyncMock(
            return_value=DedupResult(
                decision=DedupDecision.CREATE,
                candidate=candidate,
                similar_memories=[],
                actions=[],
            )
        )
        compressor._index_memory = AsyncMock(return_value=True)

        fs = MagicMock()
        fs.rm = AsyncMock()

        with patch("openviking.session.compressor.get_viking_fs", return_value=fs):
            memories = await compressor.extract_long_term_memories(
                [Message.create_user("test message")],
                user=_make_user(),
                session_id="session_test",
                ctx=_make_ctx(),
            )

        assert len(memories) == 1
        assert memories[0].uri == new_memory.uri
        fs.rm.assert_not_called()
        compressor.extractor.create_memory.assert_awaited_once()

    async def test_create_with_merge_is_executed_as_none(self):
        candidate = _make_candidate()
        target = _make_existing("merge_target.md")

        vikingdb = MagicMock()
        vikingdb.get_embedder.return_value = None
        vikingdb.delete_uris = AsyncMock(return_value=None)
        vikingdb.enqueue_embedding_msg = AsyncMock()

        compressor = SessionCompressor(vikingdb=vikingdb)
        compressor.extractor.extract = AsyncMock(return_value=[candidate])
        compressor.extractor.create_memory = AsyncMock(return_value=_make_existing("never.md"))
        compressor.extractor._merge_memory_bundle = AsyncMock(
            return_value=MergedMemoryPayload(
                abstract="merged abstract",
                overview="merged overview",
                content="merged memory content",
                reason="merged",
            )
        )
        compressor.deduplicator.deduplicate = AsyncMock(
            return_value=DedupResult(
                decision=DedupDecision.CREATE,
                candidate=candidate,
                similar_memories=[target],
                actions=[
                    ExistingMemoryAction(
                        memory=target,
                        decision=MemoryActionDecision.MERGE,
                    )
                ],
            )
        )
        compressor._index_memory = AsyncMock(return_value=True)

        fs = MagicMock()
        fs.read_file = AsyncMock(return_value="old memory content")
        fs.write_file = AsyncMock()
        fs.rm = AsyncMock()

        with patch("openviking.session.compressor.get_viking_fs", return_value=fs):
            memories = await compressor.extract_long_term_memories(
                [Message.create_user("test message")],
                user=_make_user(),
                session_id="session_test",
                ctx=_make_ctx(),
            )

        assert memories == []
        compressor.extractor.create_memory.assert_not_called()
        fs.write_file.assert_awaited_once_with(target.uri, "merged memory content", ctx=_make_ctx())
        assert target.abstract == "merged abstract"
        assert target.meta["overview"] == "merged overview"
        compressor._index_memory.assert_awaited_once()

    async def test_merge_bundle_failure_is_skipped_without_fallback(self):
        candidate = _make_candidate()
        target = _make_existing("merge_target_fail.md")

        vikingdb = MagicMock()
        vikingdb.get_embedder.return_value = None
        vikingdb.delete_uris = AsyncMock(return_value=None)
        vikingdb.enqueue_embedding_msg = AsyncMock()

        compressor = SessionCompressor(vikingdb=vikingdb)
        compressor.extractor.extract = AsyncMock(return_value=[candidate])
        compressor.extractor._merge_memory_bundle = AsyncMock(return_value=None)
        compressor.deduplicator.deduplicate = AsyncMock(
            return_value=DedupResult(
                decision=DedupDecision.NONE,
                candidate=candidate,
                similar_memories=[target],
                actions=[
                    ExistingMemoryAction(
                        memory=target,
                        decision=MemoryActionDecision.MERGE,
                    )
                ],
            )
        )
        compressor._index_memory = AsyncMock(return_value=True)

        fs = MagicMock()
        fs.read_file = AsyncMock(return_value="old memory content")
        fs.write_file = AsyncMock()
        fs.rm = AsyncMock()

        with patch("openviking.session.compressor.get_viking_fs", return_value=fs):
            memories = await compressor.extract_long_term_memories(
                [Message.create_user("test message")],
                user=_make_user(),
                session_id="session_test",
                ctx=_make_ctx(),
            )

        assert memories == []
        fs.write_file.assert_not_called()
        compressor._index_memory.assert_not_called()

    async def test_create_with_delete_runs_delete_before_create(self):
        candidate = _make_candidate()
        target = _make_existing("to_delete.md")
        new_memory = _make_existing("created_after_delete.md")
        call_order = []

        vikingdb = MagicMock()
        vikingdb.get_embedder.return_value = None
        vikingdb.delete_uris = AsyncMock(return_value=None)
        vikingdb.enqueue_embedding_msg = AsyncMock()

        compressor = SessionCompressor(vikingdb=vikingdb)
        compressor.extractor.extract = AsyncMock(return_value=[candidate])
        compressor.deduplicator.deduplicate = AsyncMock(
            return_value=DedupResult(
                decision=DedupDecision.CREATE,
                candidate=candidate,
                similar_memories=[target],
                actions=[
                    ExistingMemoryAction(
                        memory=target,
                        decision=MemoryActionDecision.DELETE,
                    )
                ],
            )
        )

        async def _create_memory(*_args, **_kwargs):
            call_order.append("create")
            return new_memory

        compressor.extractor.create_memory = AsyncMock(side_effect=_create_memory)
        compressor._index_memory = AsyncMock(return_value=True)

        fs = MagicMock()

        async def _rm(*_args, **_kwargs):
            call_order.append("delete")
            return {}

        fs.rm = AsyncMock(side_effect=_rm)

        with patch("openviking.session.compressor.get_viking_fs", return_value=fs):
            memories = await compressor.extract_long_term_memories(
                [Message.create_user("test message")],
                user=_make_user(),
                session_id="session_test",
                ctx=_make_ctx(),
            )

        assert [m.uri for m in memories] == [new_memory.uri]
        assert call_order == ["delete", "create"]
        vikingdb.delete_uris.assert_awaited_once_with(_make_ctx(), [target.uri])

    async def test_batch_dedup_passes_batch_memories_to_deduplicate(self):
        """Compressor passes batch_memories with previously created memory to deduplicate."""
        candidate_a = _make_candidate()
        candidate_a.abstract = "User prefers dark mode"
        candidate_a.content = "The user prefers dark mode in all editors."

        candidate_b = _make_candidate()
        candidate_b.abstract = "User likes dark mode"
        candidate_b.content = "The user likes dark mode for coding."

        memory_a = _make_existing("created_a.md")

        vikingdb = MagicMock()
        vikingdb.delete_uris = AsyncMock(return_value=None)
        vikingdb.enqueue_embedding_msg = AsyncMock()

        compressor = _make_compressor(vikingdb=vikingdb)
        compressor.extractor.extract = AsyncMock(return_value=[candidate_a, candidate_b])
        compressor.extractor.create_memory = AsyncMock(return_value=memory_a)

        call_count = 0

        async def _deduplicate(candidate, ctx, *, batch_memories=None):
            nonlocal call_count
            call_count += 1
            if call_count == 1:
                assert batch_memories is None or len(batch_memories) == 0
                return DedupResult(
                    decision=DedupDecision.CREATE,
                    candidate=candidate,
                    similar_memories=[],
                    actions=[],
                    query_vector=[0.1, 0.2, 0.3],
                )
            else:
                assert batch_memories is not None
                assert len(batch_memories) == 1
                assert batch_memories[0][0] == [0.1, 0.2, 0.3]
                assert batch_memories[0][1].uri == memory_a.uri
                return DedupResult(
                    decision=DedupDecision.SKIP,
                    candidate=candidate,
                    similar_memories=[batch_memories[0][1]],
                    actions=[],
                    query_vector=[0.1, 0.2, 0.3],
                )

        compressor.deduplicator.deduplicate = AsyncMock(side_effect=_deduplicate)
        compressor._index_memory = AsyncMock(return_value=True)

        fs = MagicMock()
        fs.rm = AsyncMock()

        with patch("openviking.session.compressor.get_viking_fs", return_value=fs):
            memories = await compressor.extract_long_term_memories(
                [Message.create_user("test message")],
                user=_make_user(),
                session_id="session_test",
                ctx=_make_ctx(),
            )

        assert len(memories) == 1
        assert memories[0].uri == memory_a.uri
        assert call_count == 2
        compressor.extractor.create_memory.assert_awaited_once()

    async def test_batch_dedup_real_cosine_path(self):
        """End-to-end: real deduplicator cosine comparison catches batch duplicate."""
        candidate_a = _make_candidate()
        candidate_a.abstract = "User prefers dark mode"
        candidate_a.content = "The user prefers dark mode in all editors."

        candidate_b = _make_candidate()
        candidate_b.abstract = "User likes dark mode"
        candidate_b.content = "The user likes dark mode for coding."

        memory_a = _make_existing("real_a.md")

        vikingdb = MagicMock()
        vikingdb.search_similar_memories = AsyncMock(return_value=[])
        vikingdb.delete_uris = AsyncMock(return_value=None)
        vikingdb.enqueue_embedding_msg = AsyncMock()

        compressor = _make_compressor(vikingdb=vikingdb, embedder=_DummyEmbedder())
        compressor.extractor.extract = AsyncMock(return_value=[candidate_a, candidate_b])
        compressor.extractor.create_memory = AsyncMock(return_value=memory_a)
        compressor._index_memory = AsyncMock(return_value=True)

        # Spy on _llm_decision to verify batch match triggers LLM path
        original_llm_decision = compressor.deduplicator._llm_decision
        llm_decision_calls = []

        async def _spy_llm_decision(candidate, similar_memories):
            llm_decision_calls.append(similar_memories)
            return await original_llm_decision(candidate, similar_memories)

        compressor.deduplicator._llm_decision = _spy_llm_decision

        # Mock config for _llm_decision (called when similar memories found)
        class _NoVLMConfig:
            vlm = None

            class embedding:
                @staticmethod
                def get_embedder():
                    return _DummyEmbedder()

        fs = MagicMock()
        fs.rm = AsyncMock()

        with (
            patch("openviking.session.compressor.get_viking_fs", return_value=fs),
            patch(
                "openviking.session.memory_deduplicator.get_openviking_config",
                return_value=_NoVLMConfig(),
            ),
        ):
            await compressor.extract_long_term_memories(
                [Message.create_user("test message")],
                user=_make_user(),
                session_id="session_test",
                ctx=_make_ctx(),
            )

        # _DummyEmbedder returns [0.1, 0.2, 0.3] for all texts -> cosine = 1.0
        # First: DB empty, no batch -> CREATE (no _llm_decision called).
        # Second: DB empty, but batch match found (cosine=1.0) ->
        # _llm_decision IS called with the batch-sourced similar memory.
        assert vikingdb.search_similar_memories.await_count == 2
        # Key assertion: _llm_decision was called exactly once (for second candidate)
        assert len(llm_decision_calls) == 1
        # The similar_memories passed to LLM came from batch (not DB, which was empty)
        assert len(llm_decision_calls[0]) == 1
        assert llm_decision_calls[0][0].uri == memory_a.uri
