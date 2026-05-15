# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0
"""CLI session operation tests (new, list, get, get-context, delete, add-message, commit)."""

import pytest

from conftest import ov

pytestmark = pytest.mark.cli_remote


class TestSessionNew:
    def test_session_new(self):
        r = ov(["session", "new", "-o", "json"])
        assert r["exit_code"] == 0, (
            f"ov session new should exit 0, got {r['exit_code']}: {r['stderr'][:300]}"
        )
        data = r["json"]
        assert data is not None and data.get("ok") is True, f"Expected ok=true"
        result = data["result"]
        assert "session_id" in result, f"'session_id' should exist in result, got keys: {list(result.keys())}"
        assert isinstance(result["session_id"], str), "session_id should be a string"
        assert len(result["session_id"]) > 0, "session_id should not be empty"
        ov(["session", "delete", result["session_id"], "-o", "json"])


class TestSessionList:
    def test_session_list(self):
        r = ov(["session", "list", "-o", "json"])
        assert r["exit_code"] == 0, (
            f"ov session list should exit 0, got {r['exit_code']}: {r['stderr'][:300]}"
        )
        data = r["json"]
        assert data is not None and data.get("ok") is True, f"Expected ok=true"
        assert isinstance(data["result"], list), "'result' should be a list"


class TestSessionGet:
    def test_session_get(self, test_session_id):
        r = ov(["session", "get", test_session_id, "-o", "json"])
        assert r["exit_code"] == 0, (
            f"ov session get should exit 0, got {r['exit_code']}: {r['stderr'][:300]}"
        )
        data = r["json"]
        assert data is not None and data.get("ok") is True, f"Expected ok=true"
        result = data["result"]
        assert result["session_id"] == test_session_id, (
            f"session_id should match, expected {test_session_id}, got {result.get('session_id')}"
        )


class TestSessionGetContext:
    def test_session_get_context(self, test_session_id):
        r = ov(["session", "get-session-context", test_session_id, "-o", "json"])
        assert r["exit_code"] == 0, (
            f"ov session get-session-context should exit 0, got {r['exit_code']}: {r['stderr'][:300]}"
        )
        data = r["json"]
        assert data is not None and data.get("ok") is True, f"Expected ok=true"
        assert "result" in data, "'result' field should exist"


class TestSessionDelete:
    def test_session_delete(self):
        create_r = ov(["session", "new", "-o", "json"])
        assert create_r["exit_code"] == 0, (
            f"session new failed: {create_r['stderr'][:300]}"
        )
        assert create_r["json"] is not None, f"session new should return JSON, got: {create_r['stdout'][:200]}"
        session_id = create_r["json"]["result"]["session_id"]
        r = ov(["session", "delete", session_id, "-o", "json"])
        assert r["exit_code"] == 0, (
            f"ov session delete should exit 0, got {r['exit_code']}: {r['stderr'][:300]}"
        )


class TestSessionAddMessage:
    def test_session_add_message(self, test_session_id):
        r = ov(["session", "add-message", test_session_id, "--role", "user", "--content", "CLI test message", "-o", "json"])
        assert r["exit_code"] == 0, (
            f"ov session add-message should exit 0, got {r['exit_code']}: {r['stderr'][:300]}"
        )
        data = r["json"]
        assert data is not None and data.get("ok") is True, f"Expected ok=true"


class TestSessionCommit:
    def test_session_commit(self, test_session_id):
        ov(["session", "add-message", test_session_id, "--role", "user", "--content", "commit test", "-o", "json"])
        r = ov(["session", "commit", test_session_id, "-o", "json"], timeout=120)
        assert r["exit_code"] == 0, (
            f"ov session commit should exit 0, got {r['exit_code']}: {r['stderr'][:300]}"
        )
        data = r["json"]
        assert data is not None and data.get("ok") is True, f"Expected ok=true"
