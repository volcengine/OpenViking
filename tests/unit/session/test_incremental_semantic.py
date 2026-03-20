# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: Apache-2.0
"""Tests for incremental semantic processing on session commit.

Verifies the fix for https://github.com/volcengine/OpenViking/issues/505:
When a SessionCompressor successfully extracts memories and flushes its own
incremental SemanticMsg(s) with per-file change sets, the session.commit()
fallback must NOT enqueue an additional full-directory SemanticMsg that
triggers an O(n²) re-summarisation of every file.
"""

import asyncio
from typing import Any, Dict, List, Optional
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from openviking.storage.queuefs.semantic_msg import SemanticMsg


class FakeSemanticQueue:
    """In-memory queue that records enqueued messages."""

    def __init__(self):
        self.messages: List[SemanticMsg] = []

    async def enqueue(self, msg: SemanticMsg) -> str:
        self.messages.append(msg)
        return msg.id


class FakeQueueManager:
    """Minimal queue manager stub."""

    SEMANTIC = "semantic"

    def __init__(self):
        self._queues: Dict[str, FakeSemanticQueue] = {}

    def get_queue(self, name: str, allow_create: bool = False) -> FakeSemanticQueue:
        if name not in self._queues:
            self._queues[name] = FakeSemanticQueue()
        return self._queues[name]


# ---------------------------------------------------------------------------
# Test: session.commit_async should NOT enqueue a second SemanticMsg when
# the compressor already flushed incremental messages.
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_commit_skips_fallback_semantic_when_compressor_flushed():
    """When compressor extracts memories, session.commit should not re-enqueue."""
    from openviking.session.session import Session

    # Build a minimal Session with mocked internals
    session = Session.__new__(Session)
    session._session_uri = "viking://memories/user/default/sessions/test"
    session._messages = [MagicMock()]  # non-empty so commit proceeds
    session._compression = MagicMock()
    session._compression.compression_index = 0
    session._stats = MagicMock()
    session._stats.memories_extracted = 0
    session._stats.total_turns = 0
    session._stats.contexts_used = 0
    session._stats.skills_used = 0

    # Mock ctx
    session.ctx = MagicMock()
    session.ctx.account_id = "default"
    session.ctx.user.user_id = "default"
    session.ctx.user.agent_id = "default"
    session.ctx.role.value = "root"
    session.user = MagicMock()
    session.session_id = "test"

    # Mock compressor that "successfully" extracts 3 memories
    mock_compressor = AsyncMock()
    mock_compressor.extract_long_term_memories = AsyncMock(
        return_value=[MagicMock(), MagicMock(), MagicMock()]
    )
    session._session_compressor = mock_compressor

    # Mock internal methods
    session._generate_archive_summary_async = AsyncMock(return_value="summary")
    session._extract_abstract_from_summary = MagicMock(return_value="abstract")
    session._write_archive_async = AsyncMock()
    session._write_to_agfs_async = AsyncMock()
    session._write_relations_async = AsyncMock()
    session._update_active_counts_async = AsyncMock(return_value=0)
    session._vikingdb_manager = None

    # Set up queue manager
    fake_qm = FakeQueueManager()

    # Mock redo log
    mock_redo = MagicMock()
    mock_redo.write_pending = MagicMock()
    mock_redo.mark_done = MagicMock()

    with (
        patch("openviking.session.session.get_lock_manager") as mock_lock_mgr,
        patch("openviking.session.session.get_current_telemetry") as mock_telem,
        patch("openviking.storage.queuefs.get_queue_manager", return_value=fake_qm),
    ):
        mock_lock_mgr.return_value.redo_log = mock_redo
        mock_telem.return_value.set = MagicMock()

        result = await session.commit_async()

    # The compressor extracted 3 memories, so the session should NOT have
    # enqueued any additional SemanticMsg.
    semantic_queue = fake_qm.get_queue("semantic")
    assert len(semantic_queue.messages) == 0, (
        f"Expected 0 SemanticMsg from session fallback, got {len(semantic_queue.messages)}. "
        "The compressor already flushed incremental messages."
    )
    assert result["memories_extracted"] == 3


@pytest.mark.asyncio
async def test_commit_enqueues_fallback_semantic_when_no_compressor():
    """When no compressor is configured, session.commit should enqueue fallback."""
    from openviking.session.session import Session

    session = Session.__new__(Session)
    session._session_uri = "viking://memories/user/default/sessions/test"
    session._messages = [MagicMock()]
    session._compression = MagicMock()
    session._compression.compression_index = 0
    session._stats = MagicMock()
    session._stats.memories_extracted = 0
    session._stats.total_turns = 0
    session._stats.contexts_used = 0
    session._stats.skills_used = 0

    session.ctx = MagicMock()
    session.ctx.account_id = "default"
    session.ctx.user.user_id = "default"
    session.ctx.user.agent_id = "default"
    session.ctx.role.value = "root"
    session.user = MagicMock()
    session.session_id = "test"

    # No compressor configured
    session._session_compressor = None

    session._generate_archive_summary_async = AsyncMock(return_value="summary")
    session._extract_abstract_from_summary = MagicMock(return_value="abstract")
    session._write_archive_async = AsyncMock()
    session._write_to_agfs_async = AsyncMock()
    session._write_relations_async = AsyncMock()
    session._update_active_counts_async = AsyncMock(return_value=0)
    session._vikingdb_manager = None

    fake_qm = FakeQueueManager()
    mock_redo = MagicMock()
    mock_redo.write_pending = MagicMock()
    mock_redo.mark_done = MagicMock()

    with (
        patch("openviking.session.session.get_lock_manager") as mock_lock_mgr,
        patch("openviking.session.session.get_current_telemetry") as mock_telem,
        patch("openviking.storage.queuefs.get_queue_manager", return_value=fake_qm),
    ):
        mock_lock_mgr.return_value.redo_log = mock_redo
        mock_telem.return_value.set = MagicMock()

        result = await session.commit_async()

    # No compressor → session should enqueue a fallback SemanticMsg
    semantic_queue = fake_qm.get_queue("semantic")
    assert len(semantic_queue.messages) == 1, (
        f"Expected 1 fallback SemanticMsg, got {len(semantic_queue.messages)}"
    )
    msg = semantic_queue.messages[0]
    assert msg.context_type == "memory"
    assert msg.uri == session._session_uri


@pytest.mark.asyncio
async def test_commit_enqueues_fallback_when_compressor_extracts_zero():
    """When compressor extracts 0 memories, session should enqueue fallback."""
    from openviking.session.session import Session

    session = Session.__new__(Session)
    session._session_uri = "viking://memories/user/default/sessions/test"
    session._messages = [MagicMock()]
    session._compression = MagicMock()
    session._compression.compression_index = 0
    session._stats = MagicMock()
    session._stats.memories_extracted = 0
    session._stats.total_turns = 0
    session._stats.contexts_used = 0
    session._stats.skills_used = 0

    session.ctx = MagicMock()
    session.ctx.account_id = "default"
    session.ctx.user.user_id = "default"
    session.ctx.user.agent_id = "default"
    session.ctx.role.value = "root"
    session.user = MagicMock()
    session.session_id = "test"

    # Compressor returns 0 memories
    mock_compressor = AsyncMock()
    mock_compressor.extract_long_term_memories = AsyncMock(return_value=[])
    session._session_compressor = mock_compressor

    session._generate_archive_summary_async = AsyncMock(return_value="summary")
    session._extract_abstract_from_summary = MagicMock(return_value="abstract")
    session._write_archive_async = AsyncMock()
    session._write_to_agfs_async = AsyncMock()
    session._write_relations_async = AsyncMock()
    session._update_active_counts_async = AsyncMock(return_value=0)
    session._vikingdb_manager = None

    fake_qm = FakeQueueManager()
    mock_redo = MagicMock()
    mock_redo.write_pending = MagicMock()
    mock_redo.mark_done = MagicMock()

    with (
        patch("openviking.session.session.get_lock_manager") as mock_lock_mgr,
        patch("openviking.session.session.get_current_telemetry") as mock_telem,
        patch("openviking.storage.queuefs.get_queue_manager", return_value=fake_qm),
    ):
        mock_lock_mgr.return_value.redo_log = mock_redo
        mock_telem.return_value.set = MagicMock()

        result = await session.commit_async()

    # Compressor returned empty → session should enqueue fallback
    semantic_queue = fake_qm.get_queue("semantic")
    assert len(semantic_queue.messages) == 1


# ---------------------------------------------------------------------------
# Test: semantic_processor should reuse existing summaries even without
# explicit changes dict
# ---------------------------------------------------------------------------


def test_semantic_msg_changes_none_by_default():
    """SemanticMsg should default changes to None."""
    msg = SemanticMsg(uri="viking://test", context_type="memory")
    assert msg.changes is None
    assert msg.recursive is True
