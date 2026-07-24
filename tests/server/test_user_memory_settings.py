# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0

from unittest.mock import AsyncMock

import httpx
import pytest

from openviking.server.config import UserConfig
from openviking.server.identity import RequestContext, Role
from openviking.server.user_config import read_user_config
from openviking_cli.exceptions import InvalidArgumentError
from openviking_cli.session.user_id import UserIdentifier


def _ctx() -> RequestContext:
    return RequestContext(user=UserIdentifier.the_default_user(), role=Role.ROOT)


async def test_memory_settings_default_disables_agent_evolution(
    client: httpx.AsyncClient,
):
    response = await client.get("/api/v1/user-settings/memory")

    assert response.status_code == 200, response.text
    result = response.json()["result"]
    assert result["override"] == {"agent_evolution_enabled": None}
    assert result["effective"] == {"agent_evolution_enabled": False}


async def test_memory_settings_patch_is_partial_and_preserves_add_targets(
    service,
    client: httpx.AsyncClient,
):
    response = await client.patch(
        "/api/v1/user-settings/add-locations",
        json={"skill_uri": "viking://user/skills"},
    )
    assert response.status_code == 200, response.text

    response = await client.patch(
        "/api/v1/user-settings/memory",
        json={"agent_evolution_enabled": True},
    )

    assert response.status_code == 200, response.text
    result = response.json()["result"]
    assert result["effective"] == {"agent_evolution_enabled": True}
    stored = await read_user_config(service.viking_fs, _ctx())
    assert stored.add_targets.skill_uri == "viking://user/skills"
    assert stored.agent_evolution.enabled is True


async def test_memory_settings_rejects_removed_memory_types_field(client: httpx.AsyncClient):
    response = await client.patch(
        "/api/v1/user-settings/memory",
        json={"memory_types": ["profile"]},
    )

    assert response.status_code == 400
    assert "memory_types" in response.text


async def test_memory_settings_null_clears_override_and_uses_server_default(
    app,
    client: httpx.AsyncClient,
):
    app.state.config.user_config_defaults = UserConfig.model_validate(
        {"agent_evolution": {"enabled": True}}
    )
    response = await client.patch(
        "/api/v1/user-settings/memory",
        json={"agent_evolution_enabled": False},
    )
    assert response.status_code == 200, response.text

    response = await client.patch(
        "/api/v1/user-settings/memory",
        json={"agent_evolution_enabled": None},
    )

    assert response.status_code == 200, response.text
    result = response.json()["result"]
    assert result["override"] == {"agent_evolution_enabled": None}
    assert result["effective"] == {"agent_evolution_enabled": True}


async def test_delete_add_locations_preserves_memory_settings(
    service,
    client: httpx.AsyncClient,
):
    assert (
        await client.patch(
            "/api/v1/user-settings/add-locations",
            json={"skill_uri": "viking://user/skills"},
        )
    ).status_code == 200
    assert (
        await client.patch(
            "/api/v1/user-settings/memory",
            json={"agent_evolution_enabled": True},
        )
    ).status_code == 200

    response = await client.delete("/api/v1/user-settings/add-locations")

    assert response.status_code == 200, response.text
    stored = await read_user_config(service.viking_fs, _ctx())
    assert stored.add_targets.skill_uri is None
    assert stored.agent_evolution.enabled is True


async def test_embedded_memory_settings_reject_non_boolean_values(service):
    with pytest.raises(InvalidArgumentError, match="boolean or null"):
        await service.sessions.patch_memory_settings(
            _ctx(),
            agent_evolution_enabled="not-a-bool",
        )

    stored = await read_user_config(service.viking_fs, _ctx())
    assert stored.agent_evolution.enabled is None


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
