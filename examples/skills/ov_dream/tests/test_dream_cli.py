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


def test_normalize_raw_ov_recall_phrase() -> None:
    assert dream._normalize_ov_command(["ov recall 小明的信息"]) == ["recall", "小明的信息"]


def test_recall_expands_user_memories_alias_to_explicit_user_space(monkeypatch) -> None:
    monkeypatch.setenv("OPENVIKING_USER", "default")
    client = dream.OpenVikingClient(base_url="http://127.0.0.1:1933")

    assert client._resolve_target_uri("viking://user/memories") == "viking://user/default/memories/"
    assert client._resolve_target_uri("viking://user/memories/") == "viking://user/default/memories/"
    assert client._resolve_target_uri("viking://user/default/memories/") == "viking://user/default/memories/"


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


def test_serverless_session_api_uses_parts_and_telemetry_payload() -> None:
    calls = []

    class RecordingClient(dream.OpenVikingClient):
        def _request(self, method, path, payload=None):
            calls.append((method, path, payload))
            if path == "/api/v1/sessions":
                return {"session_id": "ov-session"}
            return {}

    client = RecordingClient(
        base_url=dream.SERVERLESS_BASE_URL,
        api_key="test-key",
        agent_id="test-agent",
    )

    assert client._sync_session_id("source-session") == "ov-session"
    client.add_session_message("ov-session", "user", "hello")
    client.commit_session("ov-session")

    assert calls == [
        ("POST", "/api/v1/sessions", {}),
        (
            "POST",
            "/api/v1/sessions/ov-session/messages",
            {"role": "user", "parts": [{"type": "text", "text": "hello"}]},
        ),
        ("POST", "/api/v1/sessions/ov-session/commit", {"telemetry": False}),
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
