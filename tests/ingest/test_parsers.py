# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0
"""Adapter parsing tests against synthetic per-harness fixtures."""

import json
import sqlite3

from openviking.ingest.sources.claude_code import ClaudeCodeSource
from openviking.ingest.sources.codex import CodexSource
from openviking.ingest.sources.hermes import HermesSource
from openviking.ingest.sources.mimo import MiMoSource
from openviking.ingest.sources.openclaw import OpenClawSource
from openviking.ingest.sources.opencode import OpenCodeSource
from openviking.ingest.sources.workbuddy import WorkBuddySource, user_turn_text
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


def test_codex_forked_rollout_file_gets_its_own_session_id(tmp_path):
    """A forked/continued session writes a second file that reuses ``session_meta.id``.

    Sharing one native_session_id makes the two files share one byte-offset cursor, and
    since the cursor also stores the inode, every poll looks like log rotation and
    re-reads the file from the top. Each file must own its cursor instead.
    """
    root = tmp_path / "sessions"
    day = root / "2026" / "09" / "02"
    base = "01a0600e-e891-7b42-8da9-b73b83d46acd"
    fork = "01a06126-7ca2-7eb1-b795-40499a2b3ce6"

    def _records(text):
        return [
            {
                "type": "session_meta",
                "timestamp": "2026-09-02T00:00:00Z",
                "payload": {"id": base, "model_provider": "openai", "cwd": str(tmp_path)},
            },
            {
                "type": "response_item",
                "timestamp": "2026-09-02T00:00:01Z",
                "payload": {
                    "type": "message",
                    "role": "user",
                    "content": [{"type": "input_text", "text": text}],
                },
            },
        ]

    parent = day / f"rollout-2026-09-02T10-59-44-{base}.jsonl"
    child = day / f"rollout-2026-09-02T16-05-07-{base}_{fork}.jsonl"
    _write_jsonl(parent, _records("parent turn"))
    _write_jsonl(child, _records("child turn"))

    src = CodexSource(_cfg(root), fallback_user="tester")
    refs = {r.native_session_id: r for r in src.discover_sessions()}
    # Two files -> two cursors. Before the fix both collapsed onto ``base``.
    assert set(refs) == {base, f"{base}_{fork}"}

    # Simulate two ingest sweeps, keying the cursor by native_session_id like the store does.
    first = {}
    for sid, ref in refs.items():
        msgs, cursor = src.read_messages(ref, None)
        first[sid] = cursor
        assert len(msgs) == 1
    assert {src.read_messages(refs[sid], None)[0][0].text for sid in refs} == {
        "parent turn",
        "child turn",
    }

    for sid, ref in refs.items():
        msgs, _ = src.read_messages(ref, first[sid])
        assert msgs == [], f"{sid} re-read its rollout file on the second sweep"


def _workbuddy_turn(role, text, ts_ms, model=None):
    record = {
        "id": f"m-{ts_ms}",
        "timestamp": ts_ms,
        "type": "message",
        "role": role,
        "content": [{"type": "input_text" if role == "user" else "output_text", "text": text}],
        "sessionId": "wb-session",
        "cwd": "/tmp/wb-project",
    }
    if model:
        record["providerData"] = {"model": model}
    return record


def test_workbuddy_keeps_only_the_user_query(tmp_path):
    """Host-injected context and quoted history must not become memories."""
    root = tmp_path / "projects"
    _write_jsonl(
        root / "proj" / "wb-session.jsonl",
        [
            {"timestamp": 1757000000000, "type": "file-history-snapshot", "cwd": "/tmp/wb-project"},
            {"timestamp": 1757000001000, "type": "ai-title", "aiTitle": "Refactor the parser"},
            _workbuddy_turn(
                "user",
                '<system-reminder data-role="user-context">\n'
                "You are a coding agent. Project Context: ... <user_query>example</user_query>\n"
                "</system-reminder>\n"
                "<memory_and_skills_reminder>prefer the repo skill</memory_and_skills_reminder>\n"
                "<previous_user_message><user_query>an older ask</user_query></previous_user_message>\n"
                "<current_time>2026-09-05T10:00:00Z</current_time>\n"
                "<user_query>rename the ingest cursor helper</user_query>",
                1757000002000,
            ),
            _workbuddy_turn("assistant", "Done — renamed it.", 1757000003000, model="glm-5.2"),
        ],
    )
    src = WorkBuddySource(_cfg(root), fallback_user="tester")
    ref, msgs, _ = _read_all(src)
    assert ref.native_session_id == "wb-session"
    assert ref.title == "Refactor the parser"
    assert ref.meta["cwd"] == "/tmp/wb-project"
    assert [(m.role, m.text) for m in msgs] == [
        ("user", "rename the ingest cursor helper"),
        ("assistant", "Done — renamed it."),
    ]
    # epoch milliseconds are converted to ISO-8601 UTC
    assert msgs[0].created_at == "2025-09-04T15:33:22+00:00"
    assert msgs[1].peer_id == "workbuddy__glm-5.2"


def test_workbuddy_drops_host_generated_turns(tmp_path):
    """A turn with no <user_query> carries no human text and must be dropped."""
    root = tmp_path / "projects"
    _write_jsonl(
        root / "proj" / "wb-session.jsonl",
        [
            _workbuddy_turn(
                "user",
                "<conversation_history_summary>Summary of the conversation so far.</conversation_history_summary>"
                "<additional_data>noise</additional_data>",
                1757000000000,
            ),
            _workbuddy_turn(
                "user",
                "Please continue with the conversation based on the summarized context above.",
                1757000001000,
            ),
            _workbuddy_turn("user", "<user_query>the real ask</user_query>", 1757000002000),
        ],
    )
    src = WorkBuddySource(_cfg(root), fallback_user="tester")
    _, msgs, _ = _read_all(src)
    assert [(m.role, m.text) for m in msgs] == [("user", "the real ask")]


def test_workbuddy_quoted_user_query_does_not_win():
    """A quoted <user_query> inside <previous_user_message> is history, not the ask."""
    quoted = "<previous_user_message><user_query>the first thing I ever asked</user_query></previous_user_message>"
    assert user_turn_text(f"{quoted}<user_query>the current ask</user_query>") == "the current ask"
    # nothing but injected blocks -> no human text at all
    assert user_turn_text("<system-reminder>ctx</system-reminder>") == ""
    assert user_turn_text("") == ""


def test_workbuddy_unknown_records_are_ignored(tmp_path):
    root = tmp_path / "projects"
    _write_jsonl(
        root / "proj" / "wb-session.jsonl",
        [
            {"timestamp": 1757000000000, "type": "reasoning", "content": "thinking"},
            {"timestamp": 1757000001000, "type": "function_call", "name": "read_file"},
            {
                "timestamp": 1757000002000,
                "type": "function_call_result",
                "name": "read_file",
                "output": "file body",
            },
            _workbuddy_turn("user", "<user_query>ship it</user_query>", 1757000003000),
        ],
    )
    src = WorkBuddySource(_cfg(root), fallback_user="tester")
    _, msgs, _ = _read_all(src)
    assert [(m.role, m.text) for m in msgs] == [("user", "ship it")]


def test_workbuddy_finds_title_beyond_the_head(tmp_path):
    """``ai-title`` can be written far into the file, not just in the first record."""
    root = tmp_path / "projects"
    filler = [
        _workbuddy_turn("assistant", f"filler {i}", 1757000000000 + i, model="hy3")
        for i in range(600)
    ]
    _write_jsonl(
        root / "proj" / "wb-session.jsonl",
        filler + [{"timestamp": 1757009999999, "type": "ai-title", "aiTitle": "Late title"}],
    )
    src = WorkBuddySource(_cfg(root), fallback_user="tester")
    refs = list(src.discover_sessions())
    assert [r.title for r in refs] == ["Late title"]


def test_workbuddy_latest_ai_title_wins(tmp_path):
    """The host re-emits ``ai-title`` when it re-titles a session; the last one is the name."""
    root = tmp_path / "projects"
    _write_jsonl(
        root / "proj" / "wb-session.jsonl",
        [
            {"timestamp": 1757000000000, "type": "ai-title", "aiTitle": "First guess"},
            _workbuddy_turn(
                "user", "<user_query>look at the installer</user_query>", 1757000001000
            ),
            _workbuddy_turn("assistant", "on it", 1757000002000, model="hy3"),
            {"timestamp": 1757000003000, "type": "ai-title", "aiTitle": "Settled name"},
        ],
    )
    src = WorkBuddySource(_cfg(root), fallback_user="tester")
    refs = list(src.discover_sessions())
    assert [r.title for r in refs] == ["Settled name"]


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


def test_mimo_skips_synthetic_parts_and_system_prompt(tmp_path):
    db = tmp_path / "mimocode.db"
    conn = sqlite3.connect(str(db))
    conn.executescript(
        """
        CREATE TABLE session (
            id TEXT, title TEXT, directory TEXT, version TEXT, time_created INT
        );
        CREATE TABLE message (
            id TEXT, session_id TEXT, agent_id TEXT, time_created INT, data TEXT
        );
        CREATE TABLE part (
            id TEXT, message_id TEXT, session_id TEXT, time_created INT, data TEXT
        );
        """
    )
    conn.execute(
        "INSERT INTO session VALUES (?,?,?,?,?)",
        ("ses_1", "demo", str(tmp_path), "desktop-test", 1000),
    )
    # message.data.system is the host system prompt — must never be message text
    conn.execute(
        "INSERT INTO message VALUES (?,?,?,?,?)",
        (
            "msg_u",
            "ses_1",
            "main",
            1773044814194,
            json.dumps({"role": "user", "system": "You are MiMo agent..."}),
        ),
    )
    conn.execute(
        "INSERT INTO message VALUES (?,?,?,?,?)",
        (
            "msg_side",
            "ses_1",
            "side",
            1773044815000,
            json.dumps({"role": "assistant", "finish": "stop"}),
        ),
    )
    conn.execute(
        "INSERT INTO message VALUES (?,?,?,?,?)",
        (
            "msg_a",
            "ses_1",
            "main",
            1773044819959,
            json.dumps(
                {
                    "role": "assistant",
                    "modelID": "mimo-x-pro-preview",
                    "providerID": "xiaomi",
                    "finish": "stop",
                }
            ),
        ),
    )
    conn.execute(
        "INSERT INTO part VALUES (?,?,?,?,?)",
        (
            "p_syn",
            "msg_u",
            "ses_1",
            1,
            json.dumps(
                {
                    "type": "text",
                    "text": "<system-reminder>runtime</system-reminder>",
                    "synthetic": True,
                }
            ),
        ),
    )
    conn.execute(
        "INSERT INTO part VALUES (?,?,?,?,?)",
        (
            "p_u",
            "msg_u",
            "ses_1",
            2,
            json.dumps(
                {
                    "type": "text",
                    "text": "<system-reminder>note</system-reminder>hello mimo",
                }
            ),
        ),
    )
    conn.execute(
        "INSERT INTO part VALUES (?,?,?,?,?)",
        ("p_tool", "msg_a", "ses_1", 1, json.dumps({"type": "tool", "tool": "bash"})),
    )
    conn.execute(
        "INSERT INTO part VALUES (?,?,?,?,?)",
        ("p_a", "msg_a", "ses_1", 2, json.dumps({"type": "text", "text": "world"})),
    )
    conn.commit()
    conn.close()

    src = MiMoSource(_cfg(db), fallback_user="tester")
    refs = list(src.discover_sessions())
    assert len(refs) == 1
    assert refs[0].title == "demo"
    msgs, cursor = src.read_messages(refs[0], None)
    # sidechain row ignored; synthetic part dropped; reminder stripped
    assert [(m.role, m.text) for m in msgs] == [("user", "hello mimo"), ("assistant", "world")]
    assert msgs[1].peer_id == "mimo__xiaomi__mimo-x-pro-preview"
    msgs2, _ = src.read_messages(refs[0], cursor)
    assert msgs2 == []
