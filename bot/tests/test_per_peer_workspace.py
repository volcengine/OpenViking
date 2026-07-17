from types import SimpleNamespace

import pytest
from vikingbot.config.schema import Config, SandboxConfig, SessionKey
from vikingbot.heartbeat.service import HeartbeatService
from vikingbot.sandbox.manager import WORKSPACE_PEER_ID_METADATA_KEY, SandboxManager
from vikingbot.session.manager import SessionManager


def _session(chat_id: str) -> SessionKey:
    return SessionKey(type="openapi", channel_id="default", chat_id=chat_id)


def _manager(tmp_path, mode: str = "per-peer") -> SandboxManager:
    config = Config(sandbox=SandboxConfig(mode=mode), skills=[])
    return SandboxManager(config, tmp_path / "workspaces", tmp_path / "source")


def test_per_peer_workspace_reuses_identity_across_sessions(tmp_path):
    manager = _manager(tmp_path)

    alice_first = manager.to_workspace_id(_session("one"), "alice")
    alice_second = manager.to_workspace_id(_session("two"), "alice")
    bob = manager.to_workspace_id(_session("one"), "bob")

    assert alice_first == alice_second == "peer__alice"
    assert bob == "peer__bob"
    assert bob != alice_first


def test_per_peer_workspace_encodes_non_ascii_and_rejects_unsafe_identity(tmp_path):
    manager = _manager(tmp_path)

    workspace_id = manager.to_workspace_id(_session("one"), "用户 A")

    assert workspace_id.startswith("peer__ext-")
    assert "/" not in workspace_id
    with pytest.raises(ValueError, match="actor_peer_id"):
        manager.to_workspace_id(_session("one"))
    with pytest.raises(ValueError, match="actor_peer_id"):
        manager.to_workspace_id(_session("one"), "../alice")


def test_session_manager_defers_per_peer_workspace_until_identity_is_known(tmp_path):
    manager = _manager(tmp_path)
    sessions = SessionManager(tmp_path / "data", sandbox_manager=manager)

    session = sessions.get_or_create(_session("one"))

    assert session.key == _session("one")
    assert manager._sandboxes == {}


def test_heartbeat_groups_sessions_by_recorded_peer_workspace(tmp_path):
    manager = _manager(tmp_path)
    sessions = SimpleNamespace(
        sandbox_manager=manager,
        list_sessions=lambda: [
            {
                "key": _session("one"),
                "metadata": {WORKSPACE_PEER_ID_METADATA_KEY: "alice"},
            },
            {
                "key": _session("two"),
                "metadata": {WORKSPACE_PEER_ID_METADATA_KEY: "alice"},
            },
            {"key": _session("legacy"), "metadata": {}},
        ],
    )
    service = HeartbeatService(
        workspace=manager.workspace,
        sandbox_mode="per-peer",
        session_manager=sessions,
    )

    workspaces = service._get_all_workspaces()

    assert workspaces == {
        manager.workspace / "peer__alice": [_session("one"), _session("two")]
    }
