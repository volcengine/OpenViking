from pathlib import Path
from types import SimpleNamespace

import pytest
from vikingbot.agent.tools.ov_file import VikingGrepTool, VikingSearchTool
from vikingbot.config.schema import SessionKey
from vikingbot.hooks.base import HookContext
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

    async def commit_session(self, session_id, keep_recent_count=0, telemetry=False):
        return {
            "session_id": session_id,
            "status": "committed",
            "keep_recent_count": keep_recent_count,
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
        return []

    async def search(self, *_args, **_kwargs):
        return {"memories": [], "resources": [], "skills": []}

    async def grep(self, *_args, **_kwargs):
        return {"matches": []}

    async def close(self):
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
        root_api_key="root-key",
        account_id="acct",
        admin_user_id="admin",
        agent_id="",
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

    client = VikingClient(agent_id="workspace#channel")

    first = _DummyHTTPClient.instances[0]
    assert client.api_key_type == "root"
    assert first.kwargs["account"] == "acct"
    assert first.kwargs["user"] == "admin"
    assert first.kwargs["agent_id"] == "workspace"


def test_viking_client_init_user_mode_does_not_set_user_or_account(monkeypatch):
    monkeypatch.setattr(ov_server_module, "load_config", lambda: _make_config("user"))

    client = VikingClient(agent_id="workspace")

    first = _DummyHTTPClient.instances[0]
    assert client.api_key_type == "user"
    assert "user" not in first.kwargs
    assert "account" not in first.kwargs


@pytest.mark.asyncio
async def test_commit_user_mode_ignores_user_specific_key_flow(monkeypatch):
    monkeypatch.setattr(ov_server_module, "load_config", lambda: _make_config("user"))
    client = VikingClient(agent_id="workspace")

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
async def test_commit_root_mode_uses_sender_user_key(monkeypatch):
    monkeypatch.setattr(ov_server_module, "load_config", lambda: _make_config("root"))
    client = VikingClient(agent_id="workspace")

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
async def test_compact_hook_session_context_commits_admin_and_sender_sessions(monkeypatch):
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

        async def append_messages(
            self,
            session_id,
            messages,
            default_user_role_id=None,
            session_user_id=None,
        ):
            self.append_calls.append(
                (
                    session_id,
                    [message["content"] for message in messages],
                    default_user_role_id,
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
    assert result["users_count"] == 2
    assert fake_client.append_calls[-1] == (
        "cli__default__chat-1",
        ["admin answer", "u1 asks", "u1 reply", "u2 asks"],
        "admin",
        "admin",
    )
    assert (
        "cli__default__chat-1_u1",
        ["u1 asks", "u1 reply"],
        "u1",
        "u1",
    ) in fake_client.append_calls
    assert ("cli__default__chat-1_u2", ["u2 asks"], "u2", "u2") in fake_client.append_calls
    assert ("cli__default__chat-1", 2, "admin") in fake_client.commit_calls
    assert ("cli__default__chat-1_u1", 2, "u1") in fake_client.commit_calls
    assert ("cli__default__chat-1_u2", 2, "u2") in fake_client.commit_calls

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

        async def append_messages(
            self,
            session_id,
            messages,
            default_user_role_id=None,
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
    assert fake_client.append_calls == [
        ("cli__default__chat-1_u1", ["new reply"]),
        ("cli__default__chat-1", ["new reply"]),
    ]
    assert ("cli__default__chat-1", 2, "admin") in fake_client.commit_calls
    assert ("cli__default__chat-1_u1", 2, "u1") in fake_client.commit_calls
    assert session.metadata["openviking"]["last_synced_local_index"] == 1
    assert session.metadata["openviking"]["last_commit_local_index"] == 1


@pytest.mark.asyncio
async def test_compact_hook_force_commit_commits_sender_sessions_without_unsynced_messages(
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

        async def append_messages(
            self,
            session_id,
            messages,
            default_user_role_id=None,
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
    assert ("cli__default__chat-1", 2, "admin") in fake_client.commit_calls
    assert ("cli__default__chat-1_u1", 2, "u1") in fake_client.commit_calls
    assert ("cli__default__chat-1_u2", 2, "u2") in fake_client.commit_calls
    assert session.metadata["openviking"]["last_commit_performed"] is True


@pytest.mark.asyncio
async def test_compact_hook_session_context_sender_failure_does_not_advance_sync_cursor(
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

        async def append_messages(
            self,
            session_id,
            messages,
            default_user_role_id=None,
            session_user_id=None,
        ):
            self.append_calls.append((session_id, [message["content"] for message in messages]))
            if session_id.endswith("_u1"):
                raise RuntimeError("sender append failed")
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
    assert "sender append failed" in result["error"]
    assert fake_client.append_calls == [("cli__default__chat-1_u1", ["u1 asks", "u1 reply"])]
    assert fake_client.commit_calls == []
    state = session.metadata["openviking"]
    assert state["last_sync_status"] == "error"
    assert "sender append failed" in state["last_sync_error"]
    assert state["last_synced_local_index"] == -1
    assert state["last_commit_performed"] is False


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

        async def append_messages(
            self,
            session_id,
            messages,
            default_user_role_id=None,
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
    assert fake_client.append_calls == [
        ("cli__default__chat-1_u1", ["m3"]),
        ("cli__default__chat-1", ["m3"]),
    ]
    assert ("cli__default__chat-1", 2, "admin") in fake_client.commit_calls
    assert ("cli__default__chat-1_u1", 2, "u1") in fake_client.commit_calls
    assert session.metadata["openviking"]["last_commit_local_index"] == 2


@pytest.mark.asyncio
async def test_compact_hook_sender_commit_failure_does_not_commit_admin_and_retry_dedupes(
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
            self.fail_sender_commit = True

        async def append_messages(
            self,
            session_id,
            messages,
            default_user_role_id=None,
            session_user_id=None,
        ):
            self.append_calls.append((session_id, [message["content"] for message in messages]))
            return {"session_id": session_id, "added": len(messages)}

        async def get_session(self, session_id, user_id=None):
            return {"session_id": session_id, "pending_tokens": 120}

        async def commit_session(self, session_id, keep_recent_count=0, user_id=None):
            self.commit_calls.append((session_id, keep_recent_count, user_id))
            if session_id.endswith("_u1") and self.fail_sender_commit:
                raise RuntimeError("sender commit failed")
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
    assert "sender commit failed" in first["error"]
    assert fake_client.append_calls == [
        ("cli__default__chat-1_u1", ["u1 asks", "u1 reply"]),
        ("cli__default__chat-1", ["u1 asks", "u1 reply"]),
    ]
    assert fake_client.commit_calls == [("cli__default__chat-1_u1", 2, "u1")]
    state = session.metadata["openviking"]
    assert state["last_sync_status"] == "error"
    assert state["last_synced_local_index"] == 1
    assert state["last_sender_synced_local_indexes"] == {"u1": 1}
    assert state["last_commit_performed"] is False

    fake_client.fail_sender_commit = False
    fake_client.append_calls.clear()
    fake_client.commit_calls.clear()

    second = await hook.execute(context, session=session, force_commit=True)

    assert second["success"] is True
    assert fake_client.append_calls == []
    assert fake_client.commit_calls == [
        ("cli__default__chat-1_u1", 2, "u1"),
        ("cli__default__chat-1", 2, "admin"),
    ]
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

        async def append_messages(
            self,
            session_id,
            messages,
            default_user_role_id=None,
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
    assert fake_client.append_calls == [
        ("cli__default__chat-1_u1", ["m3"]),
        ("cli__default__chat-1", ["m3"]),
    ]
    assert fake_client.commit_calls == []


@pytest.mark.asyncio
async def test_viking_client_normalizes_system_tool_and_tool_result_messages(monkeypatch):
    monkeypatch.setattr(ov_server_module, "load_config", lambda: _make_config("root"))
    client = VikingClient(agent_id="workspace")

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
        default_user_role_id="admin",
    )

    assert [message["role"] for message in normalized] == [
        "assistant",
        "assistant",
        "assistant",
    ]
    assert [message["role_id"] for message in normalized] == [
        "workspace",
        "workspace",
        "workspace",
    ]
    assert normalized[0]["content"] == "system context"
    assert normalized[1]["content"] == "tool response"
    assert normalized[2]["content"] == "assistant answer"


@pytest.mark.asyncio
async def test_viking_client_append_messages_chunks_batches_at_server_limit(monkeypatch):
    monkeypatch.setattr(ov_server_module, "load_config", lambda: _make_config("root"))
    client = VikingClient(agent_id="workspace")

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
        default_user_role_id="admin",
    )

    assert [len(messages) for _, messages in calls] == [100, 1]
    assert result == {"session_id": "session-1", "added": 101, "message_count": 101}


@pytest.mark.asyncio
async def test_search_memory_uses_flat_namespaces(monkeypatch):
    monkeypatch.setattr(ov_server_module, "load_config", lambda: _make_config("root"))
    client = VikingClient(agent_id="workspace")

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

    await client.search_memory("hello", "sender-1", "admin", limit=5)

    assert calls == [
        "viking://user/sender-1/memories/",
        "viking://agent/workspace/memories/",
    ]


@pytest.mark.asyncio
async def test_search_memory_uses_policy_scoped_namespaces(monkeypatch):
    monkeypatch.setattr(ov_server_module, "load_config", lambda: _make_config("root"))
    client = VikingClient(agent_id="workspace")

    calls = []

    class _Result:
        memories = []

    async def _exists(_user_id):
        return True

    async def _find(*, query, target_uri, limit):
        calls.append(target_uri)
        return _Result()

    async def _accounts():
        return [
            {
                "account_id": "acct",
                "isolate_user_scope_by_agent": True,
                "isolate_agent_scope_by_user": True,
            }
        ]

    monkeypatch.setattr(client, "_check_user_exists", _exists)
    monkeypatch.setattr(client.client, "find", _find)
    monkeypatch.setattr(client.client, "admin_list_accounts", _accounts)

    await client.search_memory("hello", "sender-1", "admin", limit=5)

    assert calls == [
        "viking://user/sender-1/agent/workspace/memories/",
        "viking://agent/workspace/user/admin/memories/",
    ]


@pytest.mark.asyncio
async def test_skill_memory_uri_respects_namespace_policy(monkeypatch):
    monkeypatch.setattr(ov_server_module, "load_config", lambda: _make_config("root"))
    client = VikingClient(agent_id="workspace")

    async def _accounts():
        return [
            {
                "account_id": "acct",
                "isolate_user_scope_by_agent": False,
                "isolate_agent_scope_by_user": True,
            }
        ]

    monkeypatch.setattr(client.client, "admin_list_accounts", _accounts)
    await client._load_namespace_policy()

    assert (
        client._skill_memory_uri("planner", "admin")
        == "viking://agent/workspace/user/admin/memories/skills/planner.md"
    )


def test_openviking_grep_schema_requires_single_string_pattern():
    tool = VikingGrepTool()

    assert tool.parameters["properties"]["pattern"]["type"] == "string"


@pytest.mark.asyncio
async def test_openviking_grep_passes_admin_user_id(monkeypatch):
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

    assert calls == [("viking://resources/", "hello", True, "admin")]
    assert "Found 1 match for pattern 'hello':" in result
    assert "viking://resources/doc.md" in result


@pytest.mark.asyncio
async def test_openviking_search_uses_policy_scoped_user_namespace(monkeypatch):
    monkeypatch.setattr(ov_server_module, "load_config", lambda: _make_config("root"))
    tool = VikingSearchTool()
    client = VikingClient(agent_id="workspace")

    calls = []

    async def _accounts():
        return [
            {
                "account_id": "acct",
                "isolate_user_scope_by_agent": True,
                "isolate_agent_scope_by_user": False,
            }
        ]

    async def _search(query, target_uri=None, limit=20, user_id=None):
        calls.append(target_uri)
        return {"memories": [{"uri": target_uri, "abstract": "a", "score": 0.9, "is_leaf": True}]}

    async def _fake_get_client(_tool_context):
        return client

    monkeypatch.setattr(client.client, "admin_list_accounts", _accounts)
    monkeypatch.setattr(client, "search", _search)
    monkeypatch.setattr(tool, "_get_client", _fake_get_client)
    await client._load_namespace_policy()

    tool_context = SimpleNamespace(workspace_id="workspace", memory_user_ids=["sender-1"])
    result = await tool.execute(tool_context, query="hello")

    assert "sender-1/agent/workspace/memories" in result
    assert calls == ["viking://user/sender-1/agent/workspace/memories/"]


@pytest.mark.asyncio
async def test_openviking_search_user_key_mode_uses_current_user_namespace(monkeypatch):
    monkeypatch.setattr(ov_server_module, "load_config", lambda: _make_config("user"))
    tool = VikingSearchTool()
    client = VikingClient(agent_id="workspace")

    calls = []

    async def _search(query, target_uri=None, limit=20, user_id=None):
        calls.append(target_uri)
        return {"memories": [{"uri": target_uri, "abstract": "a", "score": 0.9, "is_leaf": True}]}

    async def _fake_get_client(_tool_context):
        return client

    monkeypatch.setattr(client, "search", _search)
    monkeypatch.setattr(tool, "_get_client", _fake_get_client)

    tool_context = SimpleNamespace(
        workspace_id="workspace", memory_user_ids=["sender-1", "sender-2"]
    )
    result = await tool.execute(tool_context, query="hello")

    assert "viking://user/memories/" in result
    assert calls == ["viking://user/memories/"]
