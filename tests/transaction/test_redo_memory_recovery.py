# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0
"""Regression tests for redo session-memory recovery."""

import json
from unittest.mock import MagicMock

from openviking.message import Message
from openviking.storage.transaction.lock_manager import LockManager


class _FakeAGFS:
    def __init__(self, message: Message):
        self.message = message

    def cat(self, path: str):
        assert path == "/local/session/archive/messages.jsonl"
        return json.dumps(self.message.to_dict())


class _FakeVikingFS:
    def _uri_to_path(self, uri, ctx=None):
        del uri, ctx
        return "/local/session/archive/messages.jsonl"


def _redo_info():
    return {
        "archive_uri": "viking://session/acc/user/session/history/archive_001",
        "session_uri": "viking://session/acc/user/session",
        "account_id": "acc",
        "user_id": "user",
        "agent_id": "agent",
        "role": "root",
    }


async def test_redo_keeps_marker_without_vikingdb(monkeypatch):
    monkeypatch.setattr(
        "openviking.storage.viking_fs.get_viking_fs",
        lambda: _FakeVikingFS(),
    )

    lm = LockManager(agfs=_FakeAGFS(Message.create_user("remember this")))
    lm._redo_log = MagicMock()
    lm._redo_log.list_pending.return_value = ["redo-task"]
    lm._redo_log.read.return_value = _redo_info()

    await lm._recover_pending_redo()

    lm._redo_log.mark_done.assert_not_called()


async def test_redo_uses_vikingdb_compressor_with_strict_dedup(monkeypatch):
    monkeypatch.setattr(
        "openviking.storage.viking_fs.get_viking_fs",
        lambda: _FakeVikingFS(),
    )

    captured = {}

    class FakeCompressor:
        async def extract_long_term_memories(self, **kwargs):
            captured.update(kwargs)
            return []

    vikingdb = MagicMock()
    lm = LockManager(
        agfs=_FakeAGFS(Message.create_user("remember this")),
        vikingdb=vikingdb,
    )
    lm._redo_log = MagicMock()
    lm._redo_log.list_pending.return_value = ["redo-task"]
    lm._redo_log.read.return_value = _redo_info()

    create_compressor = MagicMock(return_value=FakeCompressor())
    monkeypatch.setattr("openviking.session.create_session_compressor", create_compressor)

    await lm._recover_pending_redo()

    create_compressor.assert_called_once_with(vikingdb=vikingdb)
    assert captured["strict_dedup_errors"] is True
    lm._redo_log.mark_done.assert_called_once_with("redo-task")
