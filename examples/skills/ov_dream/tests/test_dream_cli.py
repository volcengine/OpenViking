from __future__ import annotations

import importlib.util
import json
from pathlib import Path
import sys


def _load_dream_module():
    module_path = Path("examples/skills/ov_dream/scripts/dream.py").resolve()
    spec = importlib.util.spec_from_file_location("ov_dream_cli", module_path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


dream = _load_dream_module()


def _write_session(path: Path, session_id: str, messages: list[tuple[str, str, str]] | None = None) -> None:
    rows = [
        {"id": session_id, "timestamp": "2026-04-20T00:00:00Z", "cwd": "/tmp"},
    ]
    for role, text, timestamp in messages or []:
        rows.append(
            {
                "type": "message",
                "timestamp": timestamp,
                "message": {
                    "role": role,
                    "content": [{"type": "text", "text": text}],
                },
            }
        )
    path.write_text("\n".join(json.dumps(row, ensure_ascii=False) for row in rows) + "\n", encoding="utf-8")


def test_normalize_raw_ov_recall_phrase() -> None:
    assert dream._normalize_ov_command(["ov recall 小明的信息"]) == ["recall", "小明的信息"]


def test_recall_expands_default_user_root_to_explicit_user_space(monkeypatch) -> None:
    monkeypatch.setenv("OPENVIKING_USER", "default")
    client = dream.OpenVikingClient(base_url="http://127.0.0.1:1933")

    assert client._resolve_target_uri("viking://user/default") == "viking://user/default"
    assert client._resolve_target_uri("viking://user/default/") == "viking://user/default"
    assert client._resolve_target_uri("viking://user/memories") == "viking://user/default/memories/"
    assert client._resolve_target_uri("viking://user/memories/") == "viking://user/default/memories/"
    assert client._resolve_target_uri("viking://user/default/memories/") == "viking://user/default/memories/"


def test_recall_default_target_uri_is_user_root() -> None:
    calls = []

    class RecordingClient(dream.OpenVikingClient):
        def _request(self, method, path, payload=None):
            calls.append((method, path, payload))
            return {"memories": []}

    client = RecordingClient(base_url=dream.SERVERLESS_BASE_URL, api_key="test-key", agent_id="test-agent")

    client.recall("hello")

    assert calls == [
        (
            "POST",
            "/api/v1/search/find",
            {
                "query": "hello",
                "limit": 5,
                "target_uri": "viking://user/default",
            },
        )
    ]


def test_serverless_headers_use_bearer_and_agent_id() -> None:
    client = dream.OpenVikingClient(
        base_url=dream.SERVERLESS_BASE_URL,
        api_key="test-key",
        agent_id="test-agent",
    )

    assert client.auth_mode == "serverless"
    assert client._headers()["Authorization"] == "Bearer test-key"
    assert client._headers()["X-OpenViking-Agent"] == "test-agent"
    assert "X-API-Key" not in client._headers()
    assert "X-OpenViking-User" not in client._headers()


def test_serverless_sync_reuses_source_session_id_and_uses_parts_payload() -> None:
    calls = []

    class RecordingClient(dream.OpenVikingClient):
        def _request(self, method, path, payload=None):
            calls.append((method, path, payload))
            return {}

    client = RecordingClient(
        base_url=dream.SERVERLESS_BASE_URL,
        api_key="test-key",
        agent_id="test-agent",
    )

    client.add_session_message("source-session", "user", "hello")
    client.commit_session("source-session")

    assert calls == [
        (
            "POST",
            "/api/v1/sessions/source-session/messages",
            {"role": "user", "parts": [{"type": "text", "text": "hello"}]},
        ),
        ("POST", "/api/v1/sessions/source-session/commit", {"telemetry": False}),
    ]


def test_get_active_session_prefers_sessions_index(tmp_path: Path) -> None:
    openclaw_root = tmp_path / ".openclaw"
    sessions_root = openclaw_root / "agents" / "main" / "sessions"
    sessions_root.mkdir(parents=True)

    indexed_session = sessions_root / "indexed.jsonl"
    indexed_session.write_text(
        json.dumps({"id": "indexed", "timestamp": "2026-04-20T00:00:00Z", "cwd": "/tmp"}) + "\n",
        encoding="utf-8",
    )
    newer_fallback = sessions_root / "newer.jsonl"
    newer_fallback.write_text(
        json.dumps({"id": "newer", "timestamp": "2026-04-20T00:00:01Z", "cwd": "/tmp"}) + "\n",
        encoding="utf-8",
    )

    (sessions_root / "sessions.json").write_text(
        json.dumps(
            {
                "agent:main:main": {
                    "sessionId": "indexed",
                    "sessionFile": str(indexed_session),
                }
            }
        ),
        encoding="utf-8",
    )

    session = dream.get_active_session(openclaw_root)

    assert session is not None
    assert session.session_id == "indexed"


def test_get_active_sessions_does_not_fallback_to_raw_jsonl(tmp_path: Path) -> None:
    openclaw_root = tmp_path / ".openclaw"
    sessions_root = openclaw_root / "agents" / "main" / "sessions"
    sessions_root.mkdir(parents=True)

    cron_session = sessions_root / "cron.jsonl"
    _write_session(cron_session, "cron")
    latest_unindexed = sessions_root / "latest.jsonl"
    _write_session(latest_unindexed, "latest")

    (sessions_root / "sessions.json").write_text(
        json.dumps(
            {
                "agent:main:cron:daily": {
                    "sessionId": "cron",
                    "sessionFile": str(cron_session),
                }
            }
        ),
        encoding="utf-8",
    )

    assert dream.get_active_sessions(openclaw_root) == []
    assert dream.get_active_session(openclaw_root) is None


def test_is_chat_session_key_filters_non_chat_openclaw_sessions() -> None:
    assert dream.is_chat_session_key("agent:main:main")
    assert dream.is_chat_session_key("agent:main:web-abc")
    assert dream.is_chat_session_key("agent:main:telegram:direct:123")
    assert dream.is_chat_session_key("agent:main:discord:channel:456")
    assert dream.is_chat_session_key("agent:main:chat:group:789")
    assert dream.is_chat_session_key("agent:main:chat:room:abc")
    assert dream.is_chat_session_key("agent:other:main")
    assert dream.is_chat_session_key("plain:main")

    assert not dream.is_chat_session_key("agent:main:cron:daily")
    assert not dream.is_chat_session_key("agent:main:heartbeat")
    assert not dream.is_chat_session_key("agent:main:subagent:child")
    assert not dream.is_chat_session_key("agent:main:acp:tool")
    assert not dream.is_chat_session_key("agent:main:hook:event")
    assert not dream.is_chat_session_key("")


def test_get_active_sessions_filters_index_entries(tmp_path: Path) -> None:
    openclaw_root = tmp_path / ".openclaw"
    sessions_root = openclaw_root / "agents" / "main" / "sessions"
    sessions_root.mkdir(parents=True)

    kept_main = sessions_root / "main.jsonl"
    kept_direct = sessions_root / "direct.jsonl"
    skipped_cron = sessions_root / "cron.jsonl"
    skipped_subagent = sessions_root / "subagent.jsonl"
    _write_session(kept_main, "main")
    _write_session(kept_direct, "direct")
    _write_session(skipped_cron, "cron")
    _write_session(skipped_subagent, "subagent")

    (sessions_root / "sessions.json").write_text(
        json.dumps(
            {
                "agent:main:main": {"sessionId": "main", "sessionFile": "main.jsonl"},
                "agent:main:telegram:direct:123": {"sessionId": "direct", "sessionFile": str(kept_direct)},
                "agent:main:cron:daily": {"sessionId": "cron", "sessionFile": str(skipped_cron)},
                "agent:main:subagent:child": {"sessionId": "subagent", "sessionFile": str(skipped_subagent)},
            }
        ),
        encoding="utf-8",
    )

    sessions = dream.get_active_sessions(openclaw_root)

    assert {session.session_id for session in sessions} == {"main", "direct"}
    assert {session.session_key for session in sessions} == {
        "agent:main:main",
        "agent:main:telegram:direct:123",
    }


def test_sync_active_session_syncs_chat_sessions_with_independent_cursors(tmp_path: Path) -> None:
    openclaw_root = tmp_path / ".openclaw"
    sessions_root = openclaw_root / "agents" / "main" / "sessions"
    state_root = openclaw_root / "memory"
    sessions_root.mkdir(parents=True)

    main_file = sessions_root / "main.jsonl"
    direct_file = sessions_root / "direct.jsonl"
    cron_file = sessions_root / "cron.jsonl"
    _write_session(
        main_file,
        "main",
        [("user", "hello from main", "2026-04-20T00:01:00Z")],
    )
    _write_session(
        direct_file,
        "direct",
        [("assistant", "hello from direct", "2026-04-20T00:02:00Z")],
    )
    _write_session(
        cron_file,
        "cron",
        [("user", "cron should not sync", "2026-04-20T00:03:00Z")],
    )
    (sessions_root / "sessions.json").write_text(
        json.dumps(
            {
                "agent:main:main": {"sessionId": "main", "sessionFile": str(main_file)},
                "agent:main:telegram:direct:123": {"sessionId": "direct", "sessionFile": str(direct_file)},
                "agent:main:cron:daily": {"sessionId": "cron", "sessionFile": str(cron_file)},
            }
        ),
        encoding="utf-8",
    )

    class RecordingClient:
        def __init__(self) -> None:
            self.messages = []
            self.commits = []

        def add_session_message(self, session_id, role, content):
            self.messages.append((session_id, role, content))

        def commit_session(self, session_id, wait=True):
            self.commits.append((session_id, wait))

    client = RecordingClient()

    summary = dream.sync_active_session(client, openclaw_root, state_root)

    assert summary["session_count"] == 2
    assert summary["synced_count"] == 2
    assert client.messages == [
        ("main", "user", "hello from main"),
        ("direct", "assistant", "hello from direct"),
    ]
    assert client.commits == [("main", True), ("direct", True)]

    state = json.loads((state_root / "ov_dream_sync.json").read_text(encoding="utf-8"))
    assert state["sessions"]["main"]["session_key"] == "agent:main:main"
    assert state["sessions"]["main"]["last_synced_timestamp"] == "2026-04-20T00:01:00Z"
    assert state["sessions"]["direct"]["session_key"] == "agent:main:telegram:direct:123"
    assert state["sessions"]["direct"]["last_synced_timestamp"] == "2026-04-20T00:02:00Z"
    assert "cron" not in state["sessions"]
