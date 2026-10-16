"""A deferred or terminal archive must not read the live conversation."""

import json
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import pytest

from openviking.session.session import Session
from openviking.storage.queuefs.session_commit_msg import SessionCommitMsg
from openviking.storage.queuefs.session_commit_processor import SessionCommitProcessor
from tests.unit.session.test_session_commit_resume import _MemoryVikingFS


@pytest.mark.asyncio
@pytest.mark.parametrize("state", ["waiting", "done", "failed", "ready"])
async def test_queue_loads_live_session_only_after_predecessor_is_ready(monkeypatch, state):
    uri = "viking://user/alice/sessions/s"
    archive = uri + "/history/archive_002"
    files = {uri + "/.meta.json": "{}"}
    if state in {"done", "failed"}:
        files[archive + ("/.done" if state == "done" else "/.failed.json")] = "{}"
    session = Session(viking_fs=_MemoryVikingFS(files), session_id="s", session_uri=uri)
    session.exists = AsyncMock(return_value=True)
    session.load = AsyncMock()
    session._ensure_phase1_ready = AsyncMock(return_value=True)
    session._can_run_archive = AsyncMock(return_value=state == "ready")
    session._archives.read_messages = AsyncMock(return_value=[SimpleNamespace(id="m")])
    session._archives.read_meta = AsyncMock(return_value={})
    session._run_memory_extraction = AsyncMock()
    tracker = SimpleNamespace(
        create=AsyncMock(return_value=SimpleNamespace(status=SimpleNamespace(value="pending"))),
        fail=AsyncMock(),
        complete=AsyncMock(),
    )
    monkeypatch.setattr("openviking.service.task_tracker.get_task_tracker", lambda: tracker)
    monkeypatch.setattr(
        "openviking.storage.queuefs.session_commit_processor.get_task_tracker", lambda: tracker
    )
    monkeypatch.setattr(
        "openviking.storage.queuefs.get_queue_manager", lambda: SimpleNamespace(enqueue=AsyncMock())
    )
    processor = SessionCommitProcessor(SimpleNamespace(session=Mock(return_value=session)))
    msg = SessionCommitMsg(
        task_id="t",
        session_id="s",
        session_uri=uri,
        archive_uri=archive,
        user={"account_id": "default", "user_id": "alice"},
    )
    result = await processor.on_dequeue({"data": json.dumps(msg.to_dict())})
    assert session.load.await_count == (1 if state == "ready" else 0)
    assert session._run_memory_extraction.await_count == (1 if state == "ready" else 0)
    assert result.outcome.value == ("requeued" if state == "waiting" else "success")
