# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: Apache-2.0

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


class _DummyVikingDB:
    def __init__(self):
        self._embedder = None

    def get_embedder(self):
        return self._embedder


class _DummyEmbedResult:
    def __init__(self, dense_vector):
        self.dense_vector = dense_vector


class _DummyEmbedder:
    def embed(self, _text):
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

        similar = await dedup._find_similar_memories(candidate)

        assert len(similar) == 1
        assert similar[0].uri == existing.uri
        call = vikingdb.search_similar_memories.await_args.kwargs
        assert call["account_id"] == "acc1"
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

        similar = await dedup._find_similar_memories(_make_candidate())

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
