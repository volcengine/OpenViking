# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: Apache-2.0

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from openviking.core.context import Context
from openviking.session.compressor import SessionCompressor
from openviking.session.memory_extractor import CandidateMemory, MemoryCategory
from openviking_cli.session.user_id import UserIdentifier


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


def _make_context(uri: str, abstract: str = "Existing memory") -> Context:
    return Context(
        uri=uri,
        context_type="memory",
        abstract=abstract,
        meta={"overview": "Existing overview"},
    )


class TestConvertToTempUri:
    def test_user_uri_converted_to_temp_uri(self):
        vikingdb = MagicMock()
        compressor = SessionCompressor(vikingdb=vikingdb)

        user_space = _make_user().user_space_name()
        target_uri = f"viking://user/{user_space}/memories/preferences/pref1.md"
        user_temp_uri = "viking://user/temp_user_123"

        result = compressor._convert_to_temp_uri(target_uri, user_temp_uri, None)

        expected = f"{user_temp_uri}/memories/preferences/pref1.md"
        assert result == expected

    def test_agent_uri_converted_to_temp_uri(self):
        vikingdb = MagicMock()
        compressor = SessionCompressor(vikingdb=vikingdb)

        agent_space = _make_user().agent_space_name()
        target_uri = f"viking://agent/{agent_space}/memories/cases/case1.md"
        agent_temp_uri = "viking://agent/temp_agent_456"

        result = compressor._convert_to_temp_uri(target_uri, None, agent_temp_uri)

        expected = f"{agent_temp_uri}/memories/cases/case1.md"
        assert result == expected

    def test_no_temp_uri_returns_original_uri(self):
        vikingdb = MagicMock()
        compressor = SessionCompressor(vikingdb=vikingdb)

        user_space = _make_user().user_space_name()
        target_uri = f"viking://user/{user_space}/memories/preferences/pref1.md"

        result = compressor._convert_to_temp_uri(target_uri, None, None)

        assert result == target_uri

    def test_mixed_uris_only_convert_matching_type(self):
        vikingdb = MagicMock()
        compressor = SessionCompressor(vikingdb=vikingdb)

        user_space = _make_user().user_space_name()
        agent_space = _make_user().agent_space_name()

        user_uri = f"viking://user/{user_space}/memories/preferences/pref1.md"
        agent_uri = f"viking://agent/{agent_space}/memories/cases/case1.md"

        user_temp_uri = "viking://user/temp_user_123"

        result_user = compressor._convert_to_temp_uri(user_uri, user_temp_uri, None)
        result_agent = compressor._convert_to_temp_uri(agent_uri, user_temp_uri, None)

        assert result_user == f"{user_temp_uri}/memories/preferences/pref1.md"
        assert result_agent == agent_uri

    def test_agent_uri_with_both_temp_uris(self):
        vikingdb = MagicMock()
        compressor = SessionCompressor(vikingdb=vikingdb)

        agent_space = _make_user().agent_space_name()
        target_uri = f"viking://agent/{agent_space}/memories/patterns/pattern1.md"
        user_temp_uri = "viking://user/temp_user_123"
        agent_temp_uri = "viking://agent/temp_agent_456"

        result = compressor._convert_to_temp_uri(target_uri, user_temp_uri, agent_temp_uri)

        expected = f"{agent_temp_uri}/memories/patterns/pattern1.md"
        assert result == expected

    def test_user_uri_with_both_temp_uris(self):
        vikingdb = MagicMock()
        compressor = SessionCompressor(vikingdb=vikingdb)

        user_space = _make_user().user_space_name()
        target_uri = f"viking://user/{user_space}/memories/entities/entity1.md"
        user_temp_uri = "viking://user/temp_user_123"
        agent_temp_uri = "viking://agent/temp_agent_456"

        result = compressor._convert_to_temp_uri(target_uri, user_temp_uri, agent_temp_uri)

        expected = f"{user_temp_uri}/memories/entities/entity1.md"
        assert result == expected

    def test_non_viking_uri_returns_unchanged(self):
        vikingdb = MagicMock()
        compressor = SessionCompressor(vikingdb=vikingdb)

        target_uri = "file:///some/local/path/memory.md"
        user_temp_uri = "viking://user/temp_user_123"

        result = compressor._convert_to_temp_uri(target_uri, user_temp_uri, None)

        assert result == target_uri

    def test_short_uri_returns_unchanged(self):
        vikingdb = MagicMock()
        compressor = SessionCompressor(vikingdb=vikingdb)

        target_uri = "viking://user"
        user_temp_uri = "viking://user/temp_user_123"

        result = compressor._convert_to_temp_uri(target_uri, user_temp_uri, None)

        assert result == target_uri


@pytest.mark.asyncio
class TestMergeIntoExisting:
    async def test_merge_into_existing_success(self):
        vikingdb = MagicMock()
        compressor = SessionCompressor(vikingdb=vikingdb)

        user_space = _make_user().user_space_name()
        target_uri = f"viking://user/{user_space}/memories/preferences/pref1.md"
        user_temp_uri = "viking://user/temp_user_123"
        temp_uri = f"{user_temp_uri}/memories/preferences/pref1.md"

        candidate = _make_candidate()
        target_memory = _make_context(target_uri)

        viking_fs = AsyncMock()
        viking_fs.read_file = AsyncMock(return_value="Existing content")
        viking_fs.write_file = AsyncMock()

        mock_payload = MagicMock()
        mock_payload.abstract = "Merged abstract"
        mock_payload.overview = "Merged overview"
        mock_payload.content = "Merged content"

        with patch.object(
            compressor.extractor,
            "_merge_memory_bundle",
            AsyncMock(return_value=mock_payload),
        ):
            ctx = MagicMock()
            result = await compressor._merge_into_existing(
                candidate,
                target_memory,
                viking_fs,
                ctx=ctx,
                user_temp_uri=user_temp_uri,
                agent_temp_uri=None,
            )

        assert result is True
        viking_fs.read_file.assert_called_once()
        viking_fs.write_file.assert_called_once()
        assert target_memory.abstract == "Merged abstract"
        assert target_memory.meta.get("overview") == "Merged overview"

    async def test_merge_into_existing_without_temp_uri(self):
        vikingdb = MagicMock()
        compressor = SessionCompressor(vikingdb=vikingdb)

        user_space = _make_user().user_space_name()
        target_uri = f"viking://user/{user_space}/memories/preferences/pref1.md"

        candidate = _make_candidate()
        target_memory = _make_context(target_uri)

        viking_fs = AsyncMock()
        viking_fs.read_file = AsyncMock(return_value="Existing content")
        viking_fs.write_file = AsyncMock()

        mock_payload = MagicMock()
        mock_payload.abstract = "Merged abstract"
        mock_payload.overview = "Merged overview"
        mock_payload.content = "Merged content"

        with patch.object(
            compressor.extractor,
            "_merge_memory_bundle",
            AsyncMock(return_value=mock_payload),
        ):
            ctx = MagicMock()
            result = await compressor._merge_into_existing(
                candidate,
                target_memory,
                viking_fs,
                ctx=ctx,
                user_temp_uri=None,
                agent_temp_uri=None,
            )

        assert result is True
        viking_fs.read_file.assert_called_once_with(target_uri, ctx=ctx)

    async def test_merge_into_existing_merge_bundle_returns_none(self):
        vikingdb = MagicMock()
        compressor = SessionCompressor(vikingdb=vikingdb)

        user_space = _make_user().user_space_name()
        target_uri = f"viking://user/{user_space}/memories/preferences/pref1.md"
        user_temp_uri = "viking://user/temp_user_123"

        candidate = _make_candidate()
        target_memory = _make_context(target_uri)

        viking_fs = AsyncMock()
        viking_fs.read_file = AsyncMock(return_value="Existing content")

        with patch.object(
            compressor.extractor,
            "_merge_memory_bundle",
            AsyncMock(return_value=None),
        ):
            ctx = MagicMock()
            result = await compressor._merge_into_existing(
                candidate,
                target_memory,
                viking_fs,
                ctx=ctx,
                user_temp_uri=user_temp_uri,
                agent_temp_uri=None,
            )

        assert result is False
        viking_fs.write_file.assert_not_called()

    async def test_merge_into_existing_read_file_exception(self):
        vikingdb = MagicMock()
        compressor = SessionCompressor(vikingdb=vikingdb)

        user_space = _make_user().user_space_name()
        target_uri = f"viking://user/{user_space}/memories/preferences/pref1.md"
        user_temp_uri = "viking://user/temp_user_123"

        candidate = _make_candidate()
        target_memory = _make_context(target_uri)

        viking_fs = AsyncMock()
        viking_fs.read_file = AsyncMock(side_effect=Exception("Read error"))

        ctx = MagicMock()
        result = await compressor._merge_into_existing(
            candidate,
            target_memory,
            viking_fs,
            ctx=ctx,
            user_temp_uri=user_temp_uri,
            agent_temp_uri=None,
        )

        assert result is False

    async def test_merge_into_existing_agent_uri(self):
        vikingdb = MagicMock()
        compressor = SessionCompressor(vikingdb=vikingdb)

        agent_space = _make_user().agent_space_name()
        target_uri = f"viking://agent/{agent_space}/memories/cases/case1.md"
        agent_temp_uri = "viking://agent/temp_agent_456"

        candidate = _make_candidate(category=MemoryCategory.CASES)
        target_memory = _make_context(target_uri)

        viking_fs = AsyncMock()
        viking_fs.read_file = AsyncMock(return_value="Existing content")
        viking_fs.write_file = AsyncMock()

        mock_payload = MagicMock()
        mock_payload.abstract = "Merged case abstract"
        mock_payload.overview = "Merged case overview"
        mock_payload.content = "Merged case content"

        with patch.object(
            compressor.extractor,
            "_merge_memory_bundle",
            AsyncMock(return_value=mock_payload),
        ):
            ctx = MagicMock()
            result = await compressor._merge_into_existing(
                candidate,
                target_memory,
                viking_fs,
                ctx=ctx,
                user_temp_uri=None,
                agent_temp_uri=agent_temp_uri,
            )

        assert result is True
        expected_temp_uri = f"{agent_temp_uri}/memories/cases/case1.md"
        viking_fs.read_file.assert_called_once_with(expected_temp_uri, ctx=ctx)


@pytest.mark.asyncio
class TestDeleteExistingMemory:
    async def test_delete_existing_memory_success(self):
        vikingdb = MagicMock()
        vikingdb.delete_uris = AsyncMock()
        compressor = SessionCompressor(vikingdb=vikingdb)

        user_space = _make_user().user_space_name()
        target_uri = f"viking://user/{user_space}/memories/preferences/pref1.md"
        user_temp_uri = "viking://user/temp_user_123"
        temp_uri = f"{user_temp_uri}/memories/preferences/pref1.md"

        memory = _make_context(target_uri)

        viking_fs = AsyncMock()
        viking_fs.rm = AsyncMock()

        ctx = MagicMock()
        result = await compressor._delete_existing_memory(
            memory,
            viking_fs,
            ctx=ctx,
            user_temp_uri=user_temp_uri,
            agent_temp_uri=None,
        )

        assert result is True
        viking_fs.rm.assert_called_once_with(temp_uri, recursive=False, ctx=ctx)
        vikingdb.delete_uris.assert_called_once_with(ctx, [temp_uri])

    async def test_delete_existing_memory_without_temp_uri(self):
        vikingdb = MagicMock()
        vikingdb.delete_uris = AsyncMock()
        compressor = SessionCompressor(vikingdb=vikingdb)

        user_space = _make_user().user_space_name()
        target_uri = f"viking://user/{user_space}/memories/preferences/pref1.md"

        memory = _make_context(target_uri)

        viking_fs = AsyncMock()
        viking_fs.rm = AsyncMock()

        ctx = MagicMock()
        result = await compressor._delete_existing_memory(
            memory,
            viking_fs,
            ctx=ctx,
            user_temp_uri=None,
            agent_temp_uri=None,
        )

        assert result is True
        viking_fs.rm.assert_called_once_with(target_uri, recursive=False, ctx=ctx)
        vikingdb.delete_uris.assert_called_once_with(ctx, [target_uri])

    async def test_delete_existing_memory_rm_exception(self):
        vikingdb = MagicMock()
        compressor = SessionCompressor(vikingdb=vikingdb)

        user_space = _make_user().user_space_name()
        target_uri = f"viking://user/{user_space}/memories/preferences/pref1.md"
        user_temp_uri = "viking://user/temp_user_123"

        memory = _make_context(target_uri)

        viking_fs = AsyncMock()
        viking_fs.rm = AsyncMock(side_effect=Exception("Delete error"))

        ctx = MagicMock()
        result = await compressor._delete_existing_memory(
            memory,
            viking_fs,
            ctx=ctx,
            user_temp_uri=user_temp_uri,
            agent_temp_uri=None,
        )

        assert result is False

    async def test_delete_existing_memory_vector_delete_exception(self):
        vikingdb = MagicMock()
        vikingdb.delete_uris = AsyncMock(side_effect=Exception("Vector delete error"))
        compressor = SessionCompressor(vikingdb=vikingdb)

        user_space = _make_user().user_space_name()
        target_uri = f"viking://user/{user_space}/memories/preferences/pref1.md"
        user_temp_uri = "viking://user/temp_user_123"

        memory = _make_context(target_uri)

        viking_fs = AsyncMock()
        viking_fs.rm = AsyncMock()

        ctx = MagicMock()
        result = await compressor._delete_existing_memory(
            memory,
            viking_fs,
            ctx=ctx,
            user_temp_uri=user_temp_uri,
            agent_temp_uri=None,
        )

        assert result is True
        viking_fs.rm.assert_called_once()
        vikingdb.delete_uris.assert_called_once()

    async def test_delete_existing_memory_agent_uri(self):
        vikingdb = MagicMock()
        vikingdb.delete_uris = AsyncMock()
        compressor = SessionCompressor(vikingdb=vikingdb)

        agent_space = _make_user().agent_space_name()
        target_uri = f"viking://agent/{agent_space}/memories/cases/case1.md"
        agent_temp_uri = "viking://agent/temp_agent_456"
        temp_uri = f"{agent_temp_uri}/memories/cases/case1.md"

        memory = _make_context(target_uri)

        viking_fs = AsyncMock()
        viking_fs.rm = AsyncMock()

        ctx = MagicMock()
        result = await compressor._delete_existing_memory(
            memory,
            viking_fs,
            ctx=ctx,
            user_temp_uri=None,
            agent_temp_uri=agent_temp_uri,
        )

        assert result is True
        viking_fs.rm.assert_called_once_with(temp_uri, recursive=False, ctx=ctx)
        vikingdb.delete_uris.assert_called_once_with(ctx, [temp_uri])
