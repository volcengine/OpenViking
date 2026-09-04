# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0

import json
from unittest.mock import AsyncMock, MagicMock

import pytest

from openviking.message import Message, TextPart
from openviking.session.session import Session, SessionMeta


class _PathLock:
    def __init__(self):
        self.acquired = 0
        self.released = 0

    async def pathlock_acquire_exact(self, path, timeout_secs):
        del path, timeout_secs
        self.acquired += 1
        return "lease-1"

    async def pathlock_release(self, lease):
        assert lease == "lease-1"
        self.released += 1


class _MetaVikingFS:
    def __init__(
        self,
        session_uri: str,
        persisted_meta: SessionMeta,
        existing_uris=None,
    ):
        self.meta_uri = f"{session_uri}/.meta.json"
        self.files = {self.meta_uri: json.dumps(persisted_meta.to_dict(include_internal=True))}
        self._async_agfs = _PathLock()
        self.writes = []
        self.existing_uris = set(existing_uris or [])

    def _uri_to_path(self, uri, ctx=None):
        del uri, ctx
        return "/sessions/session-1"

    async def read_file(self, uri, ctx=None):
        del ctx
        if uri not in self.files:
            raise FileNotFoundError(uri)
        return self.files[uri]

    async def exists(self, uri, ctx=None):
        del ctx
        return uri in self.existing_uris

    async def write_file(self, uri, content, ctx=None, lease_ref=None):
        del ctx
        self.files[uri] = content
        self.writes.append((uri, content, lease_ref))


@pytest.mark.asyncio
async def test_commit_uses_event_tags_from_lock_protected_meta_snapshot(monkeypatch):
    monkeypatch.setattr("openviking.session.session._enabled_memory_types", lambda: set())
    session_uri = "viking://user/default/sessions/session-1"
    persisted_meta = SessionMeta(
        session_id="session-1",
        event_search_tags=["channel=app"],
        pending_usage_records=[
            {"uri": "viking://resources/context-1", "type": "context"},
            {
                "uri": "viking://skills/skill-1",
                "type": "skill",
                "input": "sensitive skill input",
                "output": "sensitive skill output",
                "success": False,
            },
        ],
    )
    viking_fs = _MetaVikingFS(session_uri, persisted_meta)
    session = Session(
        viking_fs=viking_fs,
        session_id="session-1",
        session_uri=session_uri,
    )
    session.meta.event_search_tags = ["channel=web"]
    archived_message = Message(
        id="message-1",
        role="user",
        parts=[TextPart("I want a refund")],
    )
    monkeypatch.setattr(
        session,
        "_read_live_messages_strict",
        AsyncMock(return_value=[archived_message]),
    )
    monkeypatch.setattr(session, "_list_archive_refs", AsyncMock(return_value=[]))

    captured_queue_message = {}

    captured_usage_events = []
    persisted_usage_events_at_marker = []

    async def capture_phase1_marker(archive_uri, *, queue_message, usage_events, **kwargs):
        del archive_uri, kwargs
        captured_queue_message.update(queue_message)
        captured_usage_events.extend(usage_events)
        persisted_meta_at_marker = SessionMeta.from_dict(
            json.loads(viking_fs.files[viking_fs.meta_uri])
        )
        persisted_usage_events_at_marker.extend(persisted_meta_at_marker.pending_usage_records)
        raise RuntimeError("stop after queue snapshot")

    monkeypatch.setattr(session, "_write_phase1_marker", capture_phase1_marker)
    monkeypatch.setattr(session, "_write_failed_marker", AsyncMock())

    with pytest.raises(RuntimeError, match="stop after queue snapshot"):
        await session.commit_async()

    assert captured_queue_message["event_search_tags"] == ["channel=app"]
    assert captured_queue_message["usage_uris"] == [
        "viking://resources/context-1",
        "viking://skills/skill-1",
    ]
    assert captured_usage_events[0]["event_id"]
    assert captured_usage_events[0]["uri"] == "viking://resources/context-1"
    assert all(set(event) == {"event_id", "uri", "type"} for event in captured_usage_events)
    assert persisted_usage_events_at_marker == captured_usage_events
    assert viking_fs._async_agfs.acquired == 1
    assert viking_fs._async_agfs.released == 1


@pytest.mark.asyncio
async def test_update_config_updates_policy_and_tags_in_one_locked_write():
    session_uri = "viking://user/default/sessions/session-1"
    persisted_meta = SessionMeta(
        session_id="session-1",
        message_count=41,
        pending_tokens=8200,
        auto_commit_policy={
            "pending_token_threshold": 8000,
            "message_count_threshold": 40,
        },
        event_search_tags=["channel=web"],
    )
    viking_fs = _MetaVikingFS(session_uri, persisted_meta)
    session = Session(
        viking_fs=viking_fs,
        session_id="session-1",
        session_uri=session_uri,
    )

    await session.update_config(
        event_search_tags=["channel=app"],
        auto_commit_policy={
            "pending_token_threshold": 8000,
            "message_count_threshold": 25,
            "idle_timeout_seconds": 86400,
            "keep_recent_count": 2,
            "min_commit_interval_seconds": 0,
        },
    )

    saved_meta = SessionMeta.from_dict(json.loads(viking_fs.files[viking_fs.meta_uri]))
    assert saved_meta.event_search_tags == ["channel=app"]
    assert saved_meta.auto_commit_policy["message_count_threshold"] == 25
    assert saved_meta.message_count == 41
    assert saved_meta.pending_tokens == 8200
    assert len(viking_fs.writes) == 1
    assert viking_fs.writes[0][2] is None
    assert viking_fs._async_agfs.acquired == 1
    assert viking_fs._async_agfs.released == 1


@pytest.mark.asyncio
async def test_used_accumulates_across_recreated_session_instances():
    session_uri = "viking://user/default/sessions/session-1"
    viking_fs = _MetaVikingFS(session_uri, SessionMeta(session_id="session-1"))

    first_request = Session(
        viking_fs=viking_fs,
        session_id="session-1",
        session_uri=session_uri,
    )
    await first_request.used_async(
        contexts=["viking://resources/context-1"],
        skill={
            "uri": "viking://skills/skill-1",
            "input": "sensitive skill input",
            "output": "sensitive skill output",
            "success": False,
        },
    )

    second_request = Session(
        viking_fs=viking_fs,
        session_id="session-1",
        session_uri=session_uri,
    )
    await second_request.used_async(
        contexts=["viking://resources/context-2", "viking://resources/context-3"],
        skill={"uri": "viking://skills/skill-2"},
    )

    assert second_request.stats.contexts_used == 3
    assert second_request.stats.skills_used == 2
    saved_meta = SessionMeta.from_dict(json.loads(viking_fs.files[viking_fs.meta_uri]))
    assert len(saved_meta.pending_usage_records) == 5
    assert all(
        set(event) == {"event_id", "uri", "type"} for event in saved_meta.pending_usage_records
    )
    assert "pending_usage_records" not in saved_meta.to_dict()


@pytest.mark.asyncio
async def test_ready_marker_failure_restores_persisted_pending_usage(monkeypatch):
    monkeypatch.setattr("openviking.session.session._enabled_memory_types", lambda: set())
    session_uri = "viking://user/default/sessions/session-1"
    persisted_meta = SessionMeta(
        session_id="session-1",
        pending_usage_records=[{"uri": "viking://resources/context-1", "type": "context"}],
    )
    viking_fs = _MetaVikingFS(session_uri, persisted_meta)
    session = Session(
        viking_fs=viking_fs,
        session_id="session-1",
        session_uri=session_uri,
    )
    archived_message = Message(
        id="message-1",
        role="user",
        parts=[TextPart("archive me")],
    )
    monkeypatch.setattr(
        session,
        "_read_live_messages_strict",
        AsyncMock(return_value=[archived_message]),
    )
    monkeypatch.setattr(session, "_list_archive_refs", AsyncMock(return_value=[]))
    monkeypatch.setattr(session, "_write_phase1_marker", AsyncMock())
    monkeypatch.setattr(session, "_write_to_agfs_async", AsyncMock())
    monkeypatch.setattr(
        session,
        "_write_phase1_ready_marker",
        AsyncMock(side_effect=RuntimeError("ready marker unavailable")),
    )
    monkeypatch.setattr(session, "_write_failed_marker", AsyncMock())

    queue_manager = AsyncMock()
    task_tracker = AsyncMock()
    monkeypatch.setattr(
        "openviking.storage.queuefs.get_queue_manager",
        lambda: queue_manager,
    )
    monkeypatch.setattr(
        "openviking.service.task_tracker.get_task_tracker",
        lambda: task_tracker,
    )

    with pytest.raises(RuntimeError, match="ready marker unavailable"):
        await session.commit_async()

    reloaded_meta = SessionMeta.from_dict(json.loads(viking_fs.files[viking_fs.meta_uri]))
    assert [item["uri"] for item in reloaded_meta.pending_usage_records] == [
        "viking://resources/context-1"
    ]


@pytest.mark.asyncio
async def test_restart_reconciles_usage_when_failed_marker_write_fails_after_restore(
    monkeypatch,
):
    monkeypatch.setattr("openviking.session.session._enabled_memory_types", lambda: set())
    session_uri = "viking://user/default/sessions/session-1"
    archive_uri = f"{session_uri}/history/archive_001"
    persisted_meta = SessionMeta(
        session_id="session-1",
        pending_usage_records=[{"uri": "viking://resources/context-1", "type": "context"}],
    )
    viking_fs = _MetaVikingFS(session_uri, persisted_meta)
    archived_message = Message(
        id="message-1",
        role="user",
        parts=[TextPart("archive me")],
    )
    live_messages = [archived_message]

    first_session = Session(
        viking_fs=viking_fs,
        session_id="session-1",
        session_uri=session_uri,
    )

    async def read_live_messages():
        return list(live_messages)

    async def persist_live_messages(messages=None):
        live_messages[:] = list(messages or [])

    monkeypatch.setattr(first_session, "_read_live_messages_strict", read_live_messages)
    monkeypatch.setattr(first_session, "_write_to_agfs_async", persist_live_messages)
    monkeypatch.setattr(first_session, "_list_archive_refs", AsyncMock(return_value=[]))

    queue_manager = MagicMock()
    queue_manager.enqueue = AsyncMock()
    task_tracker = MagicMock()
    task_tracker.create = AsyncMock()
    task_tracker.has_work.return_value = True
    monkeypatch.setattr(
        "openviking.storage.queuefs.get_queue_manager",
        lambda: queue_manager,
    )
    monkeypatch.setattr(
        "openviking.service.task_tracker.get_task_tracker",
        lambda: task_tracker,
    )

    ready_marker_failure = AsyncMock(side_effect=RuntimeError("ready marker unavailable"))
    failed_marker_failure = AsyncMock(side_effect=RuntimeError("failed marker unavailable"))
    monkeypatch.setattr(first_session, "_write_phase1_ready_marker", ready_marker_failure)
    monkeypatch.setattr(first_session, "_write_failed_marker", failed_marker_failure)

    with pytest.raises(RuntimeError, match="ready marker unavailable"):
        await first_session.commit_async()

    restored_meta = SessionMeta.from_dict(json.loads(viking_fs.files[viking_fs.meta_uri]))
    assert len(restored_meta.pending_usage_records) == 1
    event_id = restored_meta.pending_usage_records[0]["event_id"]
    assert event_id
    assert live_messages == []
    assert f"{archive_uri}/.failed.json" not in viking_fs.files
    interrupted_phase1 = json.loads(viking_fs.files[f"{archive_uri}/.meta.json"])["phase1"]
    assert interrupted_phase1["status"] == "preparing"
    assert interrupted_phase1["usage_events"][0]["event_id"] == event_id
    ready_marker_failure.assert_awaited_once()
    failed_marker_failure.assert_awaited_once()

    restarted_session = Session(
        viking_fs=viking_fs,
        session_id="session-1",
        session_uri=session_uri,
    )
    monkeypatch.setattr(restarted_session, "_read_live_messages_strict", read_live_messages)

    assert await restarted_session._ensure_phase1_ready(archive_uri)

    recovered_meta = SessionMeta.from_dict(json.loads(viking_fs.files[viking_fs.meta_uri]))
    assert recovered_meta.pending_usage_records == []
    recovered_phase1 = json.loads(viking_fs.files[f"{archive_uri}/.meta.json"])["phase1"]
    assert recovered_phase1["status"] == "ready"
    assert recovered_phase1["usage_events"][0]["event_id"] == event_id


def _phase1_marker_with_usage(*, event_id: str) -> dict:
    return {
        "status": "preparing",
        "queue_message": {"task_id": "task-1"},
        "original_message_ids": ["message-1"],
        "archived_message_ids": ["message-1"],
        "retained_message_ids": [],
        "usage_events": [
            {
                "event_id": event_id,
                "uri": "viking://resources/context-1",
                "type": "context",
            }
        ],
    }


@pytest.mark.asyncio
async def test_phase1_recovery_consumes_usage_when_root_rewrite_is_durable(
    monkeypatch,
):
    session_uri = "viking://user/default/sessions/session-1"
    event_id = "usage-event-1"
    persisted_meta = SessionMeta(
        session_id="session-1",
        pending_usage_records=[
            {
                "event_id": event_id,
                "uri": "viking://resources/context-1",
                "type": "context",
            }
        ],
    )
    viking_fs = _MetaVikingFS(session_uri, persisted_meta)
    session = Session(
        viking_fs=viking_fs,
        session_id="session-1",
        session_uri=session_uri,
    )
    marker = _phase1_marker_with_usage(event_id=event_id)
    monkeypatch.setattr(session, "_read_phase1_meta", AsyncMock(return_value=marker))
    monkeypatch.setattr(session, "_archive_file_exists", AsyncMock(return_value=False))
    monkeypatch.setattr(session, "_read_live_messages_strict", AsyncMock(return_value=[]))
    ready_marker = AsyncMock()
    monkeypatch.setattr(session, "_write_phase1_ready_marker", ready_marker)
    tracker = MagicMock()
    tracker.has_work.return_value = True
    monkeypatch.setattr(
        "openviking.service.task_tracker.get_task_tracker",
        lambda: tracker,
    )

    assert await session._ensure_phase1_ready(f"{session_uri}/history/archive_001")

    saved_meta = SessionMeta.from_dict(json.loads(viking_fs.files[viking_fs.meta_uri]))
    assert saved_meta.pending_usage_records == []
    ready_marker.assert_awaited_once()


@pytest.mark.asyncio
async def test_phase1_recovery_restores_usage_before_failing_original_root(
    monkeypatch,
):
    session_uri = "viking://user/default/sessions/session-1"
    event_id = "usage-event-1"
    viking_fs = _MetaVikingFS(session_uri, SessionMeta(session_id="session-1"))
    session = Session(
        viking_fs=viking_fs,
        session_id="session-1",
        session_uri=session_uri,
    )
    marker = _phase1_marker_with_usage(event_id=event_id)
    original_message = Message(
        id="message-1",
        role="user",
        parts=[TextPart("still live")],
    )
    monkeypatch.setattr(session, "_read_phase1_meta", AsyncMock(return_value=marker))
    monkeypatch.setattr(session, "_archive_file_exists", AsyncMock(return_value=False))
    monkeypatch.setattr(
        session,
        "_read_live_messages_strict",
        AsyncMock(return_value=[original_message]),
    )
    failed_marker = AsyncMock()
    monkeypatch.setattr(session, "_write_failed_marker", failed_marker)
    tracker = MagicMock()
    tracker.has_work.return_value = True
    monkeypatch.setattr(
        "openviking.service.task_tracker.get_task_tracker",
        lambda: tracker,
    )

    assert not await session._ensure_phase1_ready(f"{session_uri}/history/archive_001")

    saved_meta = SessionMeta.from_dict(json.loads(viking_fs.files[viking_fs.meta_uri]))
    assert [item["event_id"] for item in saved_meta.pending_usage_records] == [event_id]
    failed_marker.assert_awaited_once()
    assert viking_fs.writes[-1][0] == viking_fs.meta_uri
    assert viking_fs.writes[-1][2] is None
    assert "lease_ref" not in failed_marker.await_args.kwargs

    # Recovery is idempotent: retrying restoration cannot duplicate the event.
    await session._restore_phase1_usage(marker)
    saved_meta = SessionMeta.from_dict(json.loads(viking_fs.files[viking_fs.meta_uri]))
    assert [item["event_id"] for item in saved_meta.pending_usage_records] == [event_id]


@pytest.mark.asyncio
async def test_next_commit_reconciles_preparing_usage_before_snapshot(monkeypatch):
    monkeypatch.setattr("openviking.session.session._enabled_memory_types", lambda: set())
    session_uri = "viking://user/default/sessions/session-1"
    predecessor_uri = f"{session_uri}/history/archive_001"
    event_id = "usage-event-1"
    persisted_meta = SessionMeta(
        session_id="session-1",
        commit_count=1,
        pending_usage_records=[
            {
                "event_id": event_id,
                "uri": "viking://resources/context-1",
                "type": "context",
            }
        ],
    )
    viking_fs = _MetaVikingFS(
        session_uri,
        persisted_meta,
        existing_uris={predecessor_uri},
    )
    predecessor_marker = _phase1_marker_with_usage(event_id=event_id)
    viking_fs.files[f"{predecessor_uri}/.meta.json"] = json.dumps({"phase1": predecessor_marker})
    session = Session(
        viking_fs=viking_fs,
        session_id="session-1",
        session_uri=session_uri,
    )
    new_message = Message(
        id="message-2",
        role="user",
        parts=[TextPart("new turn")],
    )
    monkeypatch.setattr(
        session,
        "_read_live_messages_strict",
        AsyncMock(return_value=[new_message]),
    )
    monkeypatch.setattr(session, "_write_failed_marker", AsyncMock())
    tracker = MagicMock()
    tracker.has_work.return_value = True
    monkeypatch.setattr(
        "openviking.service.task_tracker.get_task_tracker",
        lambda: tracker,
    )

    captured_next_usage_events = []

    async def capture_next_marker(archive_uri, *, usage_events, **kwargs):
        del kwargs
        if archive_uri == predecessor_uri:
            raise AssertionError("predecessor should be recovered, not recreated")
        captured_next_usage_events.extend(usage_events)
        raise RuntimeError("stop at next snapshot")

    original_ready_marker = session._write_phase1_ready_marker

    async def write_predecessor_ready(archive_uri, lease_ref=None):
        assert archive_uri == predecessor_uri
        await original_ready_marker(archive_uri, lease_ref=lease_ref)

    monkeypatch.setattr(session, "_write_phase1_marker", capture_next_marker)
    monkeypatch.setattr(session, "_write_phase1_ready_marker", write_predecessor_ready)

    with pytest.raises(RuntimeError, match="stop at next snapshot"):
        await session.commit_async()

    assert captured_next_usage_events == []
    saved_meta = SessionMeta.from_dict(json.loads(viking_fs.files[viking_fs.meta_uri]))
    assert saved_meta.pending_usage_records == []


@pytest.mark.asyncio
async def test_phase1_recovery_propagates_marker_read_failure(monkeypatch):
    session_uri = "viking://user/default/sessions/session-1"
    archive_uri = f"{session_uri}/history/archive_001"
    viking_fs = _MetaVikingFS(session_uri, SessionMeta(session_id="session-1"))
    original_read_file = viking_fs.read_file

    async def fail_archive_meta_read(uri, ctx=None):
        if uri == f"{archive_uri}/.meta.json":
            raise RuntimeError("archive meta unavailable")
        return await original_read_file(uri, ctx=ctx)

    monkeypatch.setattr(viking_fs, "read_file", fail_archive_meta_read)
    session = Session(
        viking_fs=viking_fs,
        session_id="session-1",
        session_uri=session_uri,
    )

    with pytest.raises(RuntimeError, match="archive meta unavailable"):
        await session._ensure_phase1_ready(archive_uri)

    assert viking_fs._async_agfs.acquired == 0
