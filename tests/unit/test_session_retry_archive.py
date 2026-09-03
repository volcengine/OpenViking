# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0
"""Unit tests for legacy session commit archive resolution."""

import json
from hashlib import sha256
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from openviking.session.session import Session

SESSION_URI = "viking://user/alice/sessions/session-1"
ARCHIVE_URI = f"{SESSION_URI}/history/archive_003"
FAILED_TASK_CREATED_AT = 1786994253.298716


class _AsyncAgfs:
    async def pathlock_acquire_tree(self, _path, timeout_secs):
        assert timeout_secs > 0
        return "lease"

    async def pathlock_release(self, lease):
        assert lease == "lease"


class _VikingFS:
    def __init__(self):
        self._async_agfs = _AsyncAgfs()

    async def glob(self, pattern, *, uri, ctx):
        assert pattern == "archive_*/messages.jsonl"
        assert uri == f"{SESSION_URI}/history"
        assert ctx is not None
        return {"matches": [f"{ARCHIVE_URI}/messages.jsonl"]}

    async def read_file(self, uri, *, ctx):
        assert uri == f"{ARCHIVE_URI}/.meta.json"
        assert ctx is not None
        return json.dumps(
            {
                "phase1": {
                    "created_at": "2026-08-17T19:17:33.276Z",
                    "queue_message": {"task_id": "offline-recovery-task"},
                }
            }
        )

    async def exists(self, uri, *, ctx):
        assert ctx is not None
        return uri == f"{ARCHIVE_URI}/.done"

    def _uri_to_path(self, uri, *, ctx):
        assert uri == SESSION_URI
        assert ctx is not None
        return "/session-1"


def _session() -> Session:
    session = Session.__new__(Session)
    session._viking_fs = _VikingFS()
    session._session_uri = SESSION_URI
    session.session_id = "session-1"
    session.ctx = SimpleNamespace()
    return session


@pytest.mark.asyncio
async def test_legacy_archive_is_matched_by_creation_time_after_queue_id_replacement():
    state = await _session().inspect_failed_commit(
        "legacy-failed-task",
        failed_task_created_at=FAILED_TASK_CREATED_AT,
    )

    assert state == {"state": "completed", "archive_uri": ARCHIVE_URI}


@pytest.mark.asyncio
async def test_retry_returns_resolved_when_legacy_archive_is_already_complete():
    result = await _session().retry_failed_commit(
        "legacy-failed-task",
        failed_task_created_at=FAILED_TASK_CREATED_AT,
    )

    assert result == {
        "session_id": "session-1",
        "status": "completed",
        "task_id": None,
        "archive_uri": ARCHIVE_URI,
        "reason": "archive_complete",
    }


@pytest.mark.asyncio
async def test_retry_rolls_back_safe_history_when_enqueue_fails(monkeypatch):
    events: list[str] = []
    session = Session.__new__(Session)
    session._viking_fs = SimpleNamespace(
        _async_agfs=SimpleNamespace(
            pathlock_acquire_tree=AsyncMock(return_value="lease"),
            pathlock_release=AsyncMock(),
            rm=AsyncMock(),
        ),
        _uri_to_path=lambda uri, **_kwargs: uri,
        _pathlock_fs_ctx=lambda *_args: SimpleNamespace(),
        read_file=AsyncMock(return_value='{"error":"provider overloaded"}'),
        write_file=AsyncMock(),
    )
    session._session_uri = SESSION_URI
    session.session_id = "session-1"
    session.ctx = SimpleNamespace(
        account_id="acme",
        user=SimpleNamespace(user_id="alice"),
    )
    queue_payload = {
        "task_id": "failed-task",
        "session_id": "session-1",
        "session_uri": SESSION_URI,
        "archive_uri": ARCHIVE_URI,
        "user": {"account_id": "acme", "user_id": "alice"},
    }
    session._find_failed_commit_archive = AsyncMock(return_value=(ARCHIVE_URI, queue_payload))
    session._archive_file_exists = AsyncMock(side_effect=[False, True, False])
    session._read_archive_meta = AsyncMock(
        return_value={"phase1": {"queue_message": queue_payload}}
    )
    session._merge_archive_meta = AsyncMock(
        side_effect=lambda *_args, **_kwargs: events.append("merge")
    )

    tracker = SimpleNamespace(
        create=AsyncMock(side_effect=lambda *_args, **_kwargs: events.append("create")),
        fail=AsyncMock(),
    )

    def fail_enqueue(*_args, **_kwargs):
        events.append("enqueue")
        raise RuntimeError("queue unavailable")

    queue_manager = SimpleNamespace(enqueue=AsyncMock(side_effect=fail_enqueue))
    session._viking_fs._async_agfs.rm.side_effect = lambda uri, **_kwargs: events.append(
        f"remove:{uri}"
    )
    monkeypatch.setattr("openviking.service.task_tracker.get_task_tracker", lambda: tracker)
    monkeypatch.setattr("openviking.storage.queuefs.get_queue_manager", lambda: queue_manager)

    failed_task_id = "../failed/task"
    with pytest.raises(RuntimeError, match="queue unavailable"):
        await session.retry_failed_commit(failed_task_id, archive_uri=ARCHIVE_URI)

    tracker.fail.assert_awaited_once()
    retry_task_id, failure_message = tracker.fail.await_args.args
    assert retry_task_id != failed_task_id
    assert failure_message == "Failed to enqueue session commit retry"
    assert tracker.fail.await_args.kwargs == {
        "account_id": "acme",
        "user_id": "alice",
    }
    failure_history_id = sha256(failed_task_id.encode("utf-8")).hexdigest()
    failure_history_uri = f"{ARCHIVE_URI}/.failed.{failure_history_id}.json"
    assert session._viking_fs.write_file.await_args_list[0].args == (
        failure_history_uri,
        '{"error":"provider overloaded"}',
    )
    assert session._viking_fs._async_agfs.rm.await_args_list[-1].args == (failure_history_uri,)
    assert events[:4] == [
        "merge",
        "create",
        f"remove:{ARCHIVE_URI}/.failed.json",
        "enqueue",
    ]
