# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0

"""WM is optional; durable archives and long-term extraction are not."""

import json
from unittest.mock import AsyncMock

import pytest

from openviking.message import TextPart
from openviking.session import Session
from openviking.session.checkpoints import CheckpointPlanner
from tests.server.conftest import service as service
from tests.session.test_session_commit import _wait_for_task


@pytest.mark.parametrize("keep_count", [0, 2])
async def test_default_commit_keeps_raw_archive_and_ltm_without_summary(
    session_with_messages, keep_count, monkeypatch
):
    session = session_with_messages
    summary = AsyncMock(side_effect=AssertionError("WM must not run by default"))
    checkpoint = AsyncMock(side_effect=AssertionError("checkpoint must not run by default"))
    extract = AsyncMock(return_value=[])
    monkeypatch.setattr(Session, "_generate_archive_summary_async", summary)
    monkeypatch.setattr(CheckpointPlanner, "collect_requests_for_phase2", checkpoint)
    monkeypatch.setattr(session._session_compressor, "extract_long_term_memories", extract)
    original = list(session.messages)
    result = await session.commit_async(keep_recent_count=keep_count)
    assert result["effective_enable_working_memory"] is False
    task = await _wait_for_task(result["task_id"])
    assert task["status"] == "completed"
    summary.assert_not_awaited()
    checkpoint.assert_not_awaited()
    extract.assert_awaited_once()
    assert extract.call_args.kwargs["latest_archive_overview"] == ""
    done = await session._archives.read_done(result["archive_uri"])
    assert done["enable_working_memory"] is False
    archive_id = result["archive_uri"].rsplit("/", 1)[-1]
    archive = await session.get_session_archive(archive_id)
    assert archive["overview"] == archive["abstract"] == ""
    assert [message["id"] for message in archive["messages"]] == [
        message.id for message in original[: len(original) - keep_count]
    ]
    context = await session.get_session_context()
    assert context["latest_archive_overview"] == ""
    assert context["stats"]["failedArchives"] == 0
    assert len(context["messages"]) == keep_count


@pytest.mark.parametrize("override", [True, False, None])
async def test_commit_wm_override_only_changes_queued_wm_field(session, monkeypatch, override):
    policy = {
        "self": {"enabled": False},
        "peer": {"enabled": True},
        "memory_types": ["profile"],
        "working_memory": {"enabled": False},
    }
    session._meta.memory_policy = policy
    await session._save_meta()
    session.add_message("user", [TextPart("remember this preference")])
    queued = []

    async def enqueue(_name, data):
        queued.append(data)
        return data["task_id"]

    from openviking.storage.queuefs import get_queue_manager

    monkeypatch.setattr(get_queue_manager(), "enqueue", enqueue)
    result = await session.commit_async(enable_working_memory=override)
    expected = {**policy, "working_memory": {"enabled": override is True}}
    assert queued[0]["memory_policy"] == expected
    assert result["effective_enable_working_memory"] is (override is True)
    assert session._meta.memory_policy == policy
    phase1 = await session._read_phase1_meta(result["archive_uri"])
    assert phase1["queue_message"]["memory_policy"] == expected


@pytest.mark.parametrize("wm_key", ["enable_working_memory", "working_memory_enabled"])
async def test_latest_done_without_wm_does_not_resurrect_older_summary(session, wm_key):
    for index, enabled in [(1, True), (2, False)]:
        uri = f"{session.uri}/history/archive_{index:03d}"
        await session._viking_fs.write_file(f"{uri}/messages.jsonl", "", ctx=session.ctx)
        await session._viking_fs.write_file(
            f"{uri}/.done", json.dumps({wm_key: enabled}), ctx=session.ctx
        )
        if enabled:
            await session._viking_fs.write_file(f"{uri}/.overview.md", "Old WM", ctx=session.ctx)
    session.add_message("user", [TextPart("current input")])
    context = await session.get_session_context()
    assert context["latest_archive_overview"] == ""
    assert context["stats"]["failedArchives"] == 0
    assert [message["parts"][0]["text"] for message in context["messages"]] == ["current input"]
    archive = await session.get_session_archive("archive_002")
    assert archive["overview"] == archive["abstract"] == ""
    assert archive["messages"] == []
    assert (await session._archives.scan_states())[-1].state == "completed"


@pytest.mark.parametrize("wm_key", ["enable_working_memory", "working_memory_enabled"])
async def test_missing_required_summary_is_failed_for_both_marker_names(session, wm_key):
    uri = f"{session.uri}/history/archive_001"
    await session._viking_fs.write_file(f"{uri}/.done", json.dumps({wm_key: True}), ctx=session.ctx)
    assert (await session._archives.scan_states())[0].state == "failed"
    assert (await session.get_session_context())["stats"]["failedArchives"] == 1
