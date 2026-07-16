# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0

import httpx

from openviking.server.config import UserConfig
from openviking.server.identity import RequestContext, Role
from openviking.server.user_config import read_user_config
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
