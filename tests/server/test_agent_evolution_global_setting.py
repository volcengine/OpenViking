# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0

import json
from types import SimpleNamespace
from unittest.mock import AsyncMock

import httpx
import pytest
import pytest_asyncio

from openviking.pyagfs import AGFSNotFoundError
from openviking.server.account_settings import (
    AccountAgentEvolutionSettings,
    AccountSettingsPatch,
    account_settings_backup_path,
    account_settings_path,
    read_account_settings,
    update_account_settings,
)
from openviking.server.agent_evolution_config import AgentEvolutionConfigProvider
from openviking.server.app import create_app
from openviking.server.auth.plugins import DevAuthPlugin
from openviking.server.config import AgentEvolutionConfig, ServerConfig, UserConfig
from openviking.server.dependencies import set_service
from openviking.server.identity import RequestContext, Role
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


@pytest.fixture
def fake_viking_fs():
    return SimpleNamespace(agfs=_FakeAGFS())


@pytest_asyncio.fixture
async def settings_http(fake_viking_fs, monkeypatch):
    monkeypatch.setattr(
        "openviking.server.routers.admin.get_openviking_config",
        lambda: SimpleNamespace(default_account="default"),
    )
    sessions = SessionService(viking_fs=fake_viking_fs)
    service = SimpleNamespace(sessions=sessions, viking_fs=fake_viking_fs)
    app = create_app(config=ServerConfig(), service=service)
    set_service(service)
    app.state.auth_plugin = DevAuthPlugin()
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://testserver") as client:
        yield client, service


def _patch(enabled: bool) -> AccountSettingsPatch:
    return AccountSettingsPatch(agent_evolution=AccountAgentEvolutionSettings(enabled=enabled))


def test_agent_evolution_is_disabled_by_default():
    assert ServerConfig().agent_evolution.enabled is False


def test_agent_evolution_can_be_enabled_as_account_default():
    config = ServerConfig.model_validate({"agent_evolution": {"enabled": True}})

    assert config.agent_evolution.enabled is True


async def test_existing_session_observes_updated_account_value(fake_viking_fs):
    service = SessionService(viking_fs=fake_viking_fs)
    service.set_agent_evolution_config(AgentEvolutionConfig(enabled=False))
    service.set_agent_evolution_config_path(None)
    session = service.session(
        RequestContext(user=UserIdentifier.the_default_user(), role=Role.ROOT)
    )

    await update_account_settings(fake_viking_fs, "default", _patch(True))

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


async def test_provider_reloads_ov_conf_and_account_override(fake_viking_fs, tmp_path):
    config_path = tmp_path / "ov.conf"
    config_path.write_text(
        json.dumps({"server": {"agent_evolution": {"enabled": False}}}),
        encoding="utf-8",
    )
    provider = AgentEvolutionConfigProvider(
        default_enabled=True,
        viking_fs=fake_viking_fs,
        config_path=config_path,
    )

    assert await provider.is_enabled("default") is False

    config_path.write_text(
        json.dumps({"server": {"agent_evolution": {"enabled": True}}}),
        encoding="utf-8",
    )
    assert await provider.is_enabled("default") is True

    await update_account_settings(fake_viking_fs, "default", _patch(False))
    assert await provider.is_enabled("default") is False


async def test_account_overrides_are_isolated(fake_viking_fs):
    provider = AgentEvolutionConfigProvider(
        default_enabled=False,
        viking_fs=fake_viking_fs,
    )
    await update_account_settings(fake_viking_fs, "account-a", _patch(True))

    assert await provider.is_enabled("account-a") is True
    assert await provider.is_enabled("account-b") is False


async def test_account_settings_update_backs_up_previous_file(fake_viking_fs):
    await update_account_settings(fake_viking_fs, "default", _patch(False))
    await update_account_settings(fake_viking_fs, "default", _patch(True))

    current = fake_viking_fs.agfs.files[account_settings_path("default")]
    backup = fake_viking_fs.agfs.files[account_settings_backup_path("default")]
    current_payload = json.loads(current.decode("utf-8"))
    backup_payload = json.loads(backup.decode("utf-8"))

    assert current_payload["agent_evolution"]["enabled"] is True
    assert backup_payload["agent_evolution"]["enabled"] is False


async def test_account_settings_admin_api_reads_and_updates_effective_value(
    settings_http,
):
    client, service = settings_http
    service.sessions.set_agent_evolution_config(AgentEvolutionConfig(enabled=False))

    initial = await client.get("/api/v1/admin/accounts/default/settings")
    assert initial.status_code == 200, initial.text
    assert initial.json()["result"] == {
        "account_id": "default",
        "settings": {"agent_evolution": {"enabled": False}},
        "overrides": {},
    }

    updated = await client.patch(
        "/api/v1/admin/accounts/default/settings",
        json={"agent_evolution": {"enabled": True}},
    )
    assert updated.status_code == 200, updated.text
    assert updated.json()["result"]["settings"]["agent_evolution"]["enabled"] is True
    assert updated.json()["result"]["overrides"] == {"agent_evolution": {"enabled": True}}
    assert (await read_account_settings(service.viking_fs, "default")).agent_evolution.enabled


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
    assert (await read_account_settings(service.viking_fs, "default")).agent_evolution.enabled


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
    session = SimpleNamespace(
        uri="viking://user/default/sessions/test-session",
        messages=[],
    )
    service.get = AsyncMock(return_value=session)
    await update_account_settings(fake_viking_fs, "default", _patch(True))
    ctx = RequestContext(user=UserIdentifier.the_default_user(), role=Role.ROOT)

    await service.extract("test-session", ctx)

    assert extract.await_args.kwargs["agent_evolution_enabled"] is True
