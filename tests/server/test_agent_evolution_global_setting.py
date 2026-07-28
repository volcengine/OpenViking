# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0

from unittest.mock import AsyncMock

import httpx

from openviking.server.config import AgentEvolutionConfig, ServerConfig, UserConfig
from openviking.service.session_service import SessionService


def test_agent_evolution_is_disabled_by_default():
    assert ServerConfig().agent_evolution.enabled is False


def test_agent_evolution_can_be_enabled_for_the_server():
    config = ServerConfig.model_validate({"agent_evolution": {"enabled": True}})

    assert config.agent_evolution.enabled is True


def test_embedded_session_service_preserves_agent_evolution_default():
    service = SessionService()

    assert service._agent_evolution_enabled is True


def test_deprecated_user_agent_evolution_config_is_not_persisted():
    config = UserConfig.model_validate({"agent_evolution": {"enabled": True}})

    assert config.agent_evolution.enabled is True
    assert "agent_evolution" not in config.model_dump(exclude_none=True)


async def test_agent_evolution_user_endpoint_is_not_registered(
    client: httpx.AsyncClient,
):
    response = await client.get("/api/v1/user-settings/memory")

    assert response.status_code == 404


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
