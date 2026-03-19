# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: Apache-2.0
"""Regression tests for issue #505: misdirected SemanticMsg enqueue."""

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from openviking.message import TextPart
from openviking.session import Session


@pytest.mark.asyncio
async def test_no_misdirected_semantic_enqueue_after_flush():
    """After _flush_semantic_operations() enqueues proper SemanticMsg with changes dict,
    commit_async() must NOT enqueue a second SemanticMsg targeting the session URI.

    Regression test for https://github.com/volcengine/OpenViking/issues/505
    """
    # Construct a Session directly with mocks (avoids full client init)
    mock_fs = AsyncMock()
    mock_compressor = AsyncMock()
    mock_compressor.extract_long_term_memories = AsyncMock(return_value=[])

    session = Session(
        viking_fs=mock_fs,
        session_compressor=mock_compressor,
        session_id="test_505_regression",
    )
    session.add_message("user", [TextPart("Hello")])
    session.add_message("assistant", [TextPart("Hi there")])

    # Mock the queue manager to track enqueue calls
    mock_queue = AsyncMock()
    mock_queue_manager = MagicMock()
    mock_queue_manager.SEMANTIC = "semantic"
    mock_queue_manager.get_queue.return_value = mock_queue

    # Mock redo log
    mock_redo_log = MagicMock()
    mock_lock_manager = MagicMock()
    mock_lock_manager.redo_log = mock_redo_log

    with (
        patch("openviking.storage.queuefs.get_queue_manager", return_value=mock_queue_manager),
        patch("openviking.storage.transaction.get_lock_manager", return_value=mock_lock_manager),
        patch("openviking.telemetry.get_current_telemetry", return_value=MagicMock()),
        patch.object(
            session,
            "_generate_archive_summary_async",
            new_callable=AsyncMock,
            return_value="summary",
        ),
        patch.object(session, "_extract_abstract_from_summary", return_value="abstract"),
        patch.object(session, "_write_archive_async", new_callable=AsyncMock),
        patch.object(session, "_write_to_agfs_async", new_callable=AsyncMock),
        patch.object(session, "_write_relations_async", new_callable=AsyncMock),
        patch.object(
            session, "_update_active_counts_async", new_callable=AsyncMock, return_value=True
        ),
    ):
        await session.commit_async()

    # The compressor's _flush_semantic_operations() handles semantic enqueue.
    # session.py must NOT enqueue an additional misdirected SemanticMsg.
    assert mock_queue.enqueue.await_count == 0, (
        f"Expected 0 enqueue calls from session.py (compressor handles this), "
        f"got {mock_queue.enqueue.await_count}"
    )


@pytest.mark.asyncio
async def test_process_memory_directory_loads_cache_when_changes_none():
    """_process_memory_directory() must load cached summaries from .overview.md
    even when msg.changes is None. Without this, every file triggers a VLM call.

    Regression test for https://github.com/volcengine/OpenViking/issues/505
    """
    from openviking.storage.queuefs.semantic_msg import SemanticMsg
    from openviking.storage.queuefs.semantic_processor import SemanticProcessor

    processor = SemanticProcessor.__new__(SemanticProcessor)
    processor.max_concurrent_llm = 1
    processor._current_ctx = MagicMock()
    processor._current_msg = None

    msg = SemanticMsg(
        uri="viking://test/memories/dir1",
        context_type="memory",
    )
    assert msg.changes is None  # Precondition: no changes dict

    mock_fs = AsyncMock()
    # ls returns 2 files
    mock_fs.ls = AsyncMock(
        return_value=[
            {"name": "file1.md", "isDir": False},
            {"name": "file2.md", "isDir": False},
        ]
    )
    # read_file returns cached overview
    mock_fs.read_file = AsyncMock(return_value="cached overview content")
    # write_file for saving new overview
    mock_fs.write_file = AsyncMock()

    mock_generate_summary = AsyncMock()
    mock_generate_overview = AsyncMock(return_value="overview content")
    mock_extract_abstract = MagicMock(return_value="abstract")
    mock_enforce_limits = MagicMock(return_value=("overview content", "abstract"))

    # Mock VikingURI to return predictable URIs
    mock_viking_uri_instance = MagicMock()
    mock_viking_uri_instance.join.side_effect = lambda name: MagicMock(
        uri=f"viking://test/memories/dir1/{name}"
    )
    mock_viking_uri_cls = MagicMock(return_value=mock_viking_uri_instance)

    with (
        patch("openviking.storage.queuefs.semantic_processor.get_viking_fs", return_value=mock_fs),
        patch("openviking.storage.queuefs.semantic_processor.VikingURI", mock_viking_uri_cls),
        patch.object(processor, "_generate_single_file_summary", mock_generate_summary),
        patch.object(processor, "_generate_overview", mock_generate_overview),
        patch.object(processor, "_extract_abstract_from_overview", mock_extract_abstract),
        patch.object(processor, "_enforce_size_limits", mock_enforce_limits),
        patch.object(
            processor,
            "_parse_overview_md",
            return_value={
                "file1.md": "Summary of file 1.",
                "file2.md": "Summary of file 2.",
            },
        ),
        patch.object(processor, "_vectorize_directory", new_callable=AsyncMock),
    ):
        await processor._process_memory_directory(msg)

    # With cache loaded, NO VLM calls should be made for unchanged files
    assert mock_generate_summary.await_count == 0, (
        f"Expected 0 VLM calls (cache should serve both files), "
        f"got {mock_generate_summary.await_count}"
    )
