# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0

import asyncio
import json
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from openviking.message import Message, TextPart
from openviking.observability.context import get_root_observability_context
from openviking.server.identity import RequestContext, Role
from openviking.service.task_tracker import TaskTracker, set_task_tracker
from openviking.session.session import Session
from openviking.storage.queuefs.session_commit_msg import SessionCommitMsg
from openviking.storage.queuefs.session_commit_processor import SessionCommitProcessor
from openviking_cli.session.user_id import UserIdentifier


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


@pytest.mark.asyncio
async def test_session_commit_consumer_restores_identity():
    observed = {}

    async def resume(_message):
        root = get_root_observability_context()
        observed.update(account_id=root.account_id, user_id=root.user_id)

    session = SimpleNamespace(
        exists=AsyncMock(return_value=True),
        load=AsyncMock(),
        resume_queued_commit=resume,
    )
    service = SimpleNamespace(session=lambda *_args, **_kwargs: session)
    processor = SessionCommitProcessor(service, asyncio.get_running_loop())
    message = SessionCommitMsg(
        task_id="task-1",
        session_id="session-1",
        session_uri="viking://user/sessions/session-1",
        archive_uri="viking://user/sessions/session-1/history/archive-1",
        user={"account_id": "account-1", "user_id": "user-1"},
    )
    ctx = RequestContext(user=UserIdentifier("account-1", "user-1"), role=Role.USER)

    await processor._process(message, ctx)

    assert observed == {"account_id": "account-1", "user_id": "user-1"}
    assert get_root_observability_context() is None
