import sys
import types
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[4]))

config_module = types.ModuleType("vikingbot.config")
loader_module = types.ModuleType("vikingbot.config.loader")
loader_module.load_config = lambda: None
config_module.load_config = loader_module.load_config
sys.modules.setdefault("vikingbot.config", config_module)
sys.modules.setdefault("vikingbot.config.loader", loader_module)

from vikingbot.openviking_mount.ov_server import VikingClient


def _client(api_key_type: str = "user") -> VikingClient:
    client = VikingClient.__new__(VikingClient)
    client.mode = "remote"
    client.api_key_type = api_key_type
    client.admin_user_id = "bot-user"
    client.agent_id = "agent-1"
    client._request_connection = None
    client._namespace_policy = {
        "isolate_user_scope_by_agent": False,
        "isolate_agent_scope_by_user": False,
    }
    return client


def test_normalize_session_messages_maps_sender_to_peer_only_for_user_messages():
    client = _client()

    messages = [
        {"role": "user", "content": "hello", "sender_id": "telegram:alice"},
        {"role": "assistant", "content": "hi", "sender_id": "telegram:alice"},
    ]

    normalized = client._normalize_session_messages(messages)

    assert normalized[0]["peer_id"] == "telegram:alice"
    assert "peer_id" not in normalized[1]


def test_normalize_session_messages_skips_path_like_peer_ids():
    client = _client()

    normalized = client._normalize_session_messages(
        [{"role": "user", "content": "hello", "sender_id": "bad/peer"}]
    )

    assert "peer_id" not in normalized[0]


@pytest.mark.asyncio
async def test_commit_uses_current_user_key_session_and_sender_peer(monkeypatch):
    client = _client(api_key_type="user")
    calls = {}

    async def fake_append_messages(
        session_id,
        messages,
        default_user_peer_id=None,
        session_user_id=None,
    ):
        calls["append"] = {
            "session_id": session_id,
            "messages": messages,
            "default_user_peer_id": default_user_peer_id,
            "session_user_id": session_user_id,
        }
        return {"added": len(messages)}

    async def fake_commit_session(session_id, keep_recent_count=0, user_id=None):
        calls["commit"] = {
            "session_id": session_id,
            "keep_recent_count": keep_recent_count,
            "user_id": user_id,
        }
        return {"archived": True}

    monkeypatch.setattr(client, "append_messages", fake_append_messages)
    monkeypatch.setattr(client, "commit_session", fake_commit_session)

    await client.commit(
        "session-1",
        [{"role": "user", "content": "remember this"}],
        peer_id="telegram:alice",
    )

    assert calls["append"]["session_user_id"] is None
    assert calls["append"]["default_user_peer_id"] == "telegram:alice"
    assert calls["commit"]["user_id"] is None


@pytest.mark.asyncio
async def test_commit_keeps_root_owner_user_explicit(monkeypatch):
    client = _client(api_key_type="root")
    calls = {}

    async def fake_append_messages(
        session_id,
        messages,
        default_user_peer_id=None,
        session_user_id=None,
    ):
        calls["append"] = {
            "default_user_peer_id": default_user_peer_id,
            "session_user_id": session_user_id,
        }
        return {"added": len(messages)}

    async def fake_commit_session(session_id, keep_recent_count=0, user_id=None):
        calls["commit"] = {"user_id": user_id}
        return {"archived": True}

    monkeypatch.setattr(client, "append_messages", fake_append_messages)
    monkeypatch.setattr(client, "commit_session", fake_commit_session)

    await client.commit(
        "session-1",
        [{"role": "user", "content": "remember this"}],
        peer_id="telegram:alice",
    )

    assert calls["append"]["session_user_id"] == "bot-user"
    assert calls["append"]["default_user_peer_id"] == "telegram:alice"
    assert calls["commit"]["user_id"] == "bot-user"
