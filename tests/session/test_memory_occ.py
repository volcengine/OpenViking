# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0

import asyncio
from unittest.mock import AsyncMock, MagicMock, patch

from openviking.core.context import Context
from openviking.server.identity import RequestContext, Role
from openviking.session.compressor import SessionCompressor
from openviking.session.memory_extractor import (
    CandidateMemory,
    MemoryCategory,
    MergedMemoryPayload,
)
from openviking_cli.session.user_id import UserIdentifier


def _make_user() -> UserIdentifier:
    return UserIdentifier("acc1", "test_user", "test_agent")


def _make_ctx() -> RequestContext:
    return RequestContext(user=_make_user(), role=Role.USER)


def _make_candidate() -> CandidateMemory:
    return CandidateMemory(
        category=MemoryCategory.PREFERENCES,
        abstract="test abstract",
        overview="test overview",
        content="test content",
        source_session="s1",
        user=_make_user(),
        language="en",
    )


def _make_memory(meta=None) -> Context:
    m = Context(
        uri="viking://user/test_user/memories/preferences/existing.md",
        parent_uri="viking://user/test_user/memories/preferences",
        is_leaf=True,
        abstract="existing",
        context_type="memory",
        category="preferences",
    )
    if meta:
        m.meta = meta
    return m


def _make_compressor() -> SessionCompressor:
    with patch("openviking.session.memory_deduplicator.get_openviking_config") as cfg:
        cfg.return_value.embedding.get_embedder.return_value = None
        return SessionCompressor(vikingdb=MagicMock())


def _run(coro):
    return asyncio.get_event_loop().run_until_complete(coro)


class TestDeleteGuardrail:
    def test_low_dedup_score_blocks_delete(self):
        memory = _make_memory(meta={"_dedup_score": 0.3})
        fs = MagicMock()
        fs.rm = AsyncMock()
        assert _run(_make_compressor()._delete_existing_memory(memory, fs, _make_ctx())) is False
        fs.rm.assert_not_called()

    def test_high_dedup_score_allows_delete(self):
        memory = _make_memory(meta={"_dedup_score": 0.8})
        fs = MagicMock()
        fs.rm = AsyncMock()
        assert _run(_make_compressor()._delete_existing_memory(memory, fs, _make_ctx())) is True
        fs.rm.assert_called_once()


class TestMergeOCC:
    def test_merge_aborted_on_concurrent_modification(self):
        compressor = _make_compressor()
        fs = MagicMock()
        fs.stat = AsyncMock(return_value={"modTime": "T1"})
        fs.read_file = AsyncMock(return_value="old")
        fs.cas_write_file = AsyncMock(return_value=False)

        with patch.object(
            compressor.extractor,
            "_merge_memory_bundle",
            new=AsyncMock(
                return_value=MergedMemoryPayload(
                    abstract="a", overview="o", content="c", reason="r"
                )
            ),
        ):
            with patch.object(compressor, "_index_memory", new=AsyncMock()):
                assert (
                    _run(
                        compressor._merge_into_existing(
                            _make_candidate(), _make_memory(), fs, _make_ctx()
                        )
                    )
                    is False
                )

    def test_merge_succeeds_when_no_concurrent_write(self):
        compressor = _make_compressor()
        fs = MagicMock()
        fs.stat = AsyncMock(return_value={"modTime": "T1"})
        fs.read_file = AsyncMock(return_value="old")
        fs.cas_write_file = AsyncMock(return_value=True)

        with patch.object(
            compressor.extractor,
            "_merge_memory_bundle",
            new=AsyncMock(
                return_value=MergedMemoryPayload(
                    abstract="a", overview="o", content="c", reason="r"
                )
            ),
        ):
            with patch.object(compressor, "_index_memory", new=AsyncMock()):
                assert (
                    _run(
                        compressor._merge_into_existing(
                            _make_candidate(), _make_memory(), fs, _make_ctx()
                        )
                    )
                    is True
                )
        fs.cas_write_file.assert_called_once()


class TestCasWriteFile:
    def test_cas_write_blocks_on_modtime_mismatch(self):
        from openviking.storage.viking_fs import VikingFS

        fs = VikingFS.__new__(VikingFS)
        fs._agfs = MagicMock()
        fs._agfs.stat = MagicMock(return_value={"modTime": "T2"})
        fs._agfs.write = MagicMock()
        fs._ensure_access = MagicMock()
        fs._run_in_threadpool = AsyncMock(side_effect=lambda fn, *a: fn(*a))

        assert _run(fs.cas_write_file("viking://test/f.md", "c", "T1", ctx=_make_ctx())) is False
        fs._agfs.write.assert_not_called()
