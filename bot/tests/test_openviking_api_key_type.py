import json
from pathlib import Path
from types import SimpleNamespace

import pytest
from vikingbot.agent import memory as memory_module
from vikingbot.agent.context import ContextBuilder
from vikingbot.agent.loop import _is_tool_result_success
from vikingbot.agent.memory import MemoryStore
from vikingbot.agent.tools import ov_file as ov_file_module
from vikingbot.agent.tools.base import ToolContext
from vikingbot.agent.tools.ov_file import (
    VikingGlobTool,
    VikingGrepTool,
    VikingListTool,
    VikingMemoryCommitTool,
    VikingSearchTool,
)
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
        self.read_calls = []
        self.closed = False
        _DummyHTTPClient.instances.append(self)

    async def initialize(self):
        return None

    async def create_session(self, session_id=None, memory_policy=None):
        return {"session_id": session_id or "s-1", "memory_policy": memory_policy}

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

    async def find(self, *_args, **_kwargs):
        self.find_calls.append((_args, _kwargs))
        return []

    async def ls(self, path, recursive=False):
        self.ls_calls.append((path, recursive))
        return []

    async def search(self, *_args, **_kwargs):
        return {"memories": [], "resources": [], "skills": []}

    async def abstract(self, uri):
        self.read_calls.append(("abstract", uri))
        return ""

    async def overview(self, uri):
        self.read_calls.append(("overview", uri))
        return ""

    async def read(self, uri):
        self.read_calls.append(("read", uri))
        return ""

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
        auth_mode="",
        api_key_type=api_key_type,
        server_url="http://ov.local",
        api_key="root-key" if api_key_type == "root" else "user-key",
        root_api_key="legacy-root-key",
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
    assert first.kwargs["api_key"] == "root-key"
    assert first.kwargs["account"] == "acct"
    assert first.kwargs["user"] == "admin"
    assert first.kwargs["profile_enabled"] is False
    assert "agent_id" not in first.kwargs


def test_viking_client_uses_injected_config_without_reloading(monkeypatch):
    config = _make_config("root")

    def _unexpected_load():
        raise AssertionError("VikingClient must not reload Bot config")

    monkeypatch.setattr(ov_server_module, "load_config", _unexpected_load)

    client = VikingClient(config=config)

    assert client.config is config
    assert client.openviking_config is config.ov_server
    assert _DummyHTTPClient.instances[0].kwargs["api_key"] == "root-key"


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


def test_viking_client_actor_peer_id_sets_actor_header(monkeypatch):
    monkeypatch.setattr(ov_server_module, "load_config", lambda: _make_config("user"))

    client = VikingClient(actor_peer_id="sender with space")

    first = _DummyHTTPClient.instances[0]
    assert client.actor_peer_id == VikingClient._peer_id("sender with space")
    assert first.kwargs["actor_peer_id"] == client.actor_peer_id


def test_viking_client_uses_effective_auth_mode_for_dev(monkeypatch):
    config = _make_config("user", mode="remote")
    config.ov_server.effective_auth_mode = "dev"
    monkeypatch.setattr(ov_server_module, "load_config", lambda: config)

    client = VikingClient()

    first = _DummyHTTPClient.instances[0]
    assert client.auth_mode == "dev"
    assert client.mode == "local"
    assert first.kwargs == {"url": "http://ov.local"}


def test_openviking_config_api_key_type_empty_values_are_inferred():
    assert OpenVikingConfig(api_key_type=None, api_key="user-key").api_key_type == "user"
    assert OpenVikingConfig(api_key_type="", api_key="user-key").api_key_type == "user"
    config = OpenVikingConfig(api_key_type="root", api_key="root-key")
    assert config.api_key_type == "root"
    assert config.api_key == "root-key"


def test_user_key_current_memory_targets_use_current_user_shorthand(monkeypatch):
    monkeypatch.setattr(ov_server_module, "load_config", lambda: _make_config("user"))

    client = VikingClient()

    assert client.build_current_memory_target_uris(peer_ids=["sender-1"]) == [
        "viking://user/memories/",
        "viking://user/peers/sender-1/memories/",
    ]


@pytest.mark.asyncio
async def test_viking_client_search_preserves_serialized_group_results(monkeypatch):
    monkeypatch.setattr(ov_server_module, "load_config", lambda: _make_config("user"))
    client = VikingClient()

    async def _search(query, target_uri=None, limit=10):
        return {
            "memories": [
                {
                    "context_type": "memory",
                    "uri": "viking://user/default/memories/profile.md",
                    "abstract": "profile hit",
                    "score": 0.3,
                }
            ],
            "resources": [
                {
                    "context_type": "resource",
                    "uri": "viking://resources/解释信-杜涛/解释信-杜涛.md",
                    "abstract": "resource hit",
                    "score": 0.4,
                }
            ],
            "skills": [
                {
                    "context_type": "skill",
                    "uri": "viking://user/default/skills/planner.md",
                    "abstract": "skill hit",
                    "score": 0.2,
                }
            ],
            "total": 3,
        }

    monkeypatch.setattr(client.client, "search", _search)

    result = await client.search("解释信", target_uri="", limit=10)

    assert result["total"] == 3
    assert result["query"] == "解释信"
    assert result["target_uri"] == ""
    assert [item["uri"] for item in result["memories"]] == [
        "viking://user/default/memories/profile.md"
    ]
    assert [item["uri"] for item in result["resources"]] == [
        "viking://resources/解释信-杜涛/解释信-杜涛.md"
    ]
    assert [item["uri"] for item in result["skills"]] == ["viking://user/default/skills/planner.md"]


def test_ov_server_api_key_mode_does_not_read_ovcli_user_key():
    bot_data = {"root_api_key": "bot-root-key"}
    ov_data = {"root_api_key": "server-root-key"}

    auth_mode, source, api_key_source = config_loader_module._merge_ov_server_config(
        bot_data, ov_data, server_section_present=True
    )

    assert auth_mode == "api_key"
    assert source == "inherited"
    assert api_key_source == "none"
    assert bot_data["mode"] == "remote"
    assert "api_key" not in bot_data
    assert bot_data["api_key_type"] == "user"


def test_ov_server_trusted_mode_fills_api_key_from_top_level_root_key():
    bot_data = {}
    ov_data = {"auth_mode": "trusted", "root_api_key": "server-root-key"}

    config_loader_module._merge_ov_server_config(bot_data, ov_data, server_section_present=True)

    assert bot_data["mode"] == "remote"
    assert bot_data["api_key"] == "server-root-key"
    assert "root_api_key" not in bot_data
    assert bot_data["api_key_type"] == "root"


def test_ov_server_current_trusted_prefers_top_level_root_key():
    bot_data = {"api_key": "stale-bot-key"}
    ov_data = {"auth_mode": "trusted", "root_api_key": "server-root-key"}

    config_loader_module._merge_ov_server_config(bot_data, ov_data, server_section_present=True)

    assert bot_data["mode"] == "remote"
    assert bot_data["api_key"] == "server-root-key"
    assert bot_data["api_key_type"] == "root"


def test_ov_server_external_url_does_not_inherit_trusted_root_key():
    bot_data = {"server_url": "https://external.example"}
    ov_data = {"auth_mode": "trusted", "root_api_key": "server-root-key"}

    config_loader_module._merge_ov_server_config(bot_data, ov_data, server_section_present=True)

    assert bot_data["mode"] == "remote"
    assert "api_key" not in bot_data
    assert bot_data["api_key_type"] == "user"
    assert "root_api_key" not in bot_data


def test_ov_server_explicit_url_is_external_even_if_it_matches_local_url():
    bot_data = {"server_url": "http://localhost:1933"}
    ov_data = {"auth_mode": "trusted", "root_api_key": "server-root-key"}

    config_loader_module._merge_ov_server_config(bot_data, ov_data, server_section_present=True)

    assert bot_data["mode"] == "remote"
    assert "api_key" not in bot_data
    assert bot_data["api_key_type"] == "user"
    assert "root_api_key" not in bot_data


def test_ov_server_external_url_forces_remote_mode():
    bot_data = {
        "server_url": "https://external.example",
        "mode": "local",
        "api_key": "external-user-key",
    }
    ov_data = {"auth_mode": "trusted", "root_api_key": "server-root-key"}

    config_loader_module._merge_ov_server_config(bot_data, ov_data, server_section_present=True)

    assert bot_data["mode"] == "remote"
    assert bot_data["api_key"] == "external-user-key"
    assert bot_data["api_key_type"] == "user"
    assert "root_api_key" not in bot_data


def test_ov_server_external_url_root_key_does_not_imply_root_mode():
    bot_data = {
        "server_url": "https://external.example",
        "root_api_key": "bot-root-key",
    }
    ov_data = {"auth_mode": "trusted", "root_api_key": "server-root-key"}

    config_loader_module._merge_ov_server_config(bot_data, ov_data, server_section_present=True)

    assert bot_data["mode"] == "remote"
    assert "api_key" not in bot_data
    assert bot_data["api_key_type"] == "user"
    assert bot_data["root_api_key"] == "bot-root-key"


def test_ov_server_legacy_mode_is_ignored_for_current_api_key_server():
    bot_data = {"mode": "local"}
    ov_data = {"root_api_key": "server-root-key"}

    config_loader_module._merge_ov_server_config(bot_data, ov_data, server_section_present=True)

    assert bot_data["mode"] == "remote"
    assert "api_key" not in bot_data
    assert bot_data["api_key_type"] == "user"


def test_ov_server_current_dev_mode_ignores_legacy_api_key_for_mode():
    bot_data = {"api_key": "bot-user-key"}

    config_loader_module._merge_ov_server_config(bot_data, {}, server_section_present=True)

    assert bot_data["mode"] == "local"
    assert bot_data["api_key"] == "bot-user-key"


def test_ov_server_without_root_server_section_stays_standalone():
    bot_data = {}

    auth_mode, source, api_key_source = config_loader_module._merge_ov_server_config(
        bot_data,
        {},
        server_section_present=False,
    )

    assert auth_mode == ""
    assert source == "none"
    assert api_key_source == "none"
    assert "source" not in bot_data
    assert bot_data["server_url"] == ""
    assert bot_data["mode"] == "local"
    assert bot_data["api_key_type"] == "user"


def test_server_managed_load_config_ignores_bot_ov_server(monkeypatch, tmp_path):
    config_path = tmp_path / "ov.conf"
    config_path.write_text(
        json.dumps(
            {
                "server": {
                    "host": "127.0.0.1",
                    "port": 1935,
                    "auth_mode": "api_key",
                    "root_api_key": "root-key",
                },
                "bot": {
                    "ov_server": {
                        "server_url": "https://remote.example",
                        "api_key": "bot-key",
                    }
                },
            }
        )
    )
    monkeypatch.setattr(config_loader_module, "CONFIG_PATH", config_path)
    monkeypatch.setenv(config_loader_module.VIKINGBOT_WITH_OPENVIKING_SERVER_ENV, "1")
    config = config_loader_module.load_config()

    assert config.ov_server.server_url == "http://127.0.0.1:1935"
    assert config.ov_server.api_key == ""
    assert config.ov_server.get_config_source() == "inherited"
    assert config.ov_server.get_api_key_source() == "none"
    assert config.ov_server.is_server_managed() is True


def test_server_managed_load_config_uses_runtime_server_url(monkeypatch, tmp_path):
    config_path = tmp_path / "ov.conf"
    config_path.write_text(
        json.dumps(
            {
                "server": {
                    "host": "127.0.0.1",
                    "port": 1935,
                    "auth_mode": "api_key",
                    "root_api_key": "root-key",
                }
            }
        )
    )
    monkeypatch.setattr(config_loader_module, "CONFIG_PATH", config_path)
    monkeypatch.setenv(config_loader_module.VIKINGBOT_WITH_OPENVIKING_SERVER_ENV, "1")
    monkeypatch.setenv(
        config_loader_module.VIKINGBOT_MANAGED_OV_SERVER_URL_ENV,
        "http://127.0.0.1:1940",
    )

    config = config_loader_module.load_config()

    assert config.ov_server.server_url == "http://127.0.0.1:1940"
    assert config.ov_server.is_server_managed() is True


def test_vikingbot_load_config_does_not_read_ovcli_conf(monkeypatch, tmp_path):
    config_path = tmp_path / "ov.conf"
    config_path.write_text(
        json.dumps(
            {
                "server": {
                    "host": "127.0.0.1",
                    "port": 1935,
                    "auth_mode": "api_key",
                    "root_api_key": "root-key",
                },
                "bot": {"ov_server": {"api_key": "bot-user-key"}},
            }
        )
    )
    invalid_cli_path = tmp_path / "ovcli.conf"
    invalid_cli_path.write_text(json.dumps({"account_id": "legacy-account"}))
    monkeypatch.setattr(config_loader_module, "CONFIG_PATH", config_path)
    monkeypatch.setenv("OPENVIKING_CLI_CONFIG_FILE", str(invalid_cli_path))

    config = config_loader_module.load_config()

    assert not hasattr(config_loader_module, "load_ovcli_config")
    assert config.ov_server.api_key == "bot-user-key"
    assert config.ov_server.get_api_key_source() == "bot.ov_server.api_key"


@pytest.mark.asyncio
async def test_memory_store_passes_injected_config_to_request_client(monkeypatch, tmp_path):
    config = _make_config("root")
    captured = {}

    def _unexpected_load():
        raise AssertionError("MemoryStore must not reload Bot config")

    class _FakeClient:
        async def read_peer_profile(self, peer_id):
            return f"profile:{peer_id}"

        async def close(self):
            return None

    async def _create(**kwargs):
        captured.update(kwargs)
        return _FakeClient()

    monkeypatch.setattr(memory_module, "load_config", _unexpected_load)
    monkeypatch.setattr(memory_module, "VikingClient", SimpleNamespace(create=_create))

    store = MemoryStore(tmp_path, config=config)
    result = await store.get_viking_peer_profile(
        workspace_id="workspace",
        peer_id="peer-a",
        openviking_connection={"api_key": "request-key"},
    )

    assert result == "profile:peer-a"
    assert captured["config"] is config
    assert captured["connection"] == {"api_key": "request-key"}


def _auth_probe(*, ok=True, status_code=200, data=None, error=""):
    return config_loader_module._OpenVikingHTTPResult(
        ok=ok,
        status_code=status_code,
        data=data,
        error=error,
    )


def test_validate_openviking_auth_warns_when_server_unavailable(monkeypatch, capsys):
    config = SimpleNamespace(
        ov_server=SimpleNamespace(
            _source="inherited",
            mode="remote",
            api_key_type="user",
            api_key="user-key",
            root_api_key="",
            server_url="http://ov.local",
        )
    )
    monkeypatch.setattr(
        config_loader_module,
        "_request_openviking_json",
        lambda *_args, **_kwargs: _auth_probe(ok=False, error="ConnectError"),
    )

    config_loader_module.validate_openviking_auth(config)

    captured = capsys.readouterr()
    assert "OpenViking server at http://ov.local is unavailable" in captured.err
    assert "standalone mode" in captured.err
    assert "user-key" not in captured.err
    assert config.ov_server.server_url == ""
    assert config.ov_server.mode == "local"


def test_validate_openviking_auth_exits_when_explicit_server_unavailable(monkeypatch, capsys):
    config = SimpleNamespace(
        ov_server=SimpleNamespace(
            _source="explicit",
            mode="remote",
            api_key_type="user",
            api_key="user-key",
            root_api_key="",
            server_url="http://ov.remote",
        )
    )
    monkeypatch.setattr(
        config_loader_module,
        "_request_openviking_json",
        lambda *_args, **_kwargs: _auth_probe(ok=False, error="ConnectError"),
    )

    with pytest.raises(SystemExit):
        config_loader_module.validate_openviking_auth(config)

    captured = capsys.readouterr()
    assert "configured bot.ov_server.server_url is unavailable" in captured.err
    assert "OpenViking server URL: http://ov.remote" in captured.err
    assert "user-key" not in captured.err


def test_validate_openviking_auth_keeps_managed_upstream_while_server_starts(monkeypatch, capsys):
    config = SimpleNamespace(
        ov_server=SimpleNamespace(
            _source="inherited",
            _server_managed=True,
            mode="remote",
            api_key_type="user",
            api_key="",
            root_api_key="",
            server_url="http://127.0.0.1:1935",
        )
    )
    monkeypatch.setattr(
        config_loader_module,
        "_request_openviking_json",
        lambda *_args, **_kwargs: _auth_probe(ok=False, status_code=None, error="ConnectError"),
    )

    config_loader_module.validate_openviking_auth(config)

    assert config.ov_server.server_url == "http://127.0.0.1:1935"
    assert "keep the inherited upstream" in capsys.readouterr().err


def test_validate_openviking_auth_does_not_downgrade_inherited_auth_failure(monkeypatch):
    config = SimpleNamespace(
        ov_server=SimpleNamespace(
            _source="inherited",
            mode="remote",
            api_key_type="user",
            api_key="bad-user-key",
            root_api_key="",
            server_url="http://ov.local",
        )
    )
    monkeypatch.setattr(
        config_loader_module,
        "_request_openviking_json",
        lambda *_args, **_kwargs: _auth_probe(ok=False, status_code=401),
    )

    with pytest.raises(SystemExit):
        config_loader_module.validate_openviking_auth(config)

    assert config.ov_server.server_url == "http://ov.local"


def test_validate_openviking_auth_exits_for_auth_mode_mismatch(monkeypatch, capsys):
    config = SimpleNamespace(
        ov_server=SimpleNamespace(
            _source="inherited",
            mode="remote",
            api_key_type="user",
            api_key="user-key",
            root_api_key="",
            server_url="http://ov.local",
        )
    )
    monkeypatch.setattr(
        config_loader_module,
        "_request_openviking_json",
        lambda *_args, **_kwargs: _auth_probe(data={"auth_mode": "trusted"}),
    )

    with pytest.raises(SystemExit):
        config_loader_module.validate_openviking_auth(config)

    captured = capsys.readouterr()
    assert "does not match the current ov.conf" in captured.err
    assert "OpenViking server URL: http://ov.local" in captured.err
    assert "Running server auth_mode: trusted" in captured.err
    assert "Current ov.conf server auth_mode: api_key" in captured.err
    assert "restart OpenViking server with the current ov.conf" in captured.err
    assert "server.auth_mode='trusted'" in captured.err
    assert "bot.ov_server.api_key_type" not in captured.err
    assert "user-key" not in captured.err


def test_inherited_dev_config_mismatch_explains_stale_api_key_server(monkeypatch, capsys):
    config = SimpleNamespace(
        ov_server=SimpleNamespace(
            _source="inherited",
            effective_auth_mode="dev",
            mode="local",
            api_key_type="user",
            api_key="",
            root_api_key="",
            server_url="http://127.0.0.1:1935",
        )
    )
    monkeypatch.setattr(
        config_loader_module,
        "_request_openviking_json",
        lambda *_args, **_kwargs: _auth_probe(data={"auth_mode": "api_key"}),
    )

    with pytest.raises(SystemExit):
        config_loader_module.validate_openviking_auth(config)

    captured = capsys.readouterr()
    assert "Running server auth_mode: api_key" in captured.err
    assert "Current ov.conf server auth_mode: dev" in captured.err
    assert "started with different authentication settings" in captured.err
    assert "configure bot.ov_server.api_key with an OpenViking User/Admin API key" in captured.err
    assert "dev-mode VikingBot gateway must listen on localhost" in captured.err
    assert "bot.ov_server.api_key_type" not in captured.err


def test_validate_openviking_auth_exits_for_api_key_mode_without_user_key(monkeypatch, capsys):
    config = SimpleNamespace(
        ov_server=SimpleNamespace(
            mode="remote",
            api_key_type="user",
            api_key="",
            root_api_key="root-key",
            server_url="http://ov.local",
        )
    )
    monkeypatch.setattr(
        config_loader_module,
        "_request_openviking_json",
        lambda *_args, **_kwargs: _auth_probe(data={"auth_mode": "api_key"}),
    )

    with pytest.raises(SystemExit):
        config_loader_module.validate_openviking_auth(config)

    captured = capsys.readouterr()
    assert "Error:" in captured.err
    assert "OpenViking User/Admin API key" in captured.err
    assert "bot.ov_server.api_key" in captured.err
    assert "Root API keys cannot access" in captured.err


def test_validate_openviking_auth_exits_when_user_key_is_root(monkeypatch, capsys):
    config = SimpleNamespace(
        ov_server=SimpleNamespace(
            mode="remote",
            api_key_type="user",
            api_key="configured-key",
            root_api_key="",
            server_url="http://ov.local",
        )
    )
    calls = []

    def _fake_probe(_server_url, path, *, headers=None):
        calls.append((path, headers))
        if headers:
            return _auth_probe(
                data={
                    "auth_mode": "api_key",
                    "role": "root",
                    "account_id": "default",
                    "user_id": "default",
                }
            )
        return _auth_probe(data={"auth_mode": "api_key"})

    monkeypatch.setattr(config_loader_module, "_request_openviking_json", _fake_probe)

    with pytest.raises(SystemExit):
        config_loader_module.validate_openviking_auth(config)

    captured = capsys.readouterr()
    assert "resolves to a ROOT API key" in captured.err
    assert "configured-key" not in captured.err
    assert calls[1][1] == {"X-API-Key": "configured-key"}


def test_validate_openviking_auth_allows_user_key(monkeypatch, capsys):
    config = SimpleNamespace(
        ov_server=SimpleNamespace(
            mode="remote",
            api_key_type="user",
            api_key="configured-key",
            root_api_key="",
            server_url="http://ov.local",
        )
    )

    def _fake_probe(_server_url, _path, *, headers=None):
        if headers:
            return _auth_probe(
                data={
                    "auth_mode": "api_key",
                    "role": "admin",
                    "account_id": "acct",
                    "user_id": "alice",
                }
            )
        return _auth_probe(data={"auth_mode": "api_key"})

    monkeypatch.setattr(config_loader_module, "_request_openviking_json", _fake_probe)

    config_loader_module.validate_openviking_auth(config)

    captured = capsys.readouterr()
    assert captured.err == ""


def test_validate_openviking_auth_uses_effective_auth_mode_not_legacy_mode(monkeypatch, capsys):
    config = SimpleNamespace(
        ov_server=SimpleNamespace(
            effective_auth_mode="dev",
            mode="remote",
            api_key_type="user",
            api_key="configured-key",
            root_api_key="",
            server_url="http://127.0.0.1:1935",
        )
    )
    monkeypatch.setattr(
        config_loader_module,
        "_request_openviking_json",
        lambda *_args, **_kwargs: _auth_probe(data={"auth_mode": "dev"}),
    )

    config_loader_module.validate_openviking_auth(config)

    captured = capsys.readouterr()
    assert captured.err == ""


def test_validate_openviking_auth_exits_for_trusted_bad_root_key(monkeypatch, capsys):
    config = SimpleNamespace(
        ov_server=SimpleNamespace(
            mode="remote",
            api_key="configured-root",
            api_key_type="root",
            root_api_key="legacy-root",
            account_id="acct",
            admin_user_id="admin",
            server_url="http://ov.local",
        )
    )

    def _fake_probe(_server_url, path, *, headers=None):
        if path == "/api/v1/system/status":
            assert headers == {
                "X-OpenViking-Account": "acct",
                "X-OpenViking-User": "admin",
                "X-API-Key": "configured-root",
            }
            return _auth_probe(ok=False, status_code=401)
        return _auth_probe(data={"auth_mode": "trusted"})

    monkeypatch.setattr(config_loader_module, "_request_openviking_json", _fake_probe)

    with pytest.raises(SystemExit):
        config_loader_module.validate_openviking_auth(config)

    captured = capsys.readouterr()
    assert "no valid root API key is available" in captured.err
    assert "configured-root" not in captured.err


def test_validate_openviking_auth_allows_trusted_root(monkeypatch, capsys):
    config = SimpleNamespace(
        ov_server=SimpleNamespace(
            mode="remote",
            api_key="root-key",
            api_key_type="root",
            root_api_key="legacy-root",
            account_id="acct",
            admin_user_id="admin",
            server_url="http://ov.local",
        )
    )

    def _fake_probe(_server_url, path, *, headers=None):
        if path == "/api/v1/system/status":
            assert headers == {
                "X-OpenViking-Account": "acct",
                "X-OpenViking-User": "admin",
                "X-API-Key": "root-key",
            }
            return _auth_probe(data={"status": "ok", "result": {"user": "admin"}})
        return _auth_probe(data={"auth_mode": "trusted"})

    monkeypatch.setattr(config_loader_module, "_request_openviking_json", _fake_probe)

    config_loader_module.validate_openviking_auth(config)

    captured = capsys.readouterr()
    assert captured.err == ""


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
            "api_key_type": "user",
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
    assert client._namespace_policy_loaded is True
    assert client.should_sender_fanout() is False
    assert client._memory_target_uri(None) == "viking://user/memories/"
    assert first.kwargs == {
        "url": "http://ov.local",
        "api_key": "anonymous-key",
        "profile_enabled": False,
        "account": "acct",
        "user": "anonymous",
    }


def test_viking_client_request_connection_preserves_trusted_scope(monkeypatch):
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
            "api_key_type": "root",
        },
    )

    first = _DummyHTTPClient.instances[0]
    assert first.kwargs == {
        "url": "http://ov.local",
        "api_key": "admin-key",
        "profile_enabled": False,
        "account": "acct",
        "user": "default",
    }


def test_viking_client_request_connection_allows_trusted_no_key(monkeypatch):
    monkeypatch.setattr(ov_server_module, "load_config", lambda: _make_config("root"))

    VikingClient(
        agent_id="workspace#channel",
        connection={
            "server_url": "http://studio.local",
            "account_id": "acct",
            "user_id": "alice",
            "agent_id": "web-playground",
            "role": "user",
            "api_key_type": "root",
        },
    )

    first = _DummyHTTPClient.instances[0]
    assert first.kwargs == {
        "url": "http://ov.local",
        "profile_enabled": False,
        "account": "acct",
        "user": "alice",
    }


def test_viking_client_request_connection_does_not_fill_identity_from_bot_config(monkeypatch):
    monkeypatch.setattr(ov_server_module, "load_config", lambda: _make_config("user"))

    client = VikingClient(
        connection={
            "server_url": "http://untrusted.local",
            "api_key": "request-key",
            "api_key_type": "user",
        },
    )

    first = _DummyHTTPClient.instances[0]
    assert client.account_id is None
    assert client.admin_user_id is None
    assert first.kwargs == {
        "url": "http://ov.local",
        "api_key": "request-key",
        "profile_enabled": False,
    }


@pytest.mark.asyncio
async def test_commit_user_mode_uses_user_key_client_only(monkeypatch):
    monkeypatch.setattr(ov_server_module, "load_config", lambda: _make_config("user"))
    client = VikingClient()

    result = await client.commit(
        session_id="sess",
        messages=[{"role": "user", "content": "hello", "tools_used": []}],
        user_id="sender-1",
    )

    assert result["success"] is True


@pytest.mark.asyncio
async def test_commit_request_connection_uses_request_client_only(monkeypatch):
    monkeypatch.setattr(ov_server_module, "load_config", lambda: _make_config("root"))
    client = VikingClient(
        agent_id="workspace",
        connection={
            "server_url": "http://studio.local",
            "api_key": "anonymous-key",
            "account_id": "acct",
            "user_id": "anonymous",
            "agent_id": "web-playground",
            "api_key_type": "user",
        },
    )

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
            "api_key_type": "user",
            "namespace_policy": {
                "isolate_user_scope_by_agent": False,
                "isolate_agent_scope_by_user": False,
            },
        },
    )

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
async def test_commit_trusted_root_mode_uses_sender_identity_header(monkeypatch):
    monkeypatch.setattr(ov_server_module, "load_config", lambda: _make_config("root"))
    client = VikingClient()

    result = await client.commit(
        session_id="sess",
        messages=[{"role": "user", "content": "hello", "tools_used": []}],
        user_id="sender-2",
    )

    assert result["success"] is True
    assert any(
        inst.kwargs.get("api_key") == "root-key"
        and inst.kwargs.get("account") == "acct"
        and inst.kwargs.get("user") == "sender-2"
        for inst in _DummyHTTPClient.instances
    )


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
        "assistant",
    ]
    assert all("peer_id" not in message for message in normalized)
    assert normalized[0]["parts"] == [{"type": "text", "text": "system context"}]
    assert normalized[1]["parts"] == [{"type": "text", "text": "tool response"}]
    assert "content" not in normalized[2]
    assert normalized[2]["parts"][0]["type"] == "tool"
    assert normalized[2]["parts"][0]["tool_name"] == "read_file"
    assert "content" not in normalized[3]
    assert normalized[3]["parts"] == [{"type": "text", "text": "assistant answer"}]


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
async def test_viking_client_ensure_session_creates_after_legacy_not_found(monkeypatch):
    monkeypatch.setattr(ov_server_module, "load_config", lambda: _make_config("root"))
    client = VikingClient(workspace_id="workspace")

    class NotFoundError(Exception):
        code = "NOT_FOUND"

    created = []

    async def _get_session(_session_id):
        raise NotFoundError("Resource not found")

    async def _create_session(session_id=None, memory_policy=None):
        created.append((session_id, memory_policy))
        return {"session_id": session_id, "memory_policy": memory_policy}

    monkeypatch.setattr(client.client, "get_session", _get_session)
    monkeypatch.setattr(client.client, "create_session", _create_session)

    result = await client.ensure_session(
        "session-1",
        memory_policy={"strategy": "compact"},
    )

    assert result == {"session_id": "session-1", "memory_policy": {"strategy": "compact"}}
    assert created == [("session-1", {"strategy": "compact"})]


@pytest.mark.asyncio
async def test_search_memory_uses_user_namespace(monkeypatch):
    monkeypatch.setattr(ov_server_module, "load_config", lambda: _make_config("root"))
    client = VikingClient()

    await client.search_memory("hello", "sender-1", limit=5)

    scoped = _DummyHTTPClient.instances[1]
    assert scoped.kwargs["api_key"] == "root-key"
    assert scoped.kwargs["account"] == "acct"
    assert scoped.kwargs["user"] == "sender-1"
    assert scoped.find_calls[0][1]["target_uri"] == "viking://user/sender-1/memories/"
    assert scoped.closed is True


@pytest.mark.asyncio
async def test_search_memory_uses_user_namespace_without_agent_scope(monkeypatch):
    monkeypatch.setattr(ov_server_module, "load_config", lambda: _make_config("root"))
    client = VikingClient()

    await client.search_memory("hello", "sender-1", limit=5)

    scoped = _DummyHTTPClient.instances[1]
    assert scoped.kwargs["user"] == "sender-1"
    assert scoped.find_calls[0][1]["target_uri"] == "viking://user/sender-1/memories/"
    assert scoped.closed is True


@pytest.mark.asyncio
async def test_read_content_trusted_owner_uri_uses_owner_identity(monkeypatch):
    monkeypatch.setattr(ov_server_module, "load_config", lambda: _make_config("root"))
    client = VikingClient()

    await client.read_content("viking://user/sender-1/memories/profile.md", level="read")

    scoped = _DummyHTTPClient.instances[1]
    assert scoped.kwargs["api_key"] == "root-key"
    assert scoped.kwargs["account"] == "acct"
    assert scoped.kwargs["user"] == "sender-1"
    assert scoped.read_calls == [("read", "viking://user/sender-1/memories/profile.md")]
    assert scoped.closed is True


@pytest.mark.asyncio
async def test_search_memory_peer_ids_use_explicit_peer_uris(monkeypatch):
    monkeypatch.setattr(ov_server_module, "load_config", lambda: _make_config("user"))
    client = VikingClient()

    calls = []

    class _Result:
        memories = []

    async def _find(*, query, target_uri, limit):
        calls.append((query, target_uri, limit))
        return _Result()

    monkeypatch.setattr(client.client, "find", _find)

    await client.search_memory("hello", peer_ids=["sender-1", "sender-2"], limit=5)

    assert calls == [
        ("hello", "viking://user/peers/sender-1/memories/", 5),
        ("hello", "viking://user/peers/sender-2/memories/", 5),
    ]


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
async def test_viking_memory_context_keeps_legacy_users_separate_from_peers(monkeypatch, tmp_path):
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
            "limit": 35,
        }
    ]


@pytest.mark.asyncio
async def test_viking_memory_context_creates_actor_scoped_client(monkeypatch, tmp_path):
    create_calls = []

    class _FakeClient:
        def build_current_memory_target_uris(self, *, peer_ids=None, include_self=True):
            return []

        async def close(self):
            return None

    async def _fake_create(**kwargs):
        create_calls.append(kwargs)
        return _FakeClient()

    monkeypatch.setattr("vikingbot.agent.memory.load_config", lambda: _make_config("user"))
    monkeypatch.setattr("vikingbot.agent.memory.VikingClient.create", _fake_create)

    store = MemoryStore(tmp_path)

    await store.get_viking_memory_context(
        current_message="hello",
        workspace_id="workspace",
        sender_id="sender-1",
    )

    assert create_calls[0]["actor_peer_id"] == "sender-1"


@pytest.mark.asyncio
async def test_viking_memory_type_quota_actor_scope_keeps_per_type_limits(tmp_path):
    calls = []

    class _ActorClient:
        actor_peer_id = "sender-1"

        def _current_peer_memory_target_uri(self, peer_id):
            return f"viking://user/peers/{peer_id}/memories/"

        async def find(self, *, query, target_uri, context_type=None, limit):
            calls.append(
                {
                    "query": query,
                    "target_uri": target_uri,
                    "context_type": context_type,
                    "limit": limit,
                }
            )
            if target_uri.endswith("/events/"):
                return {
                    "memories": [
                        {
                            "uri": "viking://user/peers/sender-1/memories/events/e1.md",
                            "abstract": "event",
                            "score": 0.9,
                        },
                    ]
                }
            return {"memories": []}

    store = MemoryStore(tmp_path)

    result = await store._search_viking_memory_by_type_quota(
        client=_ActorClient(),
        query="hello",
        peer_ids=["sender-1", "other"],
        quotas={"events": 1, "entities": 1, "preferences": 1},
    )

    assert calls == [
        {
            "query": "hello",
            "target_uri": "viking://user/peers/sender-1/memories/events/",
            "context_type": "memory",
            "limit": 1,
        },
        {
            "query": "hello",
            "target_uri": "viking://user/peers/sender-1/memories/entities/",
            "context_type": "memory",
            "limit": 1,
        },
        {
            "query": "hello",
            "target_uri": "viking://user/peers/sender-1/memories/preferences/",
            "context_type": "memory",
            "limit": 1,
        },
    ]
    assert [item["_recall_type"] for item in result] == ["events"]


@pytest.mark.asyncio
async def test_viking_peer_profiles_use_target_peer_actor_clients(monkeypatch, tmp_path):
    create_calls = []
    closed = []

    class _FakeClient:
        def __init__(self, actor_peer_id):
            self.actor_peer_id = actor_peer_id

        async def read_peer_profile(self, peer_id):
            return f"profile for {peer_id} via {self.actor_peer_id}"

        async def close(self):
            closed.append(self.actor_peer_id)

    async def _fake_create(**kwargs):
        create_calls.append(kwargs)
        return _FakeClient(kwargs.get("actor_peer_id"))

    monkeypatch.setattr("vikingbot.agent.memory.VikingClient.create", _fake_create)

    store = MemoryStore(tmp_path)
    result = await store.get_viking_peer_profiles(
        workspace_id="workspace",
        peer_ids=["speaker-a", "speaker-b"],
        use_peer_actor_scope=True,
    )

    assert [call["actor_peer_id"] for call in create_calls] == ["speaker-a", "speaker-b"]
    assert closed == ["speaker-a", "speaker-b"]
    assert "profile for speaker-a via speaker-a" in result
    assert "profile for speaker-b via speaker-b" in result


@pytest.mark.asyncio
async def test_viking_memory_context_uses_target_peer_actor_for_additional_peer_reads(
    monkeypatch, tmp_path
):
    create_actor_ids = []
    read_calls = []
    closed = []

    class _FakeClient:
        def __init__(self, actor_peer_id):
            self.actor_peer_id = actor_peer_id

        def _current_peer_memory_target_uri(self, peer_id):
            return f"viking://user/peers/{peer_id}/memories/"

        async def find(self, *, query, target_uri, context_type=None, limit):
            if target_uri.endswith("/events/"):
                return {
                    "memories": [
                        {
                            "uri": (
                                f"viking://user/peers/{self.actor_peer_id}/memories/events/e1.md"
                            ),
                            "score": 0.9,
                        }
                    ]
                }
            return {"memories": []}

        async def read_content(self, uri, level="read"):
            read_calls.append((self.actor_peer_id, uri, level))
            return f"content via {self.actor_peer_id}"

        async def close(self):
            closed.append(self.actor_peer_id)

    async def _fake_create(**kwargs):
        create_actor_ids.append(kwargs.get("actor_peer_id"))
        return _FakeClient(kwargs.get("actor_peer_id"))

    monkeypatch.setattr(
        "vikingbot.agent.memory.load_config",
        lambda: _make_config(
            "user",
            memory_recall_events_limit=2,
            memory_recall_entities_limit=0,
            memory_recall_preferences_limit=0,
            memory_recall_max_chars=1000,
        ),
    )
    monkeypatch.setattr("vikingbot.agent.memory.VikingClient.create", _fake_create)

    store = MemoryStore(tmp_path)
    result = await store.get_viking_memory_context(
        current_message="hello",
        workspace_id="workspace",
        sender_id="sender-1",
        peer_ids=["speaker-a"],
    )

    assert create_actor_ids == ["sender-1", "speaker-a", "speaker-a"]
    assert read_calls == [
        ("sender-1", "viking://user/peers/sender-1/memories/events/e1.md", "read"),
        ("speaker-a", "viking://user/peers/speaker-a/memories/events/e1.md", "read"),
    ]
    assert closed == ["speaker-a", "speaker-a", "sender-1"]
    assert "content via sender-1" in result
    assert "content via speaker-a" in result


@pytest.mark.asyncio
async def test_viking_memory_type_quota_groups_with_event_summaries_and_uris(monkeypatch, tmp_path):
    clients = []
    base_uri = "viking://user/default/peers/sender-1/memories"

    class _FakeClient:
        def __init__(self):
            self.find_calls = []
            self.contents = {
                f"{base_uri}/events/e1.md": "short event",
                f"{base_uri}/events/e2.md": (
                    "Summary: long event summary\n"
                    "2023-01-01 (Sunday) ChatLog:\n"
                    "full long event details " + ("x" * 800)
                ),
                f"{base_uri}/events/e3.md": "legacy event without summary",
                f"{base_uri}/entities/en1.md": "short entity",
                f"{base_uri}/entities/en2.md": "long entity " + ("y" * 500),
                f"{base_uri}/preferences/p1.md": "first preference " + ("z" * 700),
                f"{base_uri}/preferences/p2.md": "second preference",
            }

        def build_current_memory_target_uris(self, *, peer_ids=None, include_self=True):
            return [base_uri]

        async def find(self, *, query, target_uri, limit):
            self.find_calls.append((query, target_uri, limit))
            if target_uri.endswith("/events/"):
                return {
                    "memories": [
                        {"uri": f"{base_uri}/events/e2.md", "score": 0.8},
                        {"uri": f"{base_uri}/events/e1.md", "score": 0.9},
                        {"uri": f"{base_uri}/events/e3.md", "score": 0.7},
                    ]
                }
            if target_uri.endswith("/entities/"):
                return {
                    "memories": [
                        {"uri": f"{base_uri}/entities/en2.md", "score": 0.8},
                        {"uri": f"{base_uri}/entities/en1.md", "score": 0.9},
                    ]
                }
            if target_uri.endswith("/preferences/"):
                return {
                    "memories": [
                        {"uri": f"{base_uri}/preferences/p1.md", "score": 0.9},
                        {"uri": f"{base_uri}/preferences/p2.md", "score": 0.8},
                    ]
                }
            return {"memories": []}

        async def read_content(self, uri, level="read"):
            return self.contents[uri]

        async def close(self):
            return None

    async def _fake_create(**_kwargs):
        client = _FakeClient()
        clients.append(client)
        return client

    monkeypatch.setattr(
        "vikingbot.agent.memory.load_config",
        lambda: _make_config("root", memory_recall_max_chars=1100),
    )
    monkeypatch.setattr("vikingbot.agent.memory.VikingClient.create", _fake_create)

    store = MemoryStore(tmp_path)
    result = await store.get_viking_memory_context(
        current_message="hello",
        workspace_id="workspace",
        sender_id="sender-1",
    )

    assert clients[0].find_calls == [
        ("hello", f"{base_uri}/events/", 10),
        ("hello", f"{base_uri}/entities/", 10),
        ("hello", f"{base_uri}/preferences/", 3),
    ]
    assert 'type="snippet"' not in result
    assert result.count('type="full"') == 3
    assert result.count('type="summary"') == 1
    assert result.count('type="uri"') == 3
    assert '<memory_group type="events">' in result
    assert '<memory_group type="entities">' in result
    assert '<memory_group type="preferences">' in result
    assert "Event memories. The URI path includes the event date." in result
    assert result.index('<memory_group type="events"') < result.index(
        '<memory_group type="entities"'
    )
    assert result.index('<memory_group type="entities"') < result.index(
        '<memory_group type="preferences"'
    )
    assert result.index("/events/e1.md") < result.index("/events/e2.md")
    assert result.index("/events/e2.md") < result.index("/events/e3.md")
    assert result.index("/events/e3.md") < result.index("/entities/en1.md")
    assert result.index("/entities/en1.md") < result.index("/entities/en2.md")
    assert result.index("/entities/en2.md") < result.index("/preferences/p1.md")
    assert result.index("/preferences/p1.md") < result.index("/preferences/p2.md")
    assert '<memory index="1" type="full">' in result
    assert '<memory index="2" type="summary">' in result
    assert '<memory index="3" type="full">' in result
    assert '<memory index="4" type="full">' in result
    assert '<memory index="5" type="uri">' in result
    assert '<memory index="6" type="uri">' in result
    assert '<memory index="7" type="uri">' in result
    assert "long event summary" in result
    assert "full long event details" not in result
    assert "legacy event without summary" in result
    assert "long entity " not in result
    assert "first preference " not in result
    assert "second preference" not in result


@pytest.mark.asyncio
async def test_viking_memory_type_quota_continues_after_oversized_entity(monkeypatch, tmp_path):
    base_uri = "viking://user/default/peers/sender-1/memories"

    class _FakeClient:
        def build_current_memory_target_uris(self, *, peer_ids=None, include_self=True):
            return [base_uri]

        async def find(self, *, query, target_uri, limit):
            if target_uri.endswith("/entities/"):
                return {
                    "memories": [
                        {"uri": f"{base_uri}/entities/long.md", "score": 0.9},
                        {"uri": f"{base_uri}/entities/short.md", "score": 0.8},
                    ]
                }
            return {"memories": []}

        async def read_content(self, uri, level="read"):
            if uri.endswith("/long.md"):
                return "long fact " + ("x" * 500)
            return "short fact"

        async def close(self):
            return None

    async def _fake_create(**_kwargs):
        return _FakeClient()

    monkeypatch.setattr(
        "vikingbot.agent.memory.load_config",
        lambda: _make_config("root", memory_recall_max_chars=1200),
    )
    monkeypatch.setattr("vikingbot.agent.memory.VikingClient.create", _fake_create)

    store = MemoryStore(tmp_path)
    result = await store.get_viking_memory_context(
        current_message="hello",
        workspace_id="workspace",
        sender_id="sender-1",
    )

    assert '<memory index="1" type="uri">' in result
    assert "<uri>viking://user/default/peers/sender-1/memories/entities/long.md</uri>" in result
    assert '<memory index="2" type="full">' in result
    assert "<uri>viking://user/default/peers/sender-1/memories/entities/short.md</uri>" in result
    assert "<content>short fact</content>" in result
    assert "long fact " not in result


@pytest.mark.asyncio
async def test_viking_memory_type_quota_does_not_overflow_preference_budget(monkeypatch, tmp_path):
    base_uri = "viking://user/default/peers/sender-1/memories"

    class _FakeClient:
        def build_current_memory_target_uris(self, *, peer_ids=None, include_self=True):
            return [base_uri]

        async def find(self, *, query, target_uri, limit):
            if target_uri.endswith("/preferences/"):
                return {
                    "memories": [
                        {"uri": f"{base_uri}/preferences/long.md", "score": 0.9},
                        {"uri": f"{base_uri}/preferences/short.md", "score": 0.8},
                    ]
                }
            return {"memories": []}

        async def read_content(self, uri, level="read"):
            if uri.endswith("/long.md"):
                return "very long preference " + ("x" * 500)
            return "short preference"

        async def close(self):
            return None

    async def _fake_create(**_kwargs):
        return _FakeClient()

    monkeypatch.setattr(
        "vikingbot.agent.memory.load_config",
        lambda: _make_config("root", memory_recall_max_chars=100),
    )
    monkeypatch.setattr("vikingbot.agent.memory.VikingClient.create", _fake_create)

    store = MemoryStore(tmp_path)
    result = await store.get_viking_memory_context(
        current_message="hello",
        workspace_id="workspace",
        sender_id="sender-1",
    )

    assert 'type="full"' not in result
    assert result.count('type="uri"') == 2
    assert "very long preference" not in result
    assert "short preference" not in result


@pytest.mark.asyncio
async def test_viking_memory_context_returns_empty_after_profile_filter(monkeypatch, tmp_path):
    class _FakeClient:
        async def search_memory(self, **_kwargs):
            return [
                {
                    "uri": "viking://user/default/peers/sender-1/memories/profile.md",
                    "score": 0.9,
                }
            ]

        async def close(self):
            return None

    async def _fake_create(**_kwargs):
        return _FakeClient()

    monkeypatch.setattr("vikingbot.agent.memory.load_config", lambda: _make_config("root"))
    monkeypatch.setattr("vikingbot.agent.memory.VikingClient.create", _fake_create)

    store = MemoryStore(tmp_path)
    result = await store.get_viking_memory_context(
        current_message="hello",
        workspace_id="workspace",
        sender_id="sender-1",
        user_ids=["legacy-user"],
    )

    assert result == ""


@pytest.mark.asyncio
async def test_openviking_search_uses_user_namespace(monkeypatch):
    monkeypatch.setattr(ov_server_module, "load_config", lambda: _make_config("root"))
    tool = VikingSearchTool()
    client = VikingClient()

    calls = []

    async def _search(query, target_uri=None, limit=20, user_id=None):
        calls.append((target_uri, user_id))
        return {"memories": [{"uri": target_uri, "abstract": "a", "score": 0.9, "is_leaf": True}]}

    async def _fake_get_client(_tool_context):
        return client

    monkeypatch.setattr(client, "search", _search)
    monkeypatch.setattr(tool, "_get_client", _fake_get_client)

    tool_context = SimpleNamespace(workspace_id="workspace", memory_owner_user_ids=["sender-1"])
    result = await tool.execute(tool_context, query="hello")

    assert "sender-1/memories" in result
    assert calls == [
        ("viking://resources/", None),
        ("viking://user/sender-1/memories/", "sender-1"),
        ("viking://user/sender-1/skills/", "sender-1"),
    ]


@pytest.mark.asyncio
async def test_openviking_search_user_key_mode_uses_current_user_namespace(monkeypatch):
    monkeypatch.setattr(ov_server_module, "load_config", lambda: _make_config("user"))
    tool = VikingSearchTool()
    client = VikingClient()

    calls = []

    async def _search(query, target_uri=None, limit=20, user_id=None):
        calls.append((target_uri, user_id))
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
        ("viking://resources/", None),
        ("viking://user/memories/", None),
        ("viking://user/skills/", None),
        ("viking://user/peers/sender-0/memories/", None),
        ("viking://user/peers/sender-1/memories/", None),
        ("viking://user/peers/sender-2/memories/", None),
    ]


@pytest.mark.asyncio
async def test_openviking_search_actor_client_expands_current_peer_scope(monkeypatch):
    tool = VikingSearchTool()
    calls = []

    class _ActorClient:
        actor_peer_id = "sender-0"

        def should_sender_fanout(self):
            return True

        def _memory_target_uri(self, _user_id=None):
            return "viking://user/memories/"

        def build_current_memory_target_uris(self, *, peer_ids=None, include_self=True):
            uris = ["viking://user/memories/"] if include_self else []
            uris.extend(f"viking://user/peers/{peer_id}/memories/" for peer_id in peer_ids or [])
            return uris

        async def search(self, query, target_uri=None, limit=20, user_id=None):
            calls.append((target_uri, user_id))
            return {
                "memories": [{"uri": "viking://user/peers/sender-0/memories/a.md", "score": 0.9}]
            }

        async def close(self):
            calls.append(("close", None))

    async def _fake_get_client(_tool_context):
        return _ActorClient()

    monkeypatch.setattr(tool, "_get_client", _fake_get_client)

    tool_context = SimpleNamespace(
        workspace_id="workspace",
        sender_id="sender-0",
        memory_peer_ids=["sender-1", "sender-2"],
    )
    result = await tool.execute(tool_context, query="hello")

    assert "sender-0/memories" in result
    assert calls == [
        ("viking://resources/", None),
        ("viking://user/memories/", None),
        ("viking://user/skills/", None),
        ("viking://user/peers/sender-0/memories/", None),
        ("viking://user/peers/sender-1/memories/", None),
        ("viking://user/peers/sender-2/memories/", None),
        ("close", None),
    ]


@pytest.mark.asyncio
async def test_openviking_tool_uses_authenticated_actor_instead_of_display_sender(monkeypatch):
    monkeypatch.setattr(ov_server_module, "load_config", lambda: _make_config("user"))
    tool = VikingSearchTool()

    result = await tool.execute(
        SimpleNamespace(
            workspace_id="workspace",
            sender_id="display-user",
            actor_peer_id="peer-a",
        ),
        query="hello",
    )

    first = _DummyHTTPClient.instances[0]
    assert first.kwargs["actor_peer_id"] == "peer-a"
    assert first.closed is True
    assert "No results found" in result


@pytest.mark.asyncio
async def test_openviking_grep_default_memory_expands_current_peer(monkeypatch):
    tool = VikingGrepTool()
    calls = []

    class _FakeClient:
        def _memory_target_uri(self, _user_id=None):
            return "viking://user/memories/"

        def build_current_memory_target_uris(self, *, peer_ids=None, include_self=True):
            uris = ["viking://user/memories/"] if include_self else []
            uris.extend(
                f"viking://user/default/peers/{peer_id}/memories/" for peer_id in peer_ids or []
            )
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
            uris.extend(
                f"viking://user/default/peers/{peer_id}/memories/" for peer_id in peer_ids or []
            )
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
            uris.extend(
                f"viking://user/default/peers/{peer_id}/memories/" for peer_id in peer_ids or []
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
                f"viking://user/admin/peers/{peer_id}/memories/" for peer_id in peer_ids or []
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
        async def get_viking_experience_context(self, **_kwargs):
            return ""

        async def get_viking_memory_context(self, **_kwargs):
            return ""

    context = ContextBuilder(workspace=tmp_path, sender_id="sender-1")
    context._memory = _EmptyMemory()

    user_info = await context._build_user_memory(
        session_key=SessionKey(type="cli", channel_id="default", chat_id="chat-1"),
        current_message="我会哪些语言",
        sender_id="sender-1",
        ov_tools_enable=True,
    )

    assert "OpenViking Memory Retrieval" in user_info
    assert "use openviking_search for the current question" in user_info
    assert "search again when the requested fact changes" in user_info
    assert "grouped by memory_type" in user_info
    assert "events contain atomic time-based facts" in user_info
    assert "entities contain stable topic/entity facts" in user_info
    assert "preferences contain likes, habits, and recurring tendencies" in user_info
    assert "full means the full memory content is already shown" in user_info
    assert "summary means only a summary is shown" in user_info
    assert "uri means only the URI is shown" in user_info
    assert "openviking_multi_read" in user_info


@pytest.mark.asyncio
async def test_context_memory_prefix_tells_agent_to_read_summary_and_uri_details(tmp_path):
    calls = []

    class _Memory:
        async def get_viking_experience_context(self, **_kwargs):
            return ""

        async def get_viking_memory_context(self, **kwargs):
            calls.append(kwargs)
            return (
                "### user memories:\n"
                '<memory index="1" type="summary">\n'
                "  <uri>viking://user/default/peers/sender-1/memories/events/e.md</uri>\n"
                "  <summary>important clue</summary>\n"
                "</memory>"
            )

    context = ContextBuilder(workspace=tmp_path, sender_id="sender-1")
    context._memory = _Memory()

    user_info = await context._build_user_memory(
        session_key=SessionKey(type="cli", channel_id="default", chat_id="chat-1"),
        current_message="问题",
        sender_id="sender-1",
        ov_tools_enable=True,
        memory_peer_ids=["peer-1"],
        memory_owner_user_ids=["owner-1"],
    )

    assert calls == [
        {
            "current_message": "问题",
            "workspace_id": "cli__default__chat-1",
            "sender_id": "sender-1",
            "peer_ids": ["peer-1"],
            "user_ids": ["owner-1"],
            "openviking_connection": None,
        }
    ]
    assert context.latest_relevant_memories
    assert "## openviking_search(query=[user_query])" in user_info
    assert "grouped by memory_type" in user_info
    assert "full means the full memory content is already shown" in user_info
    assert "summary means only a summary is shown" in user_info
    assert "uri means only the URI is shown" in user_info
    assert "openviking_multi_read" in user_info
    assert "important clue" in user_info


@pytest.mark.asyncio
async def test_context_loads_profiles_for_memory_peers(tmp_path):
    calls = {"sender": [], "peers": []}

    class _ProfileMemory:
        async def get_viking_peer_profile(self, **kwargs):
            calls["sender"].append(kwargs["peer_id"])
            return "sender profile"

        async def get_viking_peer_profiles(self, **kwargs):
            calls["peers"].append(kwargs)
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
    assert calls["peers"] == [
        {
            "workspace_id": "cli__default__chat-1",
            "peer_ids": ["speaker-a", "speaker-b"],
            "openviking_connection": None,
            "use_peer_actor_scope": True,
        }
    ]
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


@pytest.mark.asyncio
async def test_experience_reminder_follows_direct_case_experience_links(monkeypatch, tmp_path):
    from openviking.session.memory.dataclass import MemoryFile, StoredLink
    from openviking.session.memory.utils.memory_file_utils import MemoryFileUtils

    case_uri = "viking://user/admin/memories/cases/case1.md"
    exp_uri = "viking://user/admin/memories/experiences/exp1.md"
    direct_exp_uri = "viking://user/admin/memories/experiences/direct.md"
    MemoryFileUtils.write(
        MemoryFile(
            uri=case_uri,
            content="# case1\n\n## Linked Experiences\n- exp1",
            memory_type="cases",
            links=[
                StoredLink(
                    from_uri=case_uri,
                    to_uri=exp_uri,
                    link_type="related_to",
                    weight=1.0,
                    match_text=None,
                    description="",
                ).model_dump()
            ],
        )
    )

    class _FakeClient:
        admin_user_id = "admin"

        def __init__(self):
            self.search_calls = []
            self.search_experience_calls = []
            self.read_calls = []

        async def search(self, *, query, target_uri, limit):
            self.search_calls.append((query, target_uri, limit))
            if target_uri.endswith("/cases/"):
                return {"memories": [{"uri": case_uri, "score": 0.9}]}
            return {"memories": []}

        async def search_experiences(self, query, limit=5):
            self.search_experience_calls.append((query, limit))
            return [{"uri": direct_exp_uri, "score": 0.6}] if limit > 0 else []

        async def read_content(self, uri, level="read"):
            self.read_calls.append((uri, level))
            if uri == case_uri:
                return "# case1\n\n## Linked Experiences\n- [exp1](../experiences/exp1.md)"
            if uri == exp_uri:
                return "linked exp content"
            if uri == direct_exp_uri:
                return "direct exp content"
            return ""

        async def close(self):
            return None

    fake_client = _FakeClient()

    async def _fake_create(**_kwargs):
        return fake_client

    monkeypatch.setattr(
        "vikingbot.agent.memory.load_config",
        lambda: _make_config(
            "root",
            case_recall_limit=1,
            exp_recall_limit=2,
            exp_recall_max_chars=4000,
        ),
    )
    monkeypatch.setattr("vikingbot.agent.memory.VikingClient.create", _fake_create)

    content, uris = await MemoryStore(tmp_path).get_viking_experience_reminder(
        query="hello",
        workspace_id="workspace",
    )

    assert fake_client.search_calls == [("hello", "viking://user/admin/memories/cases/", 1)]
    assert fake_client.search_experience_calls == []
    assert (case_uri, "read") in fake_client.read_calls
    assert "linked exp content" in content
    assert "direct exp content" not in content
    assert exp_uri in uris
    assert direct_exp_uri not in uris


@pytest.mark.asyncio
async def test_tau2_experience_reminder_loads_case_by_exact_task_not_query(monkeypatch, tmp_path):
    from openviking.session.memory.dataclass import MemoryFile, StoredLink
    from openviking.session.memory.utils.memory_file_utils import MemoryFileUtils

    matched_case_uri = "viking://user/admin/memories/cases/tau2_airline_train_1.md"
    wrong_case_uri = "viking://user/admin/memories/cases/tau2_airline_train_9.md"
    matched_exp_uri = "viking://user/admin/memories/experiences/matched.md"
    wrong_exp_uri = "viking://user/admin/memories/experiences/wrong.md"

    def _case_content(case_uri, exp_uri, *, task_no, task_id):
        return MemoryFileUtils.write(
            MemoryFile(
                uri=case_uri,
                content="",
                memory_type="cases",
                extra_fields={
                    "case_name": case_uri.rsplit("/", 1)[-1].removesuffix(".md"),
                    "task_signature": f"tau2:airline:train:{task_id}",
                    "input": json.dumps(
                        {
                            "domain": "airline",
                            "split": "train",
                            "data_split": "airline_train",
                            "task_no": task_no,
                            "task_id": str(task_id),
                        }
                    ),
                },
                links=[
                    StoredLink(
                        from_uri=case_uri,
                        to_uri=exp_uri,
                        link_type="related_to",
                        weight=1.0,
                        match_text=None,
                        description="",
                    ).model_dump()
                ],
            ),
            content_template=(
                "# {{ case_name }}\n\n"
                "## Task Signature\n{{ task_signature }}\n\n"
                "## Input\n{{ input }}\n\n"
                "## Linked Experiences\n"
                "{% for link in links or [] %}"
                "- [{{ uri_basename(link.to_uri) }}]({{ link_target(link.to_uri) }})\n"
                "{% endfor %}"
            ),
        )

    raw_cases = {
        matched_case_uri: _case_content(matched_case_uri, matched_exp_uri, task_no=1, task_id=7),
        wrong_case_uri: _case_content(wrong_case_uri, wrong_exp_uri, task_no=9, task_id=99),
    }
    visible_cases = {
        uri: raw.split("<!-- MEMORY_FIELDS", 1)[0].strip() for uri, raw in raw_cases.items()
    }

    class _FakeClient:
        admin_user_id = "admin"

        def __init__(self):
            self.search_calls = []
            self.read_calls = []

        async def search(self, *, query, target_uri, limit):
            self.search_calls.append((query, target_uri, limit))
            return {"memories": [{"uri": wrong_case_uri, "score": 0.99}]}

        async def read_content(self, uri, level="read"):
            self.read_calls.append((uri, level))
            if uri in visible_cases:
                return visible_cases[uri]
            if uri == matched_exp_uri:
                return "matched exp content"
            if uri == wrong_exp_uri:
                return "wrong exp content"
            return ""

        async def close(self):
            return None

    fake_client = _FakeClient()

    async def _fake_create(**_kwargs):
        return fake_client

    monkeypatch.setattr(
        "vikingbot.agent.memory.load_config",
        lambda: _make_config("root", case_recall_limit=3, exp_recall_max_chars=4000),
    )
    monkeypatch.setattr("vikingbot.agent.memory.VikingClient.create", _fake_create)

    content, uris = await MemoryStore(tmp_path).get_viking_experience_reminder(
        query="same customer cancellation text could match wrong case",
        workspace_id="workspace",
        case_lookup={
            "benchmark": "tau2",
            "domain": "airline",
            "split": "train",
            "data_split": "airline_train",
            "task_no": 1,
            "task_id": "7",
            "case_name": "tau2_airline_train_1",
            "task_signature": "tau2:airline:train:7",
            "strict": True,
        },
    )

    assert fake_client.search_calls == []
    assert (matched_case_uri, "read") in fake_client.read_calls
    assert (wrong_case_uri, "read") not in fake_client.read_calls
    assert "matched exp content" in content
    assert "wrong exp content" not in content
    assert uris == [matched_exp_uri]


@pytest.mark.asyncio
async def test_tau2_experience_reminder_returns_empty_when_exact_case_mismatches(
    monkeypatch, tmp_path
):
    from openviking.session.memory.dataclass import MemoryFile, StoredLink
    from openviking.session.memory.utils.memory_file_utils import MemoryFileUtils

    case_uri = "viking://user/admin/memories/cases/tau2_airline_train_1.md"
    exp_uri = "viking://user/admin/memories/experiences/wrong.md"
    raw_case = MemoryFileUtils.write(
        MemoryFile(
            uri=case_uri,
            content="",
            memory_type="cases",
            extra_fields={
                "case_name": "tau2_airline_train_1",
                "task_signature": "tau2:airline:train:99",
                "input": json.dumps(
                    {
                        "domain": "airline",
                        "split": "train",
                        "data_split": "airline_train",
                        "task_no": 1,
                        "task_id": "99",
                    }
                ),
            },
            links=[
                StoredLink(
                    from_uri=case_uri,
                    to_uri=exp_uri,
                    link_type="related_to",
                    weight=1.0,
                    match_text=None,
                    description="",
                ).model_dump()
            ],
        ),
        content_template=(
            "# {{ case_name }}\n\n"
            "## Task Signature\n{{ task_signature }}\n\n"
            "## Input\n{{ input }}\n\n"
            "## Linked Experiences\n"
            "{% for link in links or [] %}"
            "- [{{ uri_basename(link.to_uri) }}]({{ link_target(link.to_uri) }})\n"
            "{% endfor %}"
        ),
    )
    visible_case = raw_case.split("<!-- MEMORY_FIELDS", 1)[0].strip()

    class _FakeClient:
        admin_user_id = "admin"

        def __init__(self):
            self.search_calls = []
            self.read_calls = []

        async def search(self, *, query, target_uri, limit):
            self.search_calls.append((query, target_uri, limit))
            return {"memories": [{"uri": case_uri, "score": 0.99}]}

        async def read_content(self, uri, level="read"):
            self.read_calls.append((uri, level))
            if uri == case_uri:
                return visible_case
            if uri == exp_uri:
                return "wrong exp content"
            return ""

        async def close(self):
            return None

    fake_client = _FakeClient()

    async def _fake_create(**_kwargs):
        return fake_client

    monkeypatch.setattr(
        "vikingbot.agent.memory.load_config",
        lambda: _make_config("root", case_recall_limit=3, exp_recall_max_chars=4000),
    )
    monkeypatch.setattr("vikingbot.agent.memory.VikingClient.create", _fake_create)

    content, uris = await MemoryStore(tmp_path).get_viking_experience_reminder(
        query="query would retrieve wrong case",
        workspace_id="workspace",
        case_lookup={
            "benchmark": "tau2",
            "domain": "airline",
            "split": "train",
            "data_split": "airline_train",
            "task_no": 1,
            "task_id": "7",
            "case_name": "tau2_airline_train_1",
            "task_signature": "tau2:airline:train:7",
            "strict": True,
        },
    )

    assert fake_client.search_calls == []
    assert (case_uri, "read") in fake_client.read_calls
    assert content == ""
    assert uris == []
