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

    async def pathlock_acquire_tree(self, path, timeout_secs):
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

    async def write_file(self, uri, content, ctx=None, lease_ref=None):
        del ctx
        self.files[uri] = content
        self.writes.append((uri, content, lease_ref))


@pytest.mark.asyncio
async def test_commit_uses_event_tags_from_lock_protected_meta_snapshot(monkeypatch):
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
    monkeypatch.setattr(session, "_list_archive_refs", AsyncMock(return_value=[]))

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
    assert viking_fs.writes[0][2] == "lease-1"
    assert viking_fs._async_agfs.acquired == 1
    assert viking_fs._async_agfs.released == 1
