import json
from pathlib import Path
from types import SimpleNamespace

import pytest
from vikingbot.agent.context import ContextBuilder
from vikingbot.agent.loop import _is_tool_result_success
from vikingbot.agent.memory import MemoryStore
from vikingbot.agent.tools.base import ToolContext
from vikingbot.agent.tools.ov_file import (
    VikingGlobTool,
    VikingGrepTool,
    VikingListTool,
    VikingMemoryCommitTool,
    VikingSearchTool,
)
from vikingbot.agent.tools import ov_file as ov_file_module
from vikingbot.cli import commands as commands_module
from vikingbot.config import loader as config_loader_module
from vikingbot.config.schema import OpenVikingConfig, SessionKey
from vikingbot.hooks.base import HookContext
from vikingbot.hooks.builtins import openviking_hooks as openviking_hooks_module
from vikingbot.hooks.builtins.openviking_hooks import OpenVikingCompactHook
from vikingbot.openviking_mount import ov_server as ov_server_module
from vikingbot.openviking_mount.ov_server import VikingClient
from vikingbot.openviking_mount.session_state import reset_openviking_state
from vikingbot.session.manager import SessionManager


class _DummySession:
    async def add_message(self, role, parts, created_at=None):
        return None

    async def commit_async(self):
        return {"status": "committed"}


class _DummyHTTPClient:
    instances = []

    def __init__(self, **kwargs):
        self.kwargs = kwargs
        self.find_calls = []
        self.ls_calls = []
        self.closed = False
        _DummyHTTPClient.instances.append(self)

    async def initialize(self):
        return None

    async def create_session(self, session_id=None):
        return {"session_id": session_id or "s-1"}

    async def session_exists(self, _session_id):
        return False

    async def get_session(self, session_id):
        return {"session_id": session_id, "pending_tokens": 0}

    async def batch_add_messages(self, session_id, messages):
        return {"session_id": session_id, "added": len(messages), "message_count": len(messages)}

    async def commit_session(
        self, session_id, keep_recent_count=0, telemetry=False, memory_policy=None
    ):
        return {
            "session_id": session_id,
            "status": "committed",
            "keep_recent_count": keep_recent_count,
            "memory_policy": memory_policy,
        }

    def session(self, _session_id):
        return _DummySession()

    async def admin_list_accounts(self):
        return []

    async def admin_list_users(self, _account_id):
        return []

    async def admin_register_user(self, account_id, user_id, role="user"):
        return {"account_id": account_id, "user_id": user_id, "role": role}

    async def admin_remove_user(self, _account_id, _user_id):
        return None

    async def find(self, *_args, **_kwargs):
        self.find_calls.append((_args, _kwargs))
        return []

    async def ls(self, path, recursive=False):
        self.ls_calls.append((path, recursive))
        return []

    async def search(self, *_args, **_kwargs):
        return {"memories": [], "resources": [], "skills": []}

    async def grep(self, *_args, **_kwargs):
        return {"matches": []}

    async def close(self):
        self.closed = True
        return None


def _make_config(api_key_type: str, mode: str = "remote", **ov_overrides):
    agent_defaults = {
        "session_context_enabled": False,
        "session_context_token_budget": 12000,
        "commit_token_threshold": 6000,
        "commit_keep_recent_count": 10,
    }
    agent_overrides = {}
    for key in tuple(agent_defaults):
        if key in ov_overrides:
            agent_overrides[key] = ov_overrides.pop(key)
    agents = SimpleNamespace(**{**agent_defaults, **agent_overrides})
    ov_server = SimpleNamespace(
        mode=mode,
        api_key_type=api_key_type,
        server_url="http://ov.local",
        api_key="user-key",
        root_api_key="root-key",
        account_id="acct",
        admin_user_id="admin",
        **ov_overrides,
    )
    return SimpleNamespace(
        ov_server=ov_server, agents=agents, ov_data_path=Path("/tmp/openviking-test")
    )


@pytest.fixture(autouse=True)
def _patch_http_client(monkeypatch):
    _DummyHTTPClient.instances.clear()
    monkeypatch.setattr(ov_server_module.ov, "AsyncHTTPClient", _DummyHTTPClient)


def test_viking_client_init_root_mode_sets_account_and_user(monkeypatch):
    monkeypatch.setattr(ov_server_module, "load_config", lambda: _make_config("root"))

    client = VikingClient()

    first = _DummyHTTPClient.instances[0]
    assert client.api_key_type == "root"
    assert first.kwargs["account"] == "acct"
    assert first.kwargs["user"] == "admin"
    assert first.kwargs["profile_enabled"] is False
    assert "agent_id" not in first.kwargs


def test_tool_result_success_only_treats_standard_error_prefix_as_failure():
    assert _is_tool_result_success("errorCode = 0") is True
    assert _is_tool_result_success("Error budget: 5%") is True
    assert _is_tool_result_success("Error: failed") is False


def test_viking_client_init_user_mode_does_not_set_user_or_account(monkeypatch):
    monkeypatch.setattr(ov_server_module, "load_config", lambda: _make_config("user"))

    client = VikingClient()

    first = _DummyHTTPClient.instances[0]
    assert client.api_key_type == "user"
    assert first.kwargs["profile_enabled"] is False
    assert "user" not in first.kwargs
    assert "account" not in first.kwargs
    assert "agent_id" not in first.kwargs


def test_openviking_config_api_key_type_empty_values_are_inferred():
    assert OpenVikingConfig(api_key_type=None, api_key="user-key").api_key_type == "user"
    assert OpenVikingConfig(api_key_type="", api_key="user-key").api_key_type == "user"
    assert OpenVikingConfig(api_key_type=None, root_api_key="root-key").api_key_type == "root"
    assert OpenVikingConfig(api_key_type="", root_api_key="root-key").api_key_type == "root"


def test_user_key_current_memory_targets_use_current_user_shorthand(monkeypatch):
    monkeypatch.setattr(ov_server_module, "load_config", lambda: _make_config("user"))

    client = VikingClient()

    assert client.build_current_memory_target_uris(peer_ids=["sender-1"]) == [
        "viking://user/memories/",
        "viking://user/peers/sender-1/memories/",
    ]


def test_ov_server_legacy_root_api_key_takes_precedence_over_ovcli(monkeypatch):
    bot_data = {"root_api_key": "bot-user-key"}
    ov_data = {"root_api_key": "server-root-key"}
    monkeypatch.setattr(
        config_loader_module,
        "load_ovcli_config",
        lambda: SimpleNamespace(api_key="stale-ovcli-key"),
    )

    config_loader_module._merge_ov_server_config(bot_data, ov_data)

    assert bot_data["mode"] == "remote"
    assert bot_data["api_key"] == "bot-user-key"
    assert bot_data["api_key_type"] == "root"


def test_ov_server_top_level_root_api_key_backfills_legacy_root_mode(monkeypatch):
    bot_data = {}
    ov_data = {"root_api_key": "server-root-key"}
    monkeypatch.setattr(
        config_loader_module,
        "load_ovcli_config",
        lambda: SimpleNamespace(api_key="stale-ovcli-key"),
    )

    config_loader_module._merge_ov_server_config(bot_data, ov_data)

    assert bot_data["mode"] == "remote"
    assert bot_data["api_key"] == "server-root-key"
    assert bot_data["root_api_key"] == "server-root-key"
    assert bot_data["api_key_type"] == "root"


def test_ov_server_api_key_implies_remote_mode(monkeypatch):
    bot_data = {"api_key": "bot-user-key"}
    monkeypatch.setattr(
        config_loader_module,
        "load_ovcli_config",
        lambda: SimpleNamespace(api_key="stale-ovcli-key"),
    )

    config_loader_module._merge_ov_server_config(bot_data, {})

    assert bot_data["mode"] == "remote"
    assert bot_data["api_key"] == "bot-user-key"


def test_validate_openviking_auth_allows_local_mode():
    config = SimpleNamespace(ov_server=SimpleNamespace(mode="local", api_key="", root_api_key=""))

    config_loader_module.validate_openviking_auth(config)


def test_validate_openviking_auth_allows_api_key():
    config = SimpleNamespace(
        ov_server=SimpleNamespace(mode="remote", api_key="user-key", root_api_key="")
    )

    config_loader_module.validate_openviking_auth(config)


def test_validate_openviking_auth_allows_legacy_root_api_key():
    config = SimpleNamespace(
        ov_server=SimpleNamespace(mode="remote", api_key="", root_api_key="root-key")
    )

    config_loader_module.validate_openviking_auth(config)


def test_validate_openviking_auth_exits_with_migration_hint(capsys):
    config = SimpleNamespace(ov_server=SimpleNamespace(mode="remote", api_key="", root_api_key=""))

    with pytest.raises(SystemExit):
        config_loader_module.validate_openviking_auth(config)

    captured = capsys.readouterr()
    assert "bot.ov_server.api_key" in captured.err
    assert "User API key" in captured.err
    assert "root_api_key is deprecated" in captured.err


def test_memory_user_cli_option_warns_at_runtime(capsys):
    commands_module._warn_deprecated_memory_user(["legacy-user"])

    captured = capsys.readouterr()
    assert "--memory-user is deprecated" in captured.err
    assert "--memory-peer" in captured.err


@pytest.mark.asyncio
async def test_user_key_mode_skips_admin_namespace_policy_lookup(monkeypatch):
    monkeypatch.setattr(ov_server_module, "load_config", lambda: _make_config("user"))

    client = VikingClient()

    async def _must_not_call_admin_api():
        raise AssertionError("user key mode must not call admin namespace policy API")

    monkeypatch.setattr(client.client, "admin_list_accounts", _must_not_call_admin_api)

    await client._load_namespace_policy()

    assert client._namespace_policy_loaded is True
    assert client._namespace_policy == {
        "isolate_user_scope_by_agent": False,
        "isolate_agent_scope_by_user": False,
    }


def test_viking_client_request_connection_uses_active_identity(monkeypatch):
    monkeypatch.setattr(ov_server_module, "load_config", lambda: _make_config("root"))

    client = VikingClient(
        agent_id="workspace#channel",
        connection={
            "server_url": "http://studio.local",
            "api_key": "anonymous-key",
            "account_id": "acct",
            "user_id": "anonymous",
            "agent_id": "web-playground",
            "role": "user",
            "namespace_policy": {
                "isolate_user_scope_by_agent": True,
                "isolate_agent_scope_by_user": True,
            },
        },
    )

    first = _DummyHTTPClient.instances[0]
    assert client.api_key_type == "user"
    assert client.account_id == "acct"
    assert client.admin_user_id == "anonymous"
    assert client.agent_id == "web-playground"
    assert client._apikey_manager is None
    assert client._namespace_policy_loaded is True
    assert client.should_sender_fanout() is False
    assert client._memory_target_uri(None) == "viking://user/memories/"
    assert first.kwargs == {
        "url": "http://studio.local",
        "api_key": "anonymous-key",
        "profile_enabled": False,
    }


def test_viking_client_request_connection_preserves_admin_scope(monkeypatch):
    monkeypatch.setattr(ov_server_module, "load_config", lambda: _make_config("root"))

    VikingClient(
        agent_id="workspace#channel",
        connection={
            "server_url": "http://studio.local",
            "api_key": "admin-key",
            "account_id": "acct",
            "user_id": "default",
            "agent_id": "web-playground",
            "role": "admin",
        },
    )

    first = _DummyHTTPClient.instances[0]
    assert first.kwargs == {
        "url": "http://studio.local",
        "api_key": "admin-key",
        "profile_enabled": False,
        "account": "acct",
        "user": "default",
    }


@pytest.mark.asyncio
async def test_commit_user_mode_ignores_user_specific_key_flow(monkeypatch):
    monkeypatch.setattr(ov_server_module, "load_config", lambda: _make_config("user"))
    client = VikingClient()

    async def _must_not_call(*_args, **_kwargs):
        raise AssertionError("user mode should not call user management path")

    monkeypatch.setattr(client, "_check_user_exists", _must_not_call)
    monkeypatch.setattr(client, "_initialize_user", _must_not_call)
    monkeypatch.setattr(client, "_get_or_create_user_apikey", _must_not_call)

    result = await client.commit(
        session_id="sess",
        messages=[{"role": "user", "content": "hello", "tools_used": []}],
        user_id="sender-1",
    )

    assert result["success"] is True


@pytest.mark.asyncio
async def test_commit_request_connection_bypasses_cached_user_key_flow(monkeypatch):
    monkeypatch.setattr(ov_server_module, "load_config", lambda: _make_config("root"))
    client = VikingClient(
        agent_id="workspace",
        connection={
            "server_url": "http://studio.local",
            "api_key": "anonymous-key",
            "account_id": "acct",
            "user_id": "anonymous",
            "agent_id": "web-playground",
        },
    )

    async def _must_not_call(*_args, **_kwargs):
        raise AssertionError("request connection should not call user key management path")

    monkeypatch.setattr(client, "_check_user_exists", _must_not_call)
    monkeypatch.setattr(client, "_initialize_user", _must_not_call)
    monkeypatch.setattr(client, "_get_or_create_user_apikey", _must_not_call)

    result = await client.commit(
        session_id="sess",
        messages=[{"role": "user", "content": "hello", "tools_used": []}],
        user_id="anonymous",
    )

    assert result["success"] is True
    assert [inst.kwargs["api_key"] for inst in _DummyHTTPClient.instances] == ["anonymous-key"]


@pytest.mark.asyncio
async def test_request_connection_search_memory_uses_request_client_only(monkeypatch):
    monkeypatch.setattr(ov_server_module, "load_config", lambda: _make_config("root"))
    client = VikingClient(
        agent_id="workspace",
        connection={
            "server_url": "http://studio.local",
            "api_key": "anonymous-key",
            "account_id": "acct",
            "user_id": "anonymous",
            "agent_id": "web-playground",
            "role": "user",
            "namespace_policy": {
                "isolate_user_scope_by_agent": False,
                "isolate_agent_scope_by_user": False,
            },
        },
    )

    async def _must_not_call(*_args, **_kwargs):
        raise AssertionError("request connection should not call user management path")

    monkeypatch.setattr(client, "_initialize_user", _must_not_call)
    monkeypatch.setattr(client, "_get_or_create_user_apikey", _must_not_call)

    result = await client.search_memory(
        query="php",
        user_ids=["anonymous"],
        agent_user_id="anonymous",
        limit=10,
    )

    assert result == {"user_memory": [], "agent_memory": []}
    first = _DummyHTTPClient.instances[0]
    assert len(first.find_calls) == 2
    assert first.find_calls[0][1]["target_uri"] == "viking://user/memories/"
    assert first.find_calls[1][1]["target_uri"] == "viking://agent/web-playground/memories/"


@pytest.mark.asyncio
async def test_commit_root_mode_uses_sender_user_key(monkeypatch):
    monkeypatch.setattr(ov_server_module, "load_config", lambda: _make_config("root"))
    client = VikingClient()

    async def _exists(_user_id):
        return True

    async def _user_key(_user_id):
        return "user-key-1"

    monkeypatch.setattr(client, "_check_user_exists", _exists)
    monkeypatch.setattr(client, "_get_or_create_user_apikey", _user_key)
    client._apikey_manager = object()

    result = await client.commit(
        session_id="sess",
        messages=[{"role": "user", "content": "hello", "tools_used": []}],
        user_id="sender-2",
    )

    assert result["success"] is True
    assert any(inst.kwargs.get("api_key") == "user-key-1" for inst in _DummyHTTPClient.instances)


@pytest.mark.asyncio
async def test_compact_hook_user_mode_commits_once(monkeypatch):
    from vikingbot.hooks.builtins import openviking_hooks as hooks_module

    monkeypatch.setattr(hooks_module, "load_config", lambda: _make_config("user"))

    class _FakeClient:
        def __init__(self):
            self.calls = []

        def should_sender_fanout(self):
            return False

        def session_owner_user_id(self):
            return None

        async def commit(self, session_id, messages, user_id=None):
            self.calls.append((session_id, user_id, len(messages)))
            return {"success": "committed"}

    fake_client = _FakeClient()
    hook = OpenVikingCompactHook()

    async def _fake_get_client(_workspace_id):
        return fake_client

    monkeypatch.setattr(hook, "_get_client", _fake_get_client)

    context = HookContext(
        event_type="message.compact",
        workspace_id="ws",
        session_key=SessionKey(type="cli", channel_id="default", chat_id="chat-1"),
    )
    session = SimpleNamespace(
        messages=[
            {"sender_id": "admin", "role": "assistant", "content": "a"},
            {"sender_id": "u1", "role": "user", "content": "b"},
            {"sender_id": "u2", "role": "user", "content": "c"},
        ]
    )

    result = await hook.execute(context, session=session)

    assert result["success"] is True
    assert result["users_count"] == 0
    assert len(fake_client.calls) == 1
    assert fake_client.calls[0][0] == "cli__default__chat-1"
    assert fake_client.calls[0][1] is None


@pytest.mark.asyncio
async def test_compact_hook_session_context_commits_single_session_with_peer_messages(monkeypatch):
    from vikingbot.hooks.builtins import openviking_hooks as hooks_module

    monkeypatch.setattr(
        hooks_module,
        "load_config",
        lambda: _make_config(
            "root",
            session_context_enabled=True,
            commit_token_threshold=100,
            commit_keep_recent_count=2,
        ),
    )

    class _FakeClient:
        def __init__(self):
            self.pending_tokens = [120, 0]
            self.append_calls = []
            self.session_calls = []
            self.commit_calls = []

        def session_owner_user_id(self):
            return "admin"

        async def append_messages(
            self,
            session_id,
            messages,
            default_user_peer_id=None,
            session_user_id=None,
        ):
            self.append_calls.append(
                (
                    session_id,
                    [message["content"] for message in messages],
                    default_user_peer_id,
                    session_user_id,
                )
            )
            return {"session_id": session_id, "added": len(messages)}

        async def get_session(self, session_id, user_id=None):
            self.session_calls.append((session_id, user_id))
            pending_tokens = self.pending_tokens.pop(0) if self.pending_tokens else 0
            return {"session_id": session_id, "pending_tokens": pending_tokens}

        async def commit_session(self, session_id, keep_recent_count=0, user_id=None):
            self.commit_calls.append((session_id, keep_recent_count, user_id))
            return {"session_id": session_id, "status": "accepted"}

    fake_client = _FakeClient()
    hook = OpenVikingCompactHook()

    async def _fake_get_client(_workspace_id):
        return fake_client

    monkeypatch.setattr(hook, "_get_client", _fake_get_client)

    context = HookContext(
        event_type="message.compact",
        workspace_id="ws",
        session_key=SessionKey(type="cli", channel_id="default", chat_id="chat-1"),
    )
    session = SimpleNamespace(
        messages=[
            {"sender_id": "admin", "role": "assistant", "content": "admin answer"},
            {"sender_id": "u1", "role": "user", "content": "u1 asks"},
            {"sender_id": "u1", "role": "assistant", "content": "u1 reply"},
            {"sender_id": "u2", "role": "user", "content": "u2 asks"},
        ],
        metadata={},
    )

    result = await hook.execute(context, session=session)

    assert result["success"] is True
    assert result["admin_result"]["committed"] is True
    assert result["users_count"] == 0
    assert fake_client.append_calls == [
        (
            "cli__default__chat-1",
            ["admin answer", "u1 asks", "u1 reply", "u2 asks"],
            None,
            "admin",
        )
    ]
    assert fake_client.commit_calls == [("cli__default__chat-1", 2, "admin")]

    state = session.metadata["openviking"]
    assert state["session_id"] == "cli__default__chat-1"
    assert state["last_synced_local_index"] == len(session.messages) - 1
    assert state["last_pending_tokens"] == 0
    assert state["last_sync_status"] == "success"
    assert state["last_commit_local_index"] == len(session.messages) - 1
    assert "last_commit_at" in state


@pytest.mark.asyncio
async def test_reset_openviking_state_replaces_persisted_sender_cursors(temp_dir):
    manager = SessionManager(temp_dir / "bot")
    session_key = SessionKey(type="cli", channel_id="default", chat_id="chat-1")
    session = manager.get_or_create(session_key, skip_heartbeat=True)
    session.metadata["openviking"] = {
        "session_id": session_key.safe_name(),
        "last_synced_local_index": 19,
        "last_sender_synced_local_indexes": {"user-1": 19},
        "last_pending_tokens": 100,
        "last_commit_local_index": 19,
        "last_commit_performed": True,
        "last_sync_error": "old error",
    }
    await manager.save(session)

    session.clear()
    reset_openviking_state(session)
    await manager.save(session)

    manager._cache.clear()
    persisted_session = manager.get_or_create(session_key, skip_heartbeat=True)
    state = persisted_session.metadata["openviking"]
    assert state == {
        "session_id": session_key.safe_name(),
        "last_synced_local_index": -1,
        "last_sender_synced_local_indexes": {},
        "last_pending_tokens": 0,
        "last_commit_local_index": -1,
        "last_sync_status": "reset",
    }
    assert persisted_session.messages == []


@pytest.mark.asyncio
async def test_compact_hook_force_commit_does_not_resync_already_synced_messages(monkeypatch):
    from vikingbot.hooks.builtins import openviking_hooks as hooks_module

    monkeypatch.setattr(
        hooks_module,
        "load_config",
        lambda: _make_config(
            "root",
            session_context_enabled=True,
            commit_token_threshold=100,
            commit_keep_recent_count=2,
        ),
    )

    class _FakeClient:
        def __init__(self):
            self.append_calls = []
            self.commit_calls = []

        def session_owner_user_id(self):
            return "admin"

        async def append_messages(
            self,
            session_id,
            messages,
            default_user_peer_id=None,
            session_user_id=None,
        ):
            self.append_calls.append((session_id, [message["content"] for message in messages]))
            return {"session_id": session_id, "added": len(messages)}

        async def get_session(self, session_id, user_id=None):
            return {"session_id": session_id, "pending_tokens": 120}

        async def commit_session(self, session_id, keep_recent_count=0, user_id=None):
            self.commit_calls.append((session_id, keep_recent_count, user_id))
            return {"session_id": session_id, "status": "accepted"}

    fake_client = _FakeClient()
    hook = OpenVikingCompactHook()

    async def _fake_get_client(_workspace_id):
        return fake_client

    monkeypatch.setattr(hook, "_get_client", _fake_get_client)

    context = HookContext(
        event_type="message.compact",
        workspace_id="ws",
        session_key=SessionKey(type="cli", channel_id="default", chat_id="chat-1"),
    )
    session = SimpleNamespace(
        messages=[
            {"sender_id": "u1", "role": "user", "content": "already synced"},
            {"sender_id": "u1", "role": "assistant", "content": "new reply"},
        ],
        metadata={
            "openviking": {
                "session_id": "cli__default__chat-1",
                "last_synced_local_index": 0,
                "last_pending_tokens": 120,
            }
        },
    )

    result = await hook.execute(context, session=session, force_commit=True)

    assert result["success"] is True
    assert fake_client.append_calls == [("cli__default__chat-1", ["new reply"])]
    assert fake_client.commit_calls == [("cli__default__chat-1", 2, "admin")]
    assert session.metadata["openviking"]["last_synced_local_index"] == 1
    assert session.metadata["openviking"]["last_commit_local_index"] == 1


@pytest.mark.asyncio
async def test_compact_hook_force_commit_commits_current_session_without_unsynced_messages(
    monkeypatch,
):
    from vikingbot.hooks.builtins import openviking_hooks as hooks_module

    monkeypatch.setattr(
        hooks_module,
        "load_config",
        lambda: _make_config(
            "root",
            session_context_enabled=True,
            commit_token_threshold=1000,
            commit_keep_recent_count=2,
        ),
    )

    class _FakeClient:
        def __init__(self):
            self.append_calls = []
            self.commit_calls = []

        def session_owner_user_id(self):
            return "admin"

        async def append_messages(
            self,
            session_id,
            messages,
            default_user_peer_id=None,
            session_user_id=None,
        ):
            self.append_calls.append((session_id, [message["content"] for message in messages]))
            return {"session_id": session_id, "added": len(messages)}

        async def get_session(self, session_id, user_id=None):
            return {"session_id": session_id, "pending_tokens": 120}

        async def commit_session(self, session_id, keep_recent_count=0, user_id=None):
            self.commit_calls.append((session_id, keep_recent_count, user_id))
            return {"session_id": session_id, "status": "accepted"}

    fake_client = _FakeClient()
    hook = OpenVikingCompactHook()

    async def _fake_get_client(_workspace_id):
        return fake_client

    monkeypatch.setattr(hook, "_get_client", _fake_get_client)

    context = HookContext(
        event_type="message.compact",
        workspace_id="ws",
        session_key=SessionKey(type="cli", channel_id="default", chat_id="chat-1"),
    )
    session = SimpleNamespace(
        messages=[
            {"sender_id": "u1", "role": "user", "content": "already synced"},
            {"sender_id": "u2", "role": "user", "content": "also synced"},
        ],
        metadata={
            "openviking": {
                "session_id": "cli__default__chat-1",
                "last_synced_local_index": 1,
                "last_pending_tokens": 120,
            }
        },
    )

    result = await hook.execute(context, session=session, force_commit=True)

    assert result["success"] is True
    assert fake_client.append_calls == []
    assert fake_client.commit_calls == [("cli__default__chat-1", 2, "admin")]
    assert session.metadata["openviking"]["last_commit_performed"] is True


@pytest.mark.asyncio
async def test_compact_hook_session_context_append_failure_does_not_advance_sync_cursor(
    monkeypatch,
):
    from vikingbot.hooks.builtins import openviking_hooks as hooks_module

    monkeypatch.setattr(
        hooks_module,
        "load_config",
        lambda: _make_config(
            "root",
            session_context_enabled=True,
            commit_token_threshold=100,
            commit_keep_recent_count=2,
        ),
    )

    class _FakeClient:
        def __init__(self):
            self.append_calls = []
            self.commit_calls = []

        def session_owner_user_id(self):
            return "admin"

        async def append_messages(
            self,
            session_id,
            messages,
            default_user_peer_id=None,
            session_user_id=None,
        ):
            self.append_calls.append((session_id, [message["content"] for message in messages]))
            if session_id == "cli__default__chat-1":
                raise RuntimeError("session append failed")
            return {"session_id": session_id, "added": len(messages)}

        async def get_session(self, session_id, user_id=None):
            return {"session_id": session_id, "pending_tokens": 120}

        async def commit_session(self, session_id, keep_recent_count=0, user_id=None):
            self.commit_calls.append((session_id, keep_recent_count, user_id))
            return {"session_id": session_id, "status": "accepted"}

    fake_client = _FakeClient()
    hook = OpenVikingCompactHook()

    async def _fake_get_client(_workspace_id):
        return fake_client

    monkeypatch.setattr(hook, "_get_client", _fake_get_client)

    context = HookContext(
        event_type="message.compact",
        workspace_id="ws",
        session_key=SessionKey(type="cli", channel_id="default", chat_id="chat-1"),
    )
    session = SimpleNamespace(
        messages=[
            {"sender_id": "u1", "role": "user", "content": "u1 asks"},
            {"sender_id": "u1", "role": "assistant", "content": "u1 reply"},
        ],
        metadata={
            "openviking": {"session_id": "cli__default__chat-1", "last_synced_local_index": -1}
        },
    )

    result = await hook.execute(context, session=session)

    assert result["success"] is False
    assert "session append failed" in result["error"]
    assert fake_client.append_calls == [("cli__default__chat-1", ["u1 asks", "u1 reply"])]
    assert fake_client.commit_calls == []
    state = session.metadata["openviking"]
    assert state["last_sync_status"] == "error"
    assert "session append failed" in state["last_sync_error"]
    assert state["last_synced_local_index"] == -1
    assert state.get("last_commit_performed") is not True


@pytest.mark.asyncio
async def test_compact_hook_session_context_commits_when_message_threshold_reached(
    monkeypatch,
):
    from vikingbot.hooks.builtins import openviking_hooks as hooks_module

    monkeypatch.setattr(
        hooks_module,
        "load_config",
        lambda: _make_config(
            "root",
            session_context_enabled=True,
            commit_token_threshold=1000,
            commit_keep_recent_count=2,
        ),
    )

    class _FakeClient:
        def __init__(self):
            self.append_calls = []
            self.commit_calls = []

        def session_owner_user_id(self):
            return "admin"

        async def append_messages(
            self,
            session_id,
            messages,
            default_user_peer_id=None,
            session_user_id=None,
        ):
            self.append_calls.append((session_id, [message["content"] for message in messages]))
            return {"session_id": session_id, "added": len(messages)}

        async def get_session(self, session_id, user_id=None):
            return {"session_id": session_id, "pending_tokens": 0}

        async def commit_session(self, session_id, keep_recent_count=0, user_id=None):
            self.commit_calls.append((session_id, keep_recent_count, user_id))
            return {"session_id": session_id, "status": "accepted"}

    fake_client = _FakeClient()
    hook = OpenVikingCompactHook()

    async def _fake_get_client(_workspace_id):
        return fake_client

    monkeypatch.setattr(hook, "_get_client", _fake_get_client)

    context = HookContext(
        event_type="message.compact",
        workspace_id="ws",
        session_key=SessionKey(type="cli", channel_id="default", chat_id="chat-1"),
    )
    session = SimpleNamespace(
        messages=[
            {"sender_id": "u1", "role": "user", "content": "m1"},
            {"sender_id": "u1", "role": "assistant", "content": "m2"},
            {"sender_id": "u1", "role": "user", "content": "m3"},
        ],
        metadata={
            "openviking": {
                "session_id": "cli__default__chat-1",
                "last_synced_local_index": 1,
                "last_pending_tokens": 0,
                "last_commit_local_index": -1,
            }
        },
    )

    result = await hook.execute(context, session=session, commit_message_threshold=3)

    assert result["success"] is True
    assert result["admin_result"]["committed"] is True
    assert fake_client.append_calls == [("cli__default__chat-1", ["m3"])]
    assert fake_client.commit_calls == [("cli__default__chat-1", 2, "admin")]
    assert session.metadata["openviking"]["last_commit_local_index"] == 2


@pytest.mark.asyncio
async def test_compact_hook_session_commit_failure_retries_without_resyncing_messages(
    monkeypatch,
):
    from vikingbot.hooks.builtins import openviking_hooks as hooks_module

    monkeypatch.setattr(
        hooks_module,
        "load_config",
        lambda: _make_config(
            "root",
            session_context_enabled=True,
            commit_token_threshold=100,
            commit_keep_recent_count=2,
        ),
    )

    class _FakeClient:
        def __init__(self):
            self.append_calls = []
            self.commit_calls = []
            self.fail_session_commit = True

        def session_owner_user_id(self):
            return "admin"

        async def append_messages(
            self,
            session_id,
            messages,
            default_user_peer_id=None,
            session_user_id=None,
        ):
            self.append_calls.append((session_id, [message["content"] for message in messages]))
            return {"session_id": session_id, "added": len(messages)}

        async def get_session(self, session_id, user_id=None):
            return {"session_id": session_id, "pending_tokens": 120}

        async def commit_session(self, session_id, keep_recent_count=0, user_id=None):
            self.commit_calls.append((session_id, keep_recent_count, user_id))
            if session_id == "cli__default__chat-1" and self.fail_session_commit:
                raise RuntimeError("session commit failed")
            return {"session_id": session_id, "status": "accepted"}

    fake_client = _FakeClient()
    hook = OpenVikingCompactHook()

    async def _fake_get_client(_workspace_id):
        return fake_client

    monkeypatch.setattr(hook, "_get_client", _fake_get_client)

    context = HookContext(
        event_type="message.compact",
        workspace_id="ws",
        session_key=SessionKey(type="cli", channel_id="default", chat_id="chat-1"),
    )
    session = SimpleNamespace(
        messages=[
            {"sender_id": "u1", "role": "user", "content": "u1 asks"},
            {"sender_id": "u1", "role": "assistant", "content": "u1 reply"},
        ],
        metadata={"openviking": {"session_id": "cli__default__chat-1"}},
    )

    first = await hook.execute(context, session=session)

    assert first["success"] is False
    assert "session commit failed" in first["error"]
    assert fake_client.append_calls == [("cli__default__chat-1", ["u1 asks", "u1 reply"])]
    assert fake_client.commit_calls == [("cli__default__chat-1", 2, "admin")]
    state = session.metadata["openviking"]
    assert state["last_sync_status"] == "error"
    assert state["last_synced_local_index"] == 1
    assert state.get("last_commit_performed") is not True

    fake_client.fail_session_commit = False
    fake_client.append_calls.clear()
    fake_client.commit_calls.clear()

    second = await hook.execute(context, session=session, force_commit=True)

    assert second["success"] is True
    assert fake_client.append_calls == []
    assert fake_client.commit_calls == [("cli__default__chat-1", 2, "admin")]
    assert session.metadata["openviking"]["last_commit_performed"] is True


@pytest.mark.asyncio
async def test_compact_hook_session_context_skips_message_threshold_after_recent_commit(
    monkeypatch,
):
    from vikingbot.hooks.builtins import openviking_hooks as hooks_module

    monkeypatch.setattr(
        hooks_module,
        "load_config",
        lambda: _make_config(
            "root",
            session_context_enabled=True,
            commit_token_threshold=1000,
            commit_keep_recent_count=2,
        ),
    )

    class _FakeClient:
        def __init__(self):
            self.append_calls = []
            self.commit_calls = []

        def session_owner_user_id(self):
            return "admin"

        async def append_messages(
            self,
            session_id,
            messages,
            default_user_peer_id=None,
            session_user_id=None,
        ):
            self.append_calls.append((session_id, [message["content"] for message in messages]))
            return {"session_id": session_id, "added": len(messages)}

        async def get_session(self, session_id, user_id=None):
            return {"session_id": session_id, "pending_tokens": 0}

        async def commit_session(self, session_id, keep_recent_count=0, user_id=None):
            self.commit_calls.append((session_id, keep_recent_count, user_id))
            return {"session_id": session_id, "status": "accepted"}

    fake_client = _FakeClient()
    hook = OpenVikingCompactHook()

    async def _fake_get_client(_workspace_id):
        return fake_client

    monkeypatch.setattr(hook, "_get_client", _fake_get_client)

    context = HookContext(
        event_type="message.compact",
        workspace_id="ws",
        session_key=SessionKey(type="cli", channel_id="default", chat_id="chat-1"),
    )
    session = SimpleNamespace(
        messages=[
            {"sender_id": "u1", "role": "user", "content": "m1"},
            {"sender_id": "u1", "role": "assistant", "content": "m2"},
            {"sender_id": "u1", "role": "user", "content": "m3"},
        ],
        metadata={
            "openviking": {
                "session_id": "cli__default__chat-1",
                "last_synced_local_index": 1,
                "last_pending_tokens": 0,
                "last_commit_local_index": 1,
            }
        },
    )

    result = await hook.execute(context, session=session, commit_message_threshold=3)

    assert result["success"] is True
    assert result["admin_result"]["committed"] is False
    assert fake_client.append_calls == [("cli__default__chat-1", ["m3"])]
    assert fake_client.commit_calls == []


@pytest.mark.asyncio
async def test_viking_client_normalizes_system_tool_and_tool_result_messages(monkeypatch):
    monkeypatch.setattr(ov_server_module, "load_config", lambda: _make_config("root"))
    client = VikingClient(workspace_id="workspace")

    normalized = client._normalize_session_messages(
        [
            {
                "role": "system",
                "content": "system context",
                "timestamp": "2026-05-01T12:00:00Z",
            },
            {
                "role": "tool",
                "content": "tool response",
                "timestamp": "2026-05-01T12:00:01Z",
            },
            {
                "role": "assistant",
                "content": "assistant answer",
                "tools_used": [
                    {
                        "tool_name": "read_file",
                        "args": {"path": "README.md"},
                        "result": "file content",
                    }
                ],
                "timestamp": "2026-05-01T12:00:02Z",
            },
        ],
        default_user_peer_id="admin",
    )

    assert [message["role"] for message in normalized] == [
        "assistant",
        "assistant",
        "assistant",
    ]
    assert all("peer_id" not in message for message in normalized)
    assert normalized[0]["content"] == "system context"
    assert normalized[1]["content"] == "tool response"
    assert normalized[2]["content"] == "assistant answer"
    assert normalized[2]["parts"][1]["type"] == "tool"
    assert normalized[2]["parts"][1]["tool_name"] == "read_file"


@pytest.mark.asyncio
async def test_viking_client_append_messages_chunks_batches_at_server_limit(monkeypatch):
    monkeypatch.setattr(ov_server_module, "load_config", lambda: _make_config("root"))
    client = VikingClient(workspace_id="workspace")

    async def _exists(_session_id):
        return True

    calls = []

    async def _batch_add_messages(session_id, messages):
        calls.append((session_id, list(messages)))
        return {
            "session_id": session_id,
            "added": len(messages),
            "message_count": sum(len(batch) for _, batch in calls),
        }

    monkeypatch.setattr(client.client, "session_exists", _exists)
    monkeypatch.setattr(client.client, "batch_add_messages", _batch_add_messages)

    result = await client.append_messages(
        "session-1",
        [{"role": "user", "content": f"message {index}"} for index in range(101)],
        default_user_peer_id="admin",
    )

    assert [len(messages) for _, messages in calls] == [100, 1]
    assert result == {"session_id": "session-1", "added": 101, "message_count": 101}


@pytest.mark.asyncio
async def test_search_memory_uses_user_namespace(monkeypatch):
    monkeypatch.setattr(ov_server_module, "load_config", lambda: _make_config("root"))
    client = VikingClient()

    calls = []

    class _Result:
        memories = []

    async def _exists(_user_id):
        return True

    async def _find(*, query, target_uri, limit):
        calls.append(target_uri)
        return _Result()

    monkeypatch.setattr(client, "_check_user_exists", _exists)
    monkeypatch.setattr(client.client, "find", _find)

    await client.search_memory("hello", "sender-1", limit=5)

    assert calls == ["viking://user/sender-1/memories/"]


@pytest.mark.asyncio
async def test_search_memory_uses_user_namespace_without_agent_scope(monkeypatch):
    monkeypatch.setattr(ov_server_module, "load_config", lambda: _make_config("root"))
    client = VikingClient()

    calls = []

    class _Result:
        memories = []

    async def _exists(_user_id):
        return True

    async def _find(*, query, target_uri, limit):
        calls.append(target_uri)
        return _Result()

    monkeypatch.setattr(client, "_check_user_exists", _exists)
    monkeypatch.setattr(client.client, "find", _find)

    await client.search_memory("hello", "sender-1", limit=5)

    assert calls == ["viking://user/sender-1/memories/"]


@pytest.mark.asyncio
async def test_skill_memory_uri_uses_user_memory_namespace(monkeypatch):
    monkeypatch.setattr(ov_server_module, "load_config", lambda: _make_config("root"))
    client = VikingClient()

    assert (
        client._skill_memory_uri("planner", "admin")
        == "viking://user/admin/memories/skills/planner.md"
    )


def test_openviking_grep_schema_requires_single_string_pattern():
    tool = VikingGrepTool()

    assert tool.parameters["properties"]["pattern"]["type"] == "string"


@pytest.mark.asyncio
async def test_openviking_grep_keeps_explicit_resource_uri(monkeypatch):
    tool = VikingGrepTool()
    calls = []

    class _FakeClient:
        admin_user_id = "admin"

        async def grep(self, uri, pattern, case_insensitive=False, user_id=None):
            calls.append((uri, pattern, case_insensitive, user_id))
            return {
                "matches": [
                    {
                        "uri": "viking://resources/doc.md",
                        "line": 3,
                        "content": "hello admin scoped grep",
                    }
                ]
            }

    async def _fake_get_client(_tool_context):
        return _FakeClient()

    monkeypatch.setattr(tool, "_get_client", _fake_get_client)

    result = await tool.execute(
        SimpleNamespace(workspace_id="workspace"),
        uri="viking://resources/",
        pattern="hello",
        case_insensitive=True,
    )

    assert calls == [("viking://resources/", "hello", True, None)]
    assert "Found 1 match for pattern 'hello':" in result
    assert "viking://resources/doc.md" in result


def test_openviking_tool_memory_peer_ids_exclude_legacy_memory_users():
    tool = VikingSearchTool()

    peer_ids = tool._memory_peer_ids(
        SimpleNamespace(
            sender_id="sender-1",
            memory_peer_ids=["speaker-a"],
            memory_user_ids=["legacy-user"],
        )
    )

    assert peer_ids == ["sender-1", "speaker-a"]


def test_tool_context_syncs_legacy_memory_user_alias():
    from_legacy = ToolContext(memory_user_ids=["legacy-user"])
    from_owner = ToolContext(memory_owner_user_ids=["owner-user"])

    assert from_legacy.memory_owner_user_ids == ["legacy-user"]
    assert from_owner.memory_user_ids == ["owner-user"]


@pytest.mark.asyncio
async def test_viking_memory_context_keeps_legacy_users_separate_from_peers(
    monkeypatch, tmp_path
):
    calls = []

    class _FakeClient:
        async def search_memory(self, **kwargs):
            calls.append(kwargs)
            return []

        async def close(self):
            return None

    async def _fake_create(**_kwargs):
        return _FakeClient()

    monkeypatch.setattr("vikingbot.agent.memory.load_config", lambda: _make_config("root"))
    monkeypatch.setattr("vikingbot.agent.memory.VikingClient.create", _fake_create)

    store = MemoryStore(tmp_path)

    await store.get_viking_memory_context(
        current_message="hello",
        workspace_id="workspace",
        sender_id="sender-1",
        peer_ids=["speaker-a"],
        user_ids=["legacy-user"],
    )

    assert calls == [
        {
            "query": "hello",
            "user_ids": ["legacy-user"],
            "peer_ids": ["sender-1", "speaker-a"],
            "limit": 10,
        }
    ]


@pytest.mark.asyncio
async def test_openviking_search_uses_user_namespace(monkeypatch):
    monkeypatch.setattr(ov_server_module, "load_config", lambda: _make_config("root"))
    tool = VikingSearchTool()
    client = VikingClient()

    calls = []

    async def _search(query, target_uri=None, limit=20, user_id=None, peer_id=None):
        calls.append((target_uri, user_id, peer_id))
        return {"memories": [{"uri": target_uri, "abstract": "a", "score": 0.9, "is_leaf": True}]}

    async def _fake_get_client(_tool_context):
        return client

    monkeypatch.setattr(client, "search", _search)
    monkeypatch.setattr(tool, "_get_client", _fake_get_client)

    tool_context = SimpleNamespace(workspace_id="workspace", memory_owner_user_ids=["sender-1"])
    result = await tool.execute(tool_context, query="hello")

    assert "sender-1/memories" in result
    assert calls == [
        ("viking://resources/", None, None),
        ("viking://user/sender-1/memories/", None, None),
        ("viking://user/sender-1/skills/", None, None),
    ]


@pytest.mark.asyncio
async def test_openviking_search_user_key_mode_uses_current_user_namespace(monkeypatch):
    monkeypatch.setattr(ov_server_module, "load_config", lambda: _make_config("user"))
    tool = VikingSearchTool()
    client = VikingClient()

    calls = []

    async def _search(query, target_uri=None, limit=20, user_id=None, peer_id=None):
        calls.append((target_uri, user_id, peer_id))
        return {"memories": [{"uri": target_uri, "abstract": "a", "score": 0.9, "is_leaf": True}]}

    async def _fake_get_client(_tool_context):
        return client

    monkeypatch.setattr(client, "search", _search)
    monkeypatch.setattr(tool, "_get_client", _fake_get_client)

    tool_context = SimpleNamespace(
        workspace_id="workspace",
        sender_id="sender-0",
        memory_peer_ids=["sender-1", "sender-2"],
    )
    result = await tool.execute(tool_context, query="hello")

    assert "sender-1/memories" in result
    assert calls == [
        ("", None, "sender-0"),
        ("viking://user/peers/sender-1/memories/", None, None),
        ("viking://user/peers/sender-2/memories/", None, None),
    ]


@pytest.mark.asyncio
async def test_openviking_grep_default_memory_expands_current_peer(monkeypatch):
    tool = VikingGrepTool()
    calls = []

    class _FakeClient:
        def _memory_target_uri(self, _user_id=None):
            return "viking://user/memories/"

        def build_current_memory_target_uris(self, *, peer_ids=None, include_self=True):
            uris = ["viking://user/memories/"] if include_self else []
            uris.extend(f"viking://user/default/peers/{peer_id}/memories/" for peer_id in peer_ids or [])
            return uris

        async def grep(self, uri, pattern, case_insensitive=False, user_id=None):
            calls.append((uri, pattern, case_insensitive, user_id))
            return {"matches": []}

    async def _fake_get_client(_tool_context):
        return _FakeClient()

    monkeypatch.setattr(tool, "_get_client", _fake_get_client)

    await tool.execute(
        SimpleNamespace(workspace_id="workspace", sender_id="sender-0"),
        uri="viking://user/memories/",
        pattern="hello",
    )

    assert calls == [
        ("viking://user/memories/", "hello", False, None),
        ("viking://user/default/peers/sender-0/memories/", "hello", False, None),
    ]


@pytest.mark.asyncio
async def test_openviking_list_default_memory_expands_current_peer(monkeypatch):
    tool = VikingListTool()
    calls = []

    class _FakeClient:
        def _memory_target_uri(self, _user_id=None):
            return "viking://user/memories/"

        def build_current_memory_target_uris(self, *, peer_ids=None, include_self=True):
            uris = ["viking://user/memories/"] if include_self else []
            uris.extend(f"viking://user/default/peers/{peer_id}/memories/" for peer_id in peer_ids or [])
            return uris

        async def list_resources(self, path=None, recursive=False):
            calls.append((path, recursive))
            return []

    async def _fake_get_client(_tool_context):
        return _FakeClient()

    monkeypatch.setattr(tool, "_get_client", _fake_get_client)

    await tool.execute(
        SimpleNamespace(workspace_id="workspace", sender_id="sender-0"),
        uri="viking://user/memories/",
    )

    assert calls == [
        ("viking://user/memories/", False),
        ("viking://user/default/peers/sender-0/memories/", False),
    ]


@pytest.mark.asyncio
async def test_openviking_glob_root_adds_current_peer_memory(monkeypatch):
    tool = VikingGlobTool()
    calls = []

    class _FakeClient:
        def _memory_target_uri(self, _user_id=None):
            return "viking://user/memories/"

        def build_current_memory_target_uris(self, *, peer_ids=None, include_self=True):
            uris = ["viking://user/memories/"] if include_self else []
            uris.extend(f"viking://user/default/peers/{peer_id}/memories/" for peer_id in peer_ids or [])
            return uris

        async def glob(self, pattern, uri="viking://"):
            calls.append((pattern, uri))
            return {"matches": [], "count": 0}

    async def _fake_get_client(_tool_context):
        return _FakeClient()

    monkeypatch.setattr(tool, "_get_client", _fake_get_client)

    await tool.execute(
        SimpleNamespace(workspace_id="workspace", sender_id="sender-0"),
        pattern="*.md",
    )

    assert calls == [
        ("*.md", "viking://resources/"),
        ("*.md", "viking://user/memories/"),
        ("*.md", "viking://user/skills/"),
        ("*.md", "viking://user/default/peers/sender-0/memories/"),
    ]


@pytest.mark.asyncio
async def test_openviking_glob_root_uses_namespaced_self_targets_for_root_key(monkeypatch):
    tool = VikingGlobTool()
    calls = []

    class _FakeClient:
        def _memory_target_uri(self, _user_id=None):
            return "viking://user/admin/memories/"

        def build_current_memory_target_uris(self, *, peer_ids=None, include_self=True):
            uris = ["viking://user/admin/memories/"] if include_self else []
            uris.extend(
                f"viking://user/admin/peers/{peer_id}/memories/"
                for peer_id in peer_ids or []
            )
            return uris

        async def glob(self, pattern, uri="viking://"):
            calls.append((pattern, uri))
            return {"matches": [], "count": 0}

    async def _fake_get_client(_tool_context):
        return _FakeClient()

    monkeypatch.setattr(tool, "_get_client", _fake_get_client)

    await tool.execute(
        SimpleNamespace(workspace_id="workspace", sender_id="sender-0"),
        pattern="*.md",
    )

    assert calls == [
        ("*.md", "viking://resources/"),
        ("*.md", "viking://user/admin/memories/"),
        ("*.md", "viking://user/admin/skills/"),
        ("*.md", "viking://user/admin/peers/sender-0/memories/"),
    ]


def test_openviking_search_description_allows_follow_up_memory_queries():
    description = VikingSearchTool().description

    assert "follow-up" in description
    assert "different remembered fact" in description
    assert "before concluding no relevant record exists" in description
    assert "avoid repeated calls with similar queries" not in description.lower()


@pytest.mark.asyncio
async def test_context_reminds_agent_to_search_current_memory_question(tmp_path):
    class _EmptyMemory:
        async def get_viking_memory_context(self, **_kwargs):
            return ""

    context = ContextBuilder(workspace=tmp_path, sender_id="sender-1")
    context._memory = _EmptyMemory()

    user_info = await context._build_user_memory(
        session_key=SessionKey(type="cli", channel_id="default", chat_id="chat-1"),
        current_message="我会哪些语言",
        sender_id="sender-1",
        ov_tools_enable=True,
        is_first_round=False,
    )

    assert "OpenViking Memory Retrieval" in user_info
    assert "use openviking_search for the current question" in user_info
    assert "A previous empty search result does not prove" in user_info


@pytest.mark.asyncio
async def test_context_loads_profiles_for_memory_peers(tmp_path):
    calls = {"sender": [], "peers": []}

    class _ProfileMemory:
        async def get_viking_peer_profile(self, **kwargs):
            calls["sender"].append(kwargs["peer_id"])
            return "sender profile"

        async def get_viking_peer_profiles(self, **kwargs):
            calls["peers"].append(kwargs["peer_ids"])
            return "\n".join(f"profile for {peer_id}" for peer_id in kwargs["peer_ids"])

    context = ContextBuilder(workspace=tmp_path, sender_id="sender-1")
    context._memory = _ProfileMemory()

    system_prompt = await context.build_system_prompt(
        session_key=SessionKey(type="cli", channel_id="default", chat_id="chat-1"),
        ov_tools_enable=True,
        profile_user_list=["speaker-a"],
        memory_peer_ids=["sender-1", "speaker-a", "speaker-b"],
    )

    assert calls["sender"] == ["sender-1"]
    assert calls["peers"] == [["speaker-a", "speaker-b"]]
    assert "sender profile" in system_prompt
    assert "profile for speaker-a" in system_prompt
    assert "profile for speaker-b" in system_prompt


@pytest.mark.asyncio
async def test_openviking_memory_commit_prefers_sender_in_static_multi_user_bot(monkeypatch):
    tool = VikingMemoryCommitTool()
    calls = []

    class _FakeClient:
        admin_user_id = "default"

        async def commit(self, session_id, messages, peer_id=None):
            calls.append((session_id, messages, peer_id))
            return {"commit": {"archived": False}}

    async def _fake_get_client(_tool_context):
        return _FakeClient()

    monkeypatch.setattr(tool, "_get_client", _fake_get_client)

    tool_context = SimpleNamespace(
        workspace_id="workspace",
        sender_id="alice",
        session_key=SimpleNamespace(safe_name=lambda: "session-1"),
        openviking_connection=None,
    )
    result = await tool.execute(
        tool_context,
        messages=[{"role": "user", "content": "remember this"}],
    )
    second_result = await tool.execute(
        tool_context,
        messages=[{"role": "user", "content": "remember this again"}],
    )

    payload = json.loads(result)
    second_payload = json.loads(second_result)
    assert payload["status"] == "success"
    assert second_payload["status"] == "success"
    assert calls[0] == (
        payload["memory_commit_session_id"],
        [{"role": "user", "content": "remember this"}],
        "alice",
    )
    assert calls[1] == (
        second_payload["memory_commit_session_id"],
        [{"role": "user", "content": "remember this again"}],
        "alice",
    )
    assert payload["session_id"] == payload["memory_commit_session_id"]
    assert payload["source_session_id"] == "session-1"
    assert payload["memory_commit_session_id"].startswith("session-1__memory_commit__")
    assert second_payload["source_session_id"] == "session-1"
    assert second_payload["memory_commit_session_id"].startswith("session-1__memory_commit__")
    assert second_payload["memory_commit_session_id"] != payload["memory_commit_session_id"]


@pytest.mark.asyncio
async def test_openviking_hook_clients_are_cached_by_workspace(monkeypatch):
    openviking_hooks_module._global_clients.clear()
    created_workspace_ids = []

    class _FakeVikingClient:
        @classmethod
        async def create(cls, workspace_id=None):
            created_workspace_ids.append(workspace_id)
            return SimpleNamespace(workspace_id=workspace_id)

    monkeypatch.setattr(openviking_hooks_module, "VikingClient", _FakeVikingClient)

    ws_a_first = await openviking_hooks_module.get_global_client("workspace-a")
    ws_a_second = await openviking_hooks_module.get_global_client("workspace-a")
    ws_b = await openviking_hooks_module.get_global_client("workspace-b")

    assert ws_a_first is ws_a_second
    assert ws_a_first is not ws_b
    assert created_workspace_ids == ["workspace-a", "workspace-b"]
    openviking_hooks_module._global_clients.clear()


@pytest.mark.asyncio
async def test_openviking_tool_clients_are_cached_by_workspace(monkeypatch):
    created_workspace_ids = []

    class _FakeVikingClient:
        @classmethod
        async def create(cls, workspace_id=None, **kwargs):
            created_workspace_ids.append(workspace_id)
            return SimpleNamespace(workspace_id=workspace_id)

    monkeypatch.setattr(ov_file_module, "VikingClient", _FakeVikingClient)
    tool = VikingSearchTool()

    ws_a_first = await tool._get_client(
        SimpleNamespace(workspace_id="workspace-a", openviking_connection=None)
    )
    ws_a_second = await tool._get_client(
        SimpleNamespace(workspace_id="workspace-a", openviking_connection=None)
    )
    ws_b = await tool._get_client(
        SimpleNamespace(workspace_id="workspace-b", openviking_connection=None)
    )

    assert ws_a_first is ws_a_second
    assert ws_a_first is not ws_b
    assert created_workspace_ids == ["workspace-a", "workspace-b"]


@pytest.mark.asyncio
async def test_openviking_request_connection_client_is_closed_after_tool_call(monkeypatch):
    monkeypatch.setattr(ov_server_module, "load_config", lambda: _make_config("root"))
    tool = VikingSearchTool()

    result = await tool.execute(
        SimpleNamespace(
            workspace_id="workspace",
            memory_peer_ids=None,
            openviking_connection={
                "server_url": "http://studio.local",
                "api_key": "user-key",
                "account_id": "acct",
                "user_id": "alice",
                "agent_id": "web-playground",
                "role": "user",
            },
        ),
        query="hello",
    )

    assert result == "No results found for query: hello"
    assert len(_DummyHTTPClient.instances) == 1
    assert _DummyHTTPClient.instances[0].closed is True
