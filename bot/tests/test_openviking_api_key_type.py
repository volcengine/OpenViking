from pathlib import Path
from types import SimpleNamespace

import pytest

from vikingbot.agent.tools.ov_file import VikingSearchTool
from vikingbot.config.schema import SessionKey
from vikingbot.hooks.base import HookContext
from vikingbot.hooks.builtins.openviking_hooks import OpenVikingCompactHook
from vikingbot.openviking_mount import ov_server as ov_server_module
from vikingbot.openviking_mount.ov_server import VikingClient


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

    async def create_session(self):
        return {"session_id": "s-1"}

    def session(self, _session_id):
        return _DummySession()

    async def admin_list_accounts(self):
        return []

    async def close(self):
        return None


def _make_config(api_key_type: str, mode: str = "remote"):
    ov_server = SimpleNamespace(
        mode=mode,
        api_key_type=api_key_type,
        server_url="http://ov.local",
        root_api_key="root-key",
        account_id="acct",
        admin_user_id="admin",
        agent_id="",
    )
    return SimpleNamespace(ov_server=ov_server, ov_data_path=Path("/tmp/openviking-test"))


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

    assert result["success"] == "committed"


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

    assert result["success"] == "committed"
    assert any(inst.kwargs.get("api_key") == "user-key-1" for inst in _DummyHTTPClient.instances)


@pytest.mark.asyncio
async def test_compact_hook_user_mode_commits_once(monkeypatch):
    from vikingbot.hooks.builtins import openviking_hooks as hooks_module

    monkeypatch.setattr(hooks_module, "load_config", lambda: _make_config("user"))

    class _FakeClient:
        def __init__(self):
            self.calls = []

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
async def test_search_memory_uses_flat_agent_namespace_by_default(monkeypatch):
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
        "viking://agent/workspace/user/sender-1/memories/",
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

    async def _search(query, target_uri=None, limit=20):
        calls.append(target_uri)
        return {"memories": [{"uri": target_uri, "abstract": "a", "score": 0.9, "is_leaf": True}]}

    monkeypatch.setattr(client.client, "admin_list_accounts", _accounts)
    monkeypatch.setattr(client.client, "search", _search)
    monkeypatch.setattr(tool, "_get_client", lambda _tool_context: client)

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

    async def _search(query, target_uri=None, limit=20):
        calls.append(target_uri)
        return {"memories": [{"uri": target_uri, "abstract": "a", "score": 0.9, "is_leaf": True}]}

    monkeypatch.setattr(client.client, "search", _search)
    monkeypatch.setattr(tool, "_get_client", lambda _tool_context: client)

    tool_context = SimpleNamespace(workspace_id="workspace", memory_user_ids=["sender-1", "sender-2"])
    result = await tool.execute(tool_context, query="hello")

    assert "viking://user/memories/" in result
    assert calls == ["viking://user/memories/"]
