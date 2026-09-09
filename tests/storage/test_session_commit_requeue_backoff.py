# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0
"""Dependency-wait requeues must back off instead of hot-looping (issue #4345).

A later Session Phase 2 commit whose predecessor is still pending was
re-enqueued immediately with no delay, dedup or retry limit — one deployment
observed >800k requeues behind ~33 waiting commits.
"""

from __future__ import annotations

import asyncio
from typing import Any, Dict, List

import pytest

from openviking.storage.queuefs import session_commit_processor as scp_module
from openviking.storage.queuefs.session_commit_processor import SessionCommitProcessor
from openviking.storage.queuefs.session_commit_msg import SessionCommitMsg


def _msg(session_id: str = "sess-1", archive_uri: str = "viking://a/1") -> SessionCommitMsg:
    return SessionCommitMsg(
        task_id="task-1",
        session_id=session_id,
        session_uri="viking://s/1",
        archive_uri=archive_uri,
        user={"user_id": "u", "account_id": "a"},
    )


def _processor() -> SessionCommitProcessor:
    return SessionCommitProcessor(session_service=None, service_loop=asyncio.get_event_loop())


class _StubQueueManager:
    def __init__(self) -> None:
        self.enqueued: List[Dict[str, Any]] = []

    async def enqueue(self, queue_name: str, data: Any) -> str:
        self.enqueued.append({"queue": queue_name, "data": data})
        return "id"


def test_backoff_delay_is_exponential_and_capped() -> None:
    delays = [SessionCommitProcessor._dep_wait_delay_s(n) for n in range(0, 12)]
    assert delays[0] == pytest.approx(SessionCommitProcessor.DEP_WAIT_BASE_S)
    assert delays[1] == pytest.approx(SessionCommitProcessor.DEP_WAIT_BASE_S * 2)
    # growth stops at the cap
    assert delays[-1] == pytest.approx(SessionCommitProcessor.DEP_WAIT_MAX_S)
    assert all(d <= SessionCommitProcessor.DEP_WAIT_MAX_S + 1e-9 for d in delays)


@pytest.mark.asyncio
async def test_requeue_enqueues_once_and_registers_backoff(monkeypatch: pytest.MonkeyPatch) -> None:
    stub = _StubQueueManager()
    monkeypatch.setattr("openviking.storage.queuefs.get_queue_manager", lambda: stub)
    requeues: List[str] = []
    processor = _processor()
    processor.report_requeue = lambda: requeues.append("x")  # type: ignore[method-assign]

    msg = _msg()
    await processor._requeue_with_backoff(msg)

    assert len(stub.enqueued) == 1
    assert stub.enqueued[0]["queue"] == "SessionCommit"
    assert stub.enqueued[0]["data"]["session_id"] == "sess-1"
    assert requeues == ["x"]
    attempt, _next = processor._dep_wait[processor._dep_wait_key(msg)]
    assert attempt == 1


@pytest.mark.asyncio
async def test_repeated_waits_backoff_instead_of_hot_looping(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    stub = _StubQueueManager()
    monkeypatch.setattr("openviking.storage.queuefs.get_queue_manager", lambda: stub)
    monkeypatch.setattr(SessionCommitProcessor, "DEP_WAIT_BASE_S", 0.01)
    monkeypatch.setattr(SessionCommitProcessor, "DEP_WAIT_MAX_S", 0.05)
    monkeypatch.setattr(SessionCommitProcessor, "DEP_WAIT_MAX_HOLD_S", 0.02)

    processor = _processor()
    msg = _msg()
    # 20 dependency waits back-to-back must NOT take 20 * full consumer speed:
    # cumulative hold grows with backoff instead of the old immediate requeue.
    for _ in range(20):
        await processor._requeue_with_backoff(msg)
    attempt, _ = processor._dep_wait[processor._dep_wait_key(msg)]
    assert attempt == 20
    assert len(stub.enqueued) == 20
    # delay schedule reaches the cap quickly under the shrunken test constants
    assert processor._dep_wait_delay_s(attempt) == pytest.approx(0.05)


def test_key_distinguishes_sessions_and_archives() -> None:
    assert SessionCommitProcessor._dep_wait_key(_msg()) != SessionCommitProcessor._dep_wait_key(
        _msg(archive_uri="viking://a/2")
    )
    assert SessionCommitProcessor._dep_wait_key(_msg()) != SessionCommitProcessor._dep_wait_key(
        _msg(session_id="sess-2")
    )
