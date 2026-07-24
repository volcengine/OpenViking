# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: Apache-2.0
"""Tests for SessionCommitProcessor observability identity binding.

Phase-2 memory extraction runs in this queue worker; its VLM/embedding token
events read identity from the root observability context. These tests assert the
worker binds the committing account/user (so tokens are not attributed to
"__unknown__") and resets the context afterwards.
"""

import asyncio

from openviking.observability.context import get_root_observability_context
from openviking.server.identity import RequestContext, Role
from openviking.storage.queuefs.session_commit_msg import SessionCommitMsg
from openviking.storage.queuefs.session_commit_processor import SessionCommitProcessor
from openviking_cli.session.user_id import UserIdentifier


class _FakeSession:
    def __init__(self, captured: dict) -> None:
        self._captured = captured

    async def exists(self) -> bool:
        return True

    async def load(self) -> None:
        return None

    async def resume_queued_commit(self, msg) -> None:
        root = get_root_observability_context()
        self._captured["account_id"] = root.account_id if root else None
        self._captured["user_id"] = root.user_id if root else None


class _FakeSessionService:
    def __init__(self, captured: dict) -> None:
        self._captured = captured

    def session(self, ctx, session_id, session_uri=None):
        return _FakeSession(self._captured)


def _make_msg() -> SessionCommitMsg:
    return SessionCommitMsg(
        task_id="task-1",
        session_id="sess-1",
        session_uri="viking://sessions/sess-1",
        archive_uri="viking://sessions/sess-1/history/archive_001",
        user={"account_id": "acme", "user_id": "alice"},
    )


async def test_process_binds_committing_identity_to_root_context():
    captured: dict = {}
    processor = SessionCommitProcessor(
        _FakeSessionService(captured),
        asyncio.get_running_loop(),
    )
    ctx = RequestContext(user=UserIdentifier("acme", "alice"), role=Role.USER)

    await processor._process(_make_msg(), ctx)

    assert captured["account_id"] == "acme"
    assert captured["user_id"] == "alice"


async def test_process_resets_root_context_after_completion():
    processor = SessionCommitProcessor(
        _FakeSessionService({}),
        asyncio.get_running_loop(),
    )
    ctx = RequestContext(user=UserIdentifier("acme", "alice"), role=Role.USER)

    await processor._process(_make_msg(), ctx)

    assert get_root_observability_context() is None
