# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0

import json
from unittest.mock import AsyncMock

import pytest

from openviking.message import Message, TextPart
from openviking.service.task_tracker import TaskTracker, set_task_tracker
from openviking.session.session import Session
from openviking.storage.queuefs.session_commit_msg import SessionCommitMsg


class _TaskStore:
    def __init__(self):
        self.tasks = {}

    async def create(self, task):
        self.tasks[task.task_id] = task

    async def update(self, task):
        self.tasks[task.task_id] = task

    async def get(self, task_id, *, account_id=None, user_id=None):
        return None

    async def list(self, account_id, *, user_id=None):
        return []

    async def delete(self, task_id, *, account_id, user_id=None):
        self.tasks.pop(task_id, None)


class _MemoryVikingFS:
    def __init__(self, files):
        self.files = files

    def _uri_to_path(self, uri, ctx=None):
        return "/local/session-1"

    async def read_file(self, uri, ctx=None):
        if uri not in self.files:
            raise FileNotFoundError(uri)
        return self.files[uri]

    async def write_file(self, uri, content, ctx=None):
        self.files[uri] = content


class _VerifyFailureVikingFS(_MemoryVikingFS):
    def __init__(self, files, live_uri):
        super().__init__(files)
        self.live_uri = live_uri
        self.live_reads = 0

    async def read_file(self, uri, ctx=None):
        if uri == self.live_uri:
            self.live_reads += 1
            if self.live_reads == 2:
                raise OSError("transient verification read failure")
        return await super().read_file(uri, ctx=ctx)


@pytest.mark.asyncio
async def test_live_message_verification_rejects_silently_ignored_empty_write():
    session_uri = "viking://user/sessions/session-1"
    stale = Message(id="stale", role="user", parts=[TextPart("old active message")])
    files = {f"{session_uri}/messages.jsonl": f"{stale.to_jsonl()}\n"}
    session = Session(
        viking_fs=_MemoryVikingFS(files), session_id="session-1", session_uri=session_uri
    )

    with pytest.raises(RuntimeError, match="persistence verification failed"):
        await session._verify_live_messages_persisted([])


@pytest.mark.asyncio
async def test_commit_restores_disk_live_messages_when_verification_fails(
    monkeypatch, tmp_path
):
    session_uri = "viking://user/sessions/session-1"
    live_uri = f"{session_uri}/messages.jsonl"
    original = Message(id="original", role="user", parts=[TextPart("keep me")])
    original_content = f"{original.to_jsonl()}\n"
    files = {live_uri: original_content}
    viking_fs = _VerifyFailureVikingFS(files, live_uri)
    session = Session(viking_fs=viking_fs, session_id="session-1", session_uri=session_uri)
    session._messages = [original]

    class _LockContext:
        async def __aenter__(self):
            return self

        async def __aexit__(self, exc_type, exc, traceback):
            return False

    monkeypatch.setattr(
        "openviking.storage.transaction.LockContext", lambda *args, **kwargs: _LockContext()
    )
    monkeypatch.setattr("openviking.storage.transaction.get_lock_manager", lambda: object())

    with pytest.raises(OSError, match="transient verification read failure"):
        await session.commit_async()

    assert session._messages == [original]
    assert session._compression.compression_index == 0
    assert files[live_uri] == original_content


@pytest.mark.asyncio
async def test_resume_queued_commit_continues_phase2(monkeypatch):
    session_uri = "viking://user/sessions/session-1"
    archive_uri = f"{session_uri}/history/archive_001"
    archived = Message(id="archived", role="user", parts=[TextPart("old")])
    retained = Message(id="retained", role="assistant", parts=[TextPart("new")])
    files = {
        f"{session_uri}/messages.jsonl": f"{retained.to_jsonl()}\n",
        f"{session_uri}/.meta.json": json.dumps(
            {"session_id": "session-1", "message_count": 1, "commit_count": 1}
        ),
        f"{archive_uri}/messages.jsonl": f"{archived.to_jsonl()}\n",
    }
    viking_fs = _MemoryVikingFS(files)
    session = Session(viking_fs=viking_fs, session_id="session-1", session_uri=session_uri)
    tracker = TaskTracker(_TaskStore())
    set_task_tracker(tracker)
    monkeypatch.setattr(session, "_run_memory_extraction", AsyncMock())
    message = SessionCommitMsg(
        task_id="task-1",
        session_id="session-1",
        session_uri=session_uri,
        archive_uri=archive_uri,
        user={"account_id": "default", "user_id": "default"},
    )

    try:
        await session.resume_queued_commit(message)
    finally:
        set_task_tracker(None)

    session._run_memory_extraction.assert_awaited_once()
    assert session._run_memory_extraction.await_args.kwargs["task_id"] == "task-1"
    assert [
        item.id for item in session._run_memory_extraction.await_args.kwargs["messages"]
    ] == ["archived"]
