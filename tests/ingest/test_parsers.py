# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0
"""Adapter parsing tests against synthetic per-harness fixtures."""

import json
import sqlite3

from openviking.ingest.sources.claude_code import ClaudeCodeSource
from openviking.ingest.sources.codex import CodexSource
from openviking.ingest.sources.hermes import HermesSource
from openviking.ingest.sources.openclaw import OpenClawSource
from openviking.ingest.sources.opencode import OpenCodeSource
from openviking_cli.utils.config.ingest_config import IngestHarnessConfig


def _write_jsonl(path, records):
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w") as f:
        for r in records:
            f.write(json.dumps(r) + "\n")


def _cfg(root, **kw):
    return IngestHarnessConfig(enabled=True, paths=[str(root)], **kw)


def _read_all(source):
    refs = list(source.discover_sessions())
    assert len(refs) == 1
    msgs, cursor = source.read_messages(refs[0], None)
    return refs[0], msgs, cursor


def test_claude_code(tmp_path):
    root = tmp_path / "projects"
    _write_jsonl(
        root / "slug" / "sess-1.jsonl",
        [
            {"type": "queue-operation"},  # dropped (non-message)
            {
                "type": "user",
                "cwd": str(tmp_path),
                "timestamp": "2026-06-01T00:00:00Z",
                "message": {"role": "user", "content": "hello there"},
            },
            {
                "type": "assistant",
                "timestamp": "2026-06-01T00:00:01Z",
                "message": {
                    "role": "assistant",
                    "model": "claude-opus-4-8",
                    "content": [
                        {"type": "text", "text": "hi!"},
                        {"type": "tool_use", "name": "Read"},  # dropped
                    ],
                },
            },
            {  # sub-agent record -> dropped
                "type": "assistant",
                "isSidechain": True,
                "message": {"role": "assistant", "content": [{"type": "text", "text": "side"}]},
            },
        ],
    )
    src = ClaudeCodeSource(_cfg(root), fallback_user="tester")
    ref, msgs, _ = _read_all(src)
    assert ref.native_session_id == "sess-1"
    assert [(m.role, m.text) for m in msgs] == [("user", "hello there"), ("assistant", "hi!")]
    assert msgs[1].peer_id == "claude_code__claude-opus-4-8"
    assert msgs[0].peer_id == "tester"  # no git repo -> configured fallback


def test_codex(tmp_path):
    root = tmp_path / "sessions"
    _write_jsonl(
        root / "2026" / "06" / "25" / "rollout-x-abc.jsonl",
        [
            {
                "type": "session_meta",
                "timestamp": "2026-06-25T00:00:00Z",
                "payload": {"id": "codex-sess", "model_provider": "openai", "cwd": str(tmp_path)},
            },
            {
                "type": "response_item",
                "timestamp": "2026-06-25T00:00:01Z",
                "payload": {
                    "type": "message",
                    "role": "developer",  # dropped boilerplate
                    "content": [{"type": "input_text", "text": "system stuff"}],
                },
            },
            {
                "type": "response_item",
                "timestamp": "2026-06-25T00:00:02Z",
                "payload": {
                    "type": "message",
                    "role": "user",
                    "content": [{"type": "input_text", "text": "fix the bug"}],
                },
            },
            {
                "type": "response_item",
                "timestamp": "2026-06-25T00:00:03Z",
                "payload": {
                    "type": "message",
                    "role": "assistant",
                    "content": [{"type": "output_text", "text": "done"}],
                },
            },
            {"type": "response_item", "payload": {"type": "reasoning"}},  # dropped
        ],
    )
    src = CodexSource(_cfg(root), fallback_user="tester")
    ref, msgs, _ = _read_all(src)
    assert ref.native_session_id == "codex-sess"
    assert [(m.role, m.text) for m in msgs] == [("user", "fix the bug"), ("assistant", "done")]
    assert msgs[1].peer_id == "codex__openai"


def test_hermes_group_username(tmp_path):
    root = tmp_path / "sessions"
    _write_jsonl(
        root / "grp.jsonl",
        [
            {"role": "session_meta", "model": "doubao-x", "platform": "feishu"},
            {
                "role": "user",
                "content": "hi",
                "timestamp": "2026-06-01T00:00:00Z",
                "sender": "alice",
            },
            {"role": "assistant", "content": "hello", "timestamp": "2026-06-01T00:00:01Z"},
        ],
    )
    src = HermesSource(_cfg(root, user_field="sender"), fallback_user="tester")
    _, msgs, _ = _read_all(src)
    assert msgs[0].peer_id == "alice"  # original username from the log
    assert msgs[1].peer_id == "hermes__doubao-x"


def test_openclaw_assistant_model(tmp_path):
    root = tmp_path / "agents"
    _write_jsonl(
        root / "main" / "sessions" / "oc.jsonl",
        [
            {"type": "session", "id": "oc"},
            {
                "type": "message",
                "timestamp": "2026-06-01T00:00:00Z",
                "message": {"role": "user", "content": [{"type": "text", "text": "run it"}]},
            },
            {
                "type": "message",
                "timestamp": "2026-06-01T00:00:01Z",
                "message": {
                    "role": "assistant",
                    "provider": "ark",
                    "model": "doubao-x",
                    "content": [
                        {"type": "thinking", "thinking": "hmm"},  # dropped
                        {"type": "text", "text": "ok"},
                    ],
                },
            },
        ],
    )
    src = OpenClawSource(_cfg(root, user_field="sender"), fallback_user="tester")
    _, msgs, _ = _read_all(src)
    assert [(m.role, m.text) for m in msgs] == [("user", "run it"), ("assistant", "ok")]
    assert msgs[1].peer_id == "openclaw__ark__doubao-x"


def test_opencode_text_from_part_table(tmp_path):
    db = tmp_path / "opencode.db"
    conn = sqlite3.connect(str(db))
    conn.executescript(
        """
        CREATE TABLE session (id TEXT, title TEXT, directory TEXT, model TEXT, time_created INT);
        CREATE TABLE message (id TEXT, session_id TEXT, time_created INT, data TEXT);
        CREATE TABLE part (id TEXT, message_id TEXT, session_id TEXT, time_created INT, data TEXT);
        """
    )
    conn.execute(
        "INSERT INTO session VALUES (?,?,?,?,?)",
        ("ses_1", "demo", str(tmp_path), "m", 1000),
    )
    conn.execute(
        "INSERT INTO message VALUES (?,?,?,?)",
        ("msg_u", "ses_1", 1773044814194, json.dumps({"role": "user"})),
    )
    conn.execute(
        "INSERT INTO message VALUES (?,?,?,?)",
        (
            "msg_a",
            "ses_1",
            1773044819959,
            json.dumps(
                {
                    "role": "assistant",
                    "modelID": "glm-4.7",
                    "providerID": "tiktok",
                    "finish": "stop",  # mark complete so the cursor advances past it
                }
            ),
        ),
    )
    # text lives in the part table, not message.data
    conn.execute(
        "INSERT INTO part VALUES (?,?,?,?,?)",
        ("p1", "msg_u", "ses_1", 1, json.dumps({"type": "text", "text": "hello"})),
    )
    conn.execute(
        "INSERT INTO part VALUES (?,?,?,?,?)",
        ("p2", "msg_a", "ses_1", 1, json.dumps({"type": "step-start"})),  # not text
    )
    conn.execute(
        "INSERT INTO part VALUES (?,?,?,?,?)",
        ("p3", "msg_a", "ses_1", 2, json.dumps({"type": "text", "text": "world"})),
    )
    conn.commit()
    conn.close()

    src = OpenCodeSource(_cfg(db), fallback_user="tester")
    refs = list(src.discover_sessions())
    assert len(refs) == 1
    msgs, cursor = src.read_messages(refs[0], None)
    assert [(m.role, m.text) for m in msgs] == [("user", "hello"), ("assistant", "world")]
    assert msgs[1].peer_id == "opencode__tiktok__glm-4.7"
    # cursor advanced; re-read returns nothing new
    msgs2, _ = src.read_messages(refs[0], cursor)
    assert msgs2 == []
