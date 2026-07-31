# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0

import json
from types import SimpleNamespace
from unittest.mock import AsyncMock

import httpx

from openviking.server.agent_evolution_config import AgentEvolutionConfigProvider
from openviking.server.app import create_app
from openviking.server.config import AgentEvolutionConfig, ServerConfig, UserConfig
from openviking.server.identity import RequestContext, Role
from openviking.service.session_service import SessionService
from openviking.session import Session
from openviking_cli.session.user_id import UserIdentifier
from openviking_cli.utils.config import OPENVIKING_CONFIG_ENV


def test_agent_evolution_is_disabled_by_default():
    assert ServerConfig().agent_evolution.enabled is False


def test_agent_evolution_can_be_enabled_for_the_server():
    config = ServerConfig.model_validate({"agent_evolution": {"enabled": True}})

    assert config.agent_evolution.enabled is True


def test_embedded_session_service_preserves_agent_evolution_default(
    tmp_path,
    monkeypatch,
):
    config_path = tmp_path / "ov.conf"
    config_path.write_text(
        json.dumps({"server": {"agent_evolution": {"enabled": False}}}),
        encoding="utf-8",
    )
    monkeypatch.setenv(OPENVIKING_CONFIG_ENV, str(config_path))

    service = SessionService()

    assert service.get_agent_evolution_enabled() is True


def test_existing_session_observes_updated_runtime_value():
    service = SessionService(viking_fs=object())
    session = service.session(
        RequestContext(user=UserIdentifier.the_default_user(), role=Role.ROOT)
    )

    service.set_agent_evolution_config(AgentEvolutionConfig(enabled=False))

    assert session._agent_evolution_enabled_provider() is False


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


def test_agent_evolution_provider_reloads_config_file(tmp_path):
    config_path = tmp_path / "ov.conf"
    config_path.write_text(
        json.dumps({"server": {"agent_evolution": {"enabled": False}}}),
        encoding="utf-8",
    )
    provider = AgentEvolutionConfigProvider(
        default_enabled=True,
        config_path=config_path,
    )

    assert provider.is_enabled() is False

    config_path.write_text(
        json.dumps({"server": {"agent_evolution": {"enabled": True}}}),
        encoding="utf-8",
    )

    assert provider.is_enabled() is True


def test_session_service_reloads_from_resolved_server_config_path(tmp_path):
    config_path = tmp_path / "ov.conf"
    config_path.write_text(
        json.dumps({"server": {"agent_evolution": {"enabled": False}}}),
        encoding="utf-8",
    )
    service = SessionService()
    service.set_agent_evolution_config(AgentEvolutionConfig(enabled=True))
    service.set_agent_evolution_config_path(str(config_path))

    assert service.get_agent_evolution_enabled() is False


def test_http_app_wires_resolved_server_config_path(tmp_path):
    config_path = tmp_path / "ov.conf"
    config_path.write_text(
        json.dumps({"server": {"agent_evolution": {"enabled": False}}}),
        encoding="utf-8",
    )
    sessions = SessionService()

    create_app(
        config=ServerConfig(agent_evolution=AgentEvolutionConfig(enabled=True)),
        service=SimpleNamespace(sessions=sessions),
        config_path=str(config_path),
    )

    assert sessions.get_agent_evolution_enabled() is False

    config_path.write_text(
        json.dumps({"server": {"agent_evolution": {"enabled": True}}}),
        encoding="utf-8",
    )

    assert sessions.get_agent_evolution_enabled() is True


def test_agent_evolution_provider_applies_missing_section_default(tmp_path):
    config_path = tmp_path / "ov.conf"
    config_path.write_text(
        json.dumps({"server": {"agent_evolution": {"enabled": True}}}),
        encoding="utf-8",
    )
    provider = AgentEvolutionConfigProvider(
        default_enabled=False,
        config_path=config_path,
    )
    assert provider.is_enabled() is True

    config_path.write_text(json.dumps({"server": {}}), encoding="utf-8")

    assert provider.is_enabled() is False


def test_agent_evolution_provider_uses_standard_config_loading(tmp_path, monkeypatch):
    config_path = tmp_path / "ov.conf"
    config_path.write_text(
        '\ufeff{"server": {"agent_evolution": {"enabled": ${AGENT_EVOLUTION_ENABLED}}}}',
        encoding="utf-8",
    )
    monkeypatch.setenv("AGENT_EVOLUTION_ENABLED", "true")
    provider = AgentEvolutionConfigProvider(
        default_enabled=False,
        config_path=config_path,
    )

    assert provider.is_enabled() is True


def test_agent_evolution_provider_keeps_last_valid_value(tmp_path):
    config_path = tmp_path / "ov.conf"
    config_path.write_text(
        json.dumps({"server": {"agent_evolution": {"enabled": True}}}),
        encoding="utf-8",
    )
    provider = AgentEvolutionConfigProvider(
        default_enabled=False,
        config_path=config_path,
    )
    assert provider.is_enabled() is True

    config_path.write_text("{", encoding="utf-8")

    assert provider.is_enabled() is True

    config_path.write_text("[]", encoding="utf-8")

    assert provider.is_enabled() is True


def test_agent_evolution_provider_rejects_invalid_unrelated_server_config(tmp_path):
    config_path = tmp_path / "ov.conf"
    config_path.write_text(
        json.dumps({"server": {"agent_evolution": {"enabled": True}}}),
        encoding="utf-8",
    )
    provider = AgentEvolutionConfigProvider(
        default_enabled=False,
        config_path=config_path,
    )
    assert provider.is_enabled() is True

    config_path.write_text(
        json.dumps(
            {
                "server": {
                    "port": "not-an-integer",
                    "agent_evolution": {"enabled": False},
                }
            }
        ),
        encoding="utf-8",
    )

    assert provider.is_enabled() is True


def test_deprecated_user_agent_evolution_config_is_not_persisted():
    config = UserConfig.model_validate({"agent_evolution": {"enabled": True}})

    assert config.agent_evolution.enabled is True
    assert "agent_evolution" not in config.model_dump(exclude_none=True)


async def test_agent_evolution_user_endpoint_is_not_registered(
    client: httpx.AsyncClient,
):
    response = await client.get("/api/v1/user-settings/memory")

    assert response.status_code == 404


async def test_agent_evolution_admin_endpoint_returns_runtime_value(
    service,
    client: httpx.AsyncClient,
):
    service.sessions.set_agent_evolution_config(AgentEvolutionConfig(enabled=True))

    response = await client.get("/api/v1/admin/agent-evolution")

    assert response.status_code == 200, response.text
    assert response.json()["result"] == {
        "enabled": True,
        "account_id": "default",
    }


async def test_manual_extract_respects_disabled_agent_evolution(
    service,
    client: httpx.AsyncClient,
):
    create_response = await client.post("/api/v1/sessions", json={})
    session_id = create_response.json()["result"]["session_id"]
    add_response = await client.post(
        f"/api/v1/sessions/{session_id}/messages",
        json={"role": "user", "content": "请处理一次换货任务"},
    )
    assert add_response.status_code == 200, add_response.text

    extract = AsyncMock(return_value=[])
    service.sessions._session_compressor.extract_long_term_memories = extract

    response = await client.post(f"/api/v1/sessions/{session_id}/extract")

    assert response.status_code == 200, response.text
    assert extract.await_args.kwargs["agent_evolution_enabled"] is False


async def test_manual_extract_respects_enabled_agent_evolution(
    service,
    client: httpx.AsyncClient,
):
    service.sessions.set_agent_evolution_config(AgentEvolutionConfig(enabled=True))
    create_response = await client.post("/api/v1/sessions", json={})
    session_id = create_response.json()["result"]["session_id"]
    add_response = await client.post(
        f"/api/v1/sessions/{session_id}/messages",
        json={"role": "user", "content": "请处理一次换货任务"},
    )
    assert add_response.status_code == 200, add_response.text

    extract = AsyncMock(return_value=[])
    service.sessions._session_compressor.extract_long_term_memories = extract

    response = await client.post(f"/api/v1/sessions/{session_id}/extract")

    assert response.status_code == 200, response.text
    assert extract.await_args.kwargs["agent_evolution_enabled"] is True
