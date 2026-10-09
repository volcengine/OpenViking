# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0
"""Queued Session work must retain ownership across cleanup and ID reuse."""

import asyncio
import json
from datetime import timedelta
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from openviking.core import ttl
from openviking.message import Message, TextPart
from openviking.service.task_store import PersistentTaskStore
from openviking.service.task_tracker import TaskTracker, set_task_tracker
from openviking.service.ttl_cleanup import TTLCleanupService
from openviking.session.commit_lifetime import StaleSessionCommit
from openviking.session.session import Session
from openviking.storage.directory_ttl import read_directory_fields
from openviking.storage.queuefs.session_commit_msg import SessionCommitMsg
from openviking.utils.time_utils import format_iso8601, parse_iso_datetime
from openviking_cli.utils.config.ttl_config import TTLConfig
from tests.storage.test_transfer_merge_binding import binding_fs as binding_fs
from tests.storage.test_transfer_merge_binding import root_ctx
from tests.unit.service.test_ttl_cleanup import _cleanup_once
from tests.unit.storage.ttl_test_storage import read_record


async def seed_commit(fs, ctx, session, task_id):
    archive = session.uri + "/history/archive_001"
    msg = SessionCommitMsg(
        task_id=task_id,
        session_id=session.session_id,
        session_uri=session.uri,
        archive_uri=archive,
        user=ctx.user.to_dict(),
        memory_policy={"memory_types": []},
    )
    await fs.write_file(
        archive + "/.meta.json",
        json.dumps({"phase1": {"status": "ready", "queue_message": msg.to_dict()}}),
        ctx=ctx,
    )
    await fs.write_file(
        archive + "/messages.jsonl",
        Message(id="message-1", role="user", parts=[TextPart("old dated event")]).to_jsonl(),
        ctx=ctx,
    )
    return msg


@pytest.mark.asyncio
async def test_late_commit_cannot_recreate_event_or_corrupt_reused_session(binding_fs, monkeypatch):
    fs, ctx = binding_fs, root_ctx()
    session = Session(viking_fs=fs, session_id="reused", ctx=ctx)
    await session.ensure_exists()
    msg = await seed_commit(fs, ctx, session, "old-task")
    event = "viking://user/default/memories/events/2020/01/01"
    await fs.write_file(event + "/body.md", "old event", ctx=ctx)
    entered, finish = asyncio.Event(), asyncio.Event()

    async def delayed_extraction(**kwargs):
        entered.set()
        await finish.wait()
        await fs.write_file(event + "/late.md", "late output", ctx=ctx)

    monkeypatch.setattr(session, "_run_memory_extraction", delayed_extraction)
    monkeypatch.setattr(session, "_can_run_archive", AsyncMock(return_value=True))
    tracker = TaskTracker(PersistentTaskStore(fs._async_agfs))
    set_task_tracker(tracker)
    task = asyncio.create_task(session.resume_queued_commit(msg))
    try:
        await asyncio.wait_for(entered.wait(), 10)
        cleanup = TTLCleanupService(service=SimpleNamespace(viking_fs=fs))
        for owner in [event, session.uri]:
            fields = await read_directory_fields(fs, owner, ctx=ctx)
            await fs.write_file(
                owner + "/.meta.json",
                json.dumps({**fields, "expires_at": "2000-01-01T00:00:00Z"}),
                ctx=ctx,
            )
            assert (await _cleanup_once(cleanup, await read_record(fs, ctx.account_id, owner)))[
                "deleted"
            ]
        replacement = Session(viking_fs=fs, session_id="reused", ctx=ctx)
        await replacement.ensure_exists()
        before = await read_directory_fields(fs, replacement.uri, ctx=ctx)
        # The new session may already be committing its own archive_001.
        await seed_commit(fs, ctx, replacement, "new-task")
        finish.set()
        with pytest.raises(StaleSessionCommit):
            await task
        with pytest.raises(StaleSessionCommit):
            await session._merge_and_save_commit_meta(
                archive_index=1,
                archive_uri=msg.archive_uri,
                task_id=msg.task_id,
                memories_extracted={"events": 1},
                telemetry_snapshot=None,
            )
        assert await read_directory_fields(fs, replacement.uri, ctx=ctx) == before
        assert not await fs.exists(event, ctx=ctx, include_expired=True)
        # A fresh commit is allowed to import that historical calendar date.
        new_msg = await seed_commit(fs, ctx, replacement, "new-task")
        monkeypatch.setattr(replacement, "_run_memory_extraction", delayed_extraction)
        monkeypatch.setattr(replacement, "_can_run_archive", AsyncMock(return_value=True))
        assert await replacement.resume_queued_commit(new_msg)
        assert await fs.read_file(event + "/late.md", ctx=ctx) == "late output"
    finally:
        finish.set()
        if not task.done():
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)
        set_task_tracker(None)


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "policy",
    [
        {"mode": "days", "ttl_days": 7},
        {"mode": "absolute", "ttl_absolute": 32503680000},
        {"mode": "disabled"},
    ],
)
async def test_append_commit_and_replay_preserve_deadline(binding_fs, monkeypatch, policy):
    from openviking.session import session as session_module

    fs, ctx = binding_fs, root_ctx()
    config = TTLConfig(sessions=policy)
    monkeypatch.setattr(ttl, "get_openviking_config", lambda: SimpleNamespace(ttl=config))
    session = Session(viking_fs=fs, session_id="current", ctx=ctx)
    await session.ensure_exists()
    before = await read_directory_fields(fs, session.uri, ctx=ctx)
    config.sessions = TTLConfig(sessions={"mode": "days", "ttl_days": 30}).sessions
    completed = format_iso8601(parse_iso_datetime(before["created_at"]) + timedelta(days=1))
    monkeypatch.setattr(session_module, "get_current_timestamp", lambda: completed)
    await session.add_message_async("user", [TextPart("append")])
    appended = await read_directory_fields(fs, session.uri, ctx=ctx)
    assert appended.get("expires_at") == before.get("expires_at")
    assert appended["created_at"] == before["created_at"]
    assert appended["last_message_at"] == completed
    assert {"ttl_days", "received_at"}.isdisjoint(appended)
    await session.add_messages_async([])
    assert await read_directory_fields(fs, session.uri, ctx=ctx) == appended
    msg = await seed_commit(fs, ctx, session, "current-task")
    await session._merge_and_save_commit_meta(
        archive_index=1,
        archive_uri=msg.archive_uri,
        task_id=msg.task_id,
        memories_extracted={"events": 1},
        telemetry_snapshot=None,
    )
    fields = await read_directory_fields(fs, session.uri, ctx=ctx)
    assert fields.get("expires_at") == before.get("expires_at")
    assert fields["created_at"] == before["created_at"]
    assert fields["last_commit_at"] == completed
    assert fields["memories_extracted"]["events"] == 1
    assert fields["commit_count"] == 1
    await session._write_done_file(msg.archive_uri, "message-1", "message-1")
    done = json.loads(await fs.read_file(msg.archive_uri + "/.done", ctx=ctx))
    assert "phase2_completed_at" not in done
    tracker = TaskTracker(PersistentTaskStore(fs._async_agfs))
    set_task_tracker(tracker)
    try:
        assert await session.resume_queued_commit(msg)
    finally:
        set_task_tracker(None)
    assert await read_directory_fields(fs, session.uri, ctx=ctx) == fields


@pytest.mark.asyncio
async def test_expired_queued_commit_never_starts_extraction(binding_fs, monkeypatch):
    fs, ctx = binding_fs, root_ctx()
    session = Session(viking_fs=fs, session_id="expired", ctx=ctx)
    await session.ensure_exists()
    msg = await seed_commit(fs, ctx, session, "expired-task")
    await fs.write_file(
        session.uri + "/.meta.json", '{"expires_at":"2000-01-01T00:00:00Z"}', ctx=ctx
    )
    extract = AsyncMock()
    monkeypatch.setattr(session, "_run_memory_extraction", extract)
    with pytest.raises(StaleSessionCommit):
        await session.resume_queued_commit(msg)
    extract.assert_not_awaited()
