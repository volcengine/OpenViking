# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0

import json
from unittest.mock import AsyncMock

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

    async def pathlock_acquire_exact_batch(self, paths, timeout_secs, owner_lease_ref):
        self.acquired += 1
        return "file-lease"

    async def pathlock_release(self, lease):
        assert lease in {"lease-1", "file-lease"}
        self.released += 1


class _MetaVikingFS:
    def __init__(
        self,
        session_uri: str,
        persisted_meta: SessionMeta,
    ):
        self.meta_uri = f"{session_uri}/.meta.json"
        self.files = {self.meta_uri: json.dumps(persisted_meta.to_dict())}
        self._async_agfs = _PathLock()
        self.writes = []

    def _uri_to_path(self, uri, ctx=None):
        del uri, ctx
        return "/sessions/session-1"

    async def read_file(self, uri, ctx=None):
        del ctx
        if uri not in self.files:
            raise FileNotFoundError(uri)
        return self.files[uri]

    async def exists(self, uri, ctx=None):
        return uri in self.files

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
    monkeypatch.setattr(session._archives, "list_refs", AsyncMock(return_value=[]))

    captured_queue_message = {}

    async def capture_phase1_marker(archive_uri, *, queue_message, **kwargs):
        del archive_uri, kwargs
        captured_queue_message.update(queue_message)
        raise RuntimeError("stop after queue snapshot")

    monkeypatch.setattr(session, "_write_phase1_marker", capture_phase1_marker)
    monkeypatch.setattr(session, "_write_failed_marker", AsyncMock())

    with pytest.raises(RuntimeError, match="stop after queue snapshot"):
        await session.commit_async()

    assert captured_queue_message["event_search_tags"] == ["channel=app"]
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
    assert viking_fs._async_agfs.acquired == viking_fs._async_agfs.released


@pytest.mark.asyncio
async def test_idle_commit_rechecks_latest_activity_under_phase1_lock(monkeypatch):
    from openviking.service.session_service import SessionService
    from openviking_cli.utils.config.memory_config import SessionAutoCommitConfig

    uri = "viking://user/default/sessions/session-1"
    policy = {"idle_timeout_seconds": 60}
    persisted = SessionMeta(
        session_id="session-1",
        auto_commit_policy=policy,
        message_count=1,
        last_message_at="2099-01-01T00:00:00+00:00",
    )
    fs = _MetaVikingFS(uri, persisted)
    session = Session(viking_fs=fs, session_id="session-1", session_uri=uri)
    session.meta.auto_commit_policy = policy
    session.meta.message_count = 1
    session.meta.last_message_at = "2000-01-01T00:00:00+00:00"
    monkeypatch.setattr(
        session,
        "_read_live_messages_strict",
        AsyncMock(
            return_value=[
                Message(id="new", role="user", parts=[TextPart("new activity")]),
            ]
        ),
    )
    service = SessionService()
    service.set_session_auto_commit_config(SessionAutoCommitConfig(enabled=True))
    monkeypatch.setattr(service, "get", AsyncMock(return_value=session))
    tracker = AsyncMock()
    tracker.has_running.return_value = False
    monkeypatch.setattr("openviking.service.session_service.get_task_tracker", lambda: tracker)

    result = await service.run_auto_commit("session-1", session.ctx, reason="idle_timeout")
    assert result["archived"] is False
    assert result["idle_auto_commit_at"] == "2099-01-01T00:01:00+00:00"
    assert fs.writes == []
    assert fs._async_agfs.acquired == fs._async_agfs.released == 1


@pytest.mark.asyncio
async def test_conditional_commit_does_not_use_stale_meta_after_read_failure(monkeypatch):
    uri = "viking://user/default/sessions/session-1"
    fs = _MetaVikingFS(uri, SessionMeta(session_id="session-1"))
    session = Session(viking_fs=fs, session_id="session-1", session_uri=uri)
    monkeypatch.setattr(session, "_read_live_messages_strict", AsyncMock(return_value=[]))
    monkeypatch.setattr(fs, "read_file", AsyncMock(side_effect=OSError("storage unavailable")))
    checks = []
    with pytest.raises(OSError, match="storage unavailable"):
        await session.commit_async(pre_commit_check=lambda current: checks.append(current))
    assert checks == [] and fs.writes == []
    assert fs._async_agfs.acquired == fs._async_agfs.released == 1
