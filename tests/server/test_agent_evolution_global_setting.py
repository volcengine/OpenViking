# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0

from types import SimpleNamespace
from unittest.mock import AsyncMock

import httpx
import pytest
import pytest_asyncio

from openviking.pyagfs import AGFSNotFoundError
from openviking.server.app import create_app
from openviking.server.auth.plugins import DevAuthPlugin
from openviking.server.config import AgentEvolutionConfig, ServerConfig, UserConfig
from openviking.server.dependencies import set_service
from openviking.server.identity import RequestContext, Role
from openviking.service.core import OpenVikingService
from openviking.service.session_service import SessionService
from openviking.session import Session
from openviking_cli.session.user_id import UserIdentifier


class _FakeAGFS:
    def __init__(self):
        self.files: dict[str, bytes] = {}

    def read(self, path, ctx=None):
        del ctx
        if path not in self.files:
            raise AGFSNotFoundError(path)
        return self.files[path]

    def write(self, path, data, ctx=None):
        del ctx
        self.files[path] = bytes(data)
        return path

    def mv(self, old_path, new_path, ctx=None):
        del ctx
        self.files[new_path] = self.files.pop(old_path)
        return {}

    def ensure_parent_dirs(self, path, ctx=None):
        del path
        del ctx
        return {}

    def rm(self, path, ctx=None):
        del ctx
        self.files.pop(path, None)
        return {}

    def pathlock_acquire_exact(self, ctx, path, timeout_secs, owner_lease_ref):
        del ctx
        del path
        del timeout_secs
        del owner_lease_ref
        return {"lease_ref": "test-lease"}

    def pathlock_release(self, ctx, lease):
        del ctx
        del lease


class _FakeAclManager:
    def __init__(self):
        self.runtime_config = None

    def set_runtime_config_manager(self, runtime_config) -> None:
        self.runtime_config = runtime_config

    async def is_enabled(self, account_id: str) -> bool:
        setting = await self.runtime_config.get_account(account_id, "acl")
        return setting.enabled if setting is not None else False


class _FakeRuntimeConfig:
    def __init__(self):
        self.values = {}

    async def get_account(self, account_id: str, field: str):
        return self.values.get((account_id, field))


class _FakeApiKeyManager:
    """Minimal admin-gate stand-in: account existence + user refresh no-ops."""

    def __init__(self, account_ids):
        self._account_ids = list(account_ids)

    async def refresh_accounts_from_store(self):
        return None

    async def refresh_account_users_from_store(self, account_id):
        return None

    def ensure_account_active(self, account_id):
        if account_id not in self._account_ids:
            raise AssertionError(f"unexpected account: {account_id}")

    def get_accounts(self):
        return [{"account_id": aid} for aid in self._account_ids]


@pytest.fixture
def fake_viking_fs():
    return SimpleNamespace(agfs=_FakeAGFS(), acl_manager=_FakeAclManager())


@pytest_asyncio.fixture
async def settings_http(fake_viking_fs, monkeypatch):
    monkeypatch.setattr(
        "openviking.server.routers.admin.get_openviking_config",
        lambda: SimpleNamespace(default_account="default"),
    )
    from openviking.config.binding import manager_over_source
    from openviking.config.source import MemoryConfigSource
    from openviking_cli.utils.config.open_viking_config import OpenVikingConfigSingleton

    OpenVikingConfigSingleton.reset_instance()
    OpenVikingConfigSingleton.initialize(
        config_dict={
            "embedding": {
                "dense": {
                    "provider": "openai",
                    "model": "test-embedder",
                    "api_key": "test-key",
                    "dimension": 1024,
                }
            },
        }
    )

    source = MemoryConfigSource()
    runtime_config = manager_over_source(source)
    await runtime_config.initialize()
    sessions = SessionService(viking_fs=fake_viking_fs)
    sessions.set_runtime_config_manager(runtime_config)
    fake_viking_fs.acl_manager.set_runtime_config_manager(runtime_config)
    service = SimpleNamespace(
        sessions=sessions,
        viking_fs=fake_viking_fs,
        runtime_config_manager=runtime_config,
    )
    app = create_app(config=ServerConfig(), service=service)
    set_service(service)
    app.state.auth_plugin = DevAuthPlugin()
    # Admin routes gate on a present APIKeyManager; DevAuthPlugin resolves ROOT
    # but leaves it None, so provide a stand-in that reports the default account.
    app.state.api_key_manager = _FakeApiKeyManager(["default"])
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://testserver") as client:
        yield client, service
    OpenVikingConfigSingleton.reset_instance()


def test_agent_evolution_is_disabled_by_default():
    assert ServerConfig().agent_evolution.enabled is False


def test_agent_evolution_can_be_enabled_as_account_default():
    config = ServerConfig.model_validate({"agent_evolution": {"enabled": True}})

    assert config.agent_evolution.enabled is True


def test_server_agent_evolution_seeds_runtime_cluster_baseline():
    sessions = SessionService()
    service = object.__new__(OpenVikingService)
    service._session_service = sessions

    service.set_agent_evolution_config(AgentEvolutionConfig(enabled=True))

    assert service._agent_evolution_base_config.enabled is True
    assert sessions._agent_evolution_default_enabled is True


async def test_server_agent_evolution_updates_initialized_runtime_baseline():
    from openviking.config.binding import manager_over_source
    from openviking.config.source import MemoryConfigSource
    from openviking_cli.utils.config import set_openviking_config
    from openviking_cli.utils.config.open_viking_config import (
        OpenVikingConfig,
        OpenVikingConfigSingleton,
    )

    base = OpenVikingConfig.from_dict({})
    set_openviking_config(base)
    manager = manager_over_source(MemoryConfigSource(), base_config=base)
    await manager.initialize()
    service = object.__new__(OpenVikingService)
    service._config = base
    service._runtime_config_manager = manager
    service._session_service = SessionService()
    service.set_agent_evolution_config(AgentEvolutionConfig(enabled=True))
    try:
        await service.apply_agent_evolution_config()
        assert (await manager.get_account("default", "agent_evolution")).enabled
    finally:
        OpenVikingConfigSingleton.reset_instance()


def test_server_default_memory_policy_is_configured_on_session_service(
    fake_viking_fs, monkeypatch
):
    monkeypatch.setattr(
        "openviking.service.session_service.get_default_registry",
        lambda: SimpleNamespace(list_names=lambda **_: ["profile"]),
    )
    sessions = SessionService(viking_fs=fake_viking_fs)
    service = SimpleNamespace(sessions=sessions)
    config = ServerConfig(
        user_config_defaults=UserConfig(memory_policy={"memory_types": ["profile"]})
    )

    create_app(config=config, service=service)

    assert sessions._default_user_memory_policy == {
        "self": {"enabled": True},
        "peer": {"enabled": True},
        "memory_types": ["profile"],
    }


async def test_existing_session_observes_updated_account_value(fake_viking_fs):
    runtime_config = _FakeRuntimeConfig()
    service = SessionService(viking_fs=fake_viking_fs)
    service.set_agent_evolution_config(AgentEvolutionConfig(enabled=False))
    service.set_runtime_config_manager(runtime_config)
    session = service.session(
        RequestContext(user=UserIdentifier.the_default_user(), role=Role.ROOT)
    )

    runtime_config.values[("default", "agent_evolution")] = SimpleNamespace(enabled=True)

    assert await session._agent_evolution_enabled_provider() is True


def test_session_positional_usage_reporter_remains_compatible():
    reporter = object()

    session = Session(
        object(),
        None,
        None,
        None,
        None,
        "session-id",
        None,
        8000,
        None,
        True,
        reporter,
    )

    assert session._usage_reporter is reporter
    assert session._agent_evolution_enabled_provider is None


async def test_account_settings_admin_api_reads_and_updates_effective_value(
    settings_http,
):
    client, service = settings_http
    service.sessions.set_agent_evolution_config(AgentEvolutionConfig(enabled=False))

    initial = await client.get("/api/v1/admin/accounts/default/settings")
    assert initial.status_code == 200, initial.text
    assert initial.json()["result"] == {
        "account_id": "default",
        "settings": {
            "agent_evolution": {"enabled": False},
            "acl": {"enabled": False},
        },
        "overrides": {},
    }

    updated = await client.patch(
        "/api/v1/admin/accounts/default/settings",
        json={
            "agent_evolution": {"enabled": True},
            "acl": {"enabled": True},
        },
    )
    assert updated.status_code == 200, updated.text
    assert updated.json()["result"]["settings"]["agent_evolution"]["enabled"] is True
    assert updated.json()["result"]["settings"]["acl"] == {"enabled": True}
    assert updated.json()["result"]["overrides"] == {
        "agent_evolution": {"enabled": True},
        "acl": {"enabled": True},
    }
    assert (
        await service.runtime_config_manager.get_account("default", "agent_evolution")
    ).enabled
    assert (await service.runtime_config_manager.get_account("default", "acl")).enabled
    assert await service.viking_fs.acl_manager.is_enabled("default")


async def test_legacy_account_settings_preserves_null_and_empty_object_semantics(
    settings_http,
):
    client, _ = settings_http
    enabled = await client.patch(
        "/api/v1/admin/accounts/default/settings",
        json={
            "agent_evolution": {"enabled": True},
            "acl": {"enabled": True},
        },
    )
    assert enabled.status_code == 200, enabled.text

    unchanged = await client.patch(
        "/api/v1/admin/accounts/default/settings",
        json={"agent_evolution": None, "acl": None},
    )
    assert unchanged.status_code == 200, unchanged.text
    assert unchanged.json()["result"]["overrides"] == {
        "agent_evolution": {"enabled": True},
        "acl": {"enabled": True},
    }

    disabled = await client.patch(
        "/api/v1/admin/accounts/default/settings",
        json={"acl": {}},
    )
    assert disabled.status_code == 200, disabled.text
    assert disabled.json()["result"]["overrides"]["acl"] == {"enabled": False}


async def test_account_configuration_exposes_three_state_layer(settings_http):
    client, _ = settings_http

    updated = await client.patch(
        "/api/v1/admin/accounts/default/configuration",
        json={
            "settings": {
                "github": {"token": "account-token"},
                "acl": {"enabled": True},
            }
        },
    )
    assert updated.status_code == 200, updated.text
    assert updated.json()["result"] == {
        "account_id": "default",
        "settings": {
            "github": {"token": "account-token"},
            "acl": {"enabled": True},
        },
    }

    removed = await client.patch(
        "/api/v1/admin/accounts/default/configuration",
        json={"settings": {"acl": None}},
    )
    assert removed.status_code == 200, removed.text
    assert removed.json()["result"]["settings"] == {
        "github": {"token": "account-token"}
    }


async def test_cluster_agent_evolution_override_is_account_fallback(settings_http):
    client, service = settings_http

    updated = await client.patch(
        "/api/v1/admin/configuration",
        json={"settings": {"agent_evolution": {"enabled": True}}},
    )
    assert updated.status_code == 200, updated.text
    assert updated.json()["result"]["settings"] == {
        "agent_evolution": {"enabled": True}
    }
    assert await service.sessions.get_agent_evolution_enabled("default")

    await service.runtime_config_manager.patch_account(
        "default", {"agent_evolution": {"enabled": False}}
    )
    assert not await service.sessions.get_agent_evolution_enabled("default")

    await service.runtime_config_manager.patch_account(
        "default", {"agent_evolution": None}
    )
    assert await service.sessions.get_agent_evolution_enabled("default")


async def test_legacy_admin_configuration_routes_are_deprecated(settings_http):
    client, _ = settings_http
    schema = client._transport.app.openapi()

    assert schema["paths"]["/api/v1/admin/agent-evolution"]["get"]["deprecated"]
    assert schema["paths"]["/api/v1/admin/agent-evolution"]["put"]["deprecated"]
    legacy = schema["paths"]["/api/v1/admin/accounts/{account_id}/settings"]
    assert legacy["get"]["deprecated"]
    assert legacy["patch"]["deprecated"]
    assert not schema["paths"][
        "/api/v1/admin/accounts/{account_id}/configuration"
    ]["patch"].get("deprecated", False)
    assert "/api/v1/admin/configuration" in schema["paths"]
    assert "/api/v1/admin/settings" not in schema["paths"]


async def test_account_settings_admin_api_rejects_non_allowlisted_fields(
    settings_http,
):
    client, _ = settings_http
    response = await client.patch(
        "/api/v1/admin/accounts/default/settings",
        json={"root_api_key": "not-allowed"},
    )

    assert response.status_code == 400


async def test_agent_evolution_endpoint_keeps_name_and_uses_account_settings(
    settings_http,
):
    client, service = settings_http
    updated = await client.put(
        "/api/v1/admin/agent-evolution",
        json={"enabled": True},
    )
    assert updated.status_code == 200, updated.text
    assert updated.json()["result"] == {
        "enabled": True,
        "account_id": "default",
    }

    response = await client.get("/api/v1/admin/agent-evolution")

    assert response.status_code == 200, response.text
    assert response.json()["result"]["enabled"] is True
    assert (
        await service.runtime_config_manager.get_account("default", "agent_evolution")
    ).enabled


def test_deprecated_user_agent_evolution_config_is_not_persisted():
    config = UserConfig.model_validate({"agent_evolution": {"enabled": True}})

    assert config.agent_evolution.enabled is True
    assert "agent_evolution" not in config.model_dump(exclude_none=True)


async def test_manual_extract_respects_account_setting(fake_viking_fs):
    extract = AsyncMock(return_value=[])
    compressor = SimpleNamespace(extract_long_term_memories=extract)
    service = SessionService(
        viking_fs=fake_viking_fs,
        session_compressor=compressor,
    )
    runtime_config = _FakeRuntimeConfig()
    runtime_config.values[("default", "agent_evolution")] = SimpleNamespace(enabled=True)
    service.set_runtime_config_manager(runtime_config)
    session = SimpleNamespace(
        uri="viking://user/default/sessions/test-session",
        messages=[],
    )
    service.get = AsyncMock(return_value=session)
    ctx = RequestContext(user=UserIdentifier.the_default_user(), role=Role.ROOT)

    await service.extract("test-session", ctx)

    assert extract.await_args.kwargs["agent_evolution_enabled"] is True
