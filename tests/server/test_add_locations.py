# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0

import json

import httpx
import pytest

from openviking.server.config import AddTargetsConfig, UserConfig, load_server_config
from openviking_cli.utils.config import OPENVIKING_CONFIG_ENV
from openviking_cli.utils.config.open_viking_config import OpenVikingConfigSingleton


@pytest.fixture(autouse=True)
def _configure_test_env(monkeypatch, tmp_path):
    config_path = tmp_path / "ov.conf"
    config_path.write_text(
        json.dumps(
            {
                "storage": {
                    "workspace": str(tmp_path / "workspace"),
                    "agfs": {"backend": "local"},
                    "vectordb": {"backend": "local"},
                },
                "embedding": {
                    "dense": {
                        "provider": "openai",
                        "model": "test-embedder",
                        "api_base": "http://127.0.0.1:11434/v1",
                        "dimension": 1024,
                    }
                },
                "encryption": {"enabled": False},
            }
        ),
        encoding="utf-8",
    )
    monkeypatch.setenv(OPENVIKING_CONFIG_ENV, str(config_path))
    OpenVikingConfigSingleton.reset_instance()
    yield
    OpenVikingConfigSingleton.reset_instance()


async def _add_resource(client: httpx.AsyncClient, filename: str, **extra):
    payload = {"temp_file_id": filename, "wait": True, **extra}
    response = await client.post("/api/v1/resources", json=payload)
    assert response.status_code == 200, response.text
    return response.json()["result"]["root_uri"]


async def test_add_locations_resource_server_default_and_precedence(
    app,
    client: httpx.AsyncClient,
    sample_markdown_file,
    upload_temp_dir,
):
    app.state.config.user_config_defaults = UserConfig(
        add_targets=AddTargetsConfig(resource_uri="viking://user/resources")
    )

    root_uri = await _add_resource(client, sample_markdown_file.name)
    assert root_uri.startswith("viking://user/default/resources/")

    response = await client.patch(
        "/api/v1/user-settings/add-locations",
        json={"resource_uri": "viking://user/resources/project-a"},
    )
    assert response.status_code == 200, response.text
    body = response.json()["result"]
    assert body["effective"]["resource_uri"] == "viking://user/default/resources/project-a"

    root_uri = await _add_resource(client, sample_markdown_file.name)
    assert root_uri.startswith("viking://user/default/resources/project-a/")

    root_uri = await _add_resource(
        client,
        sample_markdown_file.name,
        to="viking://resources/one-off/sample",
    )
    assert root_uri == "viking://resources/one-off/sample"

    response = await client.delete("/api/v1/user-settings/add-locations")
    assert response.status_code == 200, response.text
    root_uri = await _add_resource(client, sample_markdown_file.name)
    assert root_uri.startswith("viking://user/default/resources/")
    assert "/project-a/" not in root_uri


async def test_add_locations_skill_server_default(
    app,
    client: httpx.AsyncClient,
):
    app.state.config.user_config_defaults = UserConfig(
        add_targets=AddTargetsConfig(skill_uri="viking://agent/skills")
    )

    response = await client.post(
        "/api/v1/skills",
        json={
            "data": {
                "name": "default-agent-skill",
                "description": "Skill default target test",
                "content": "# Default Agent Skill",
            },
            "wait": True,
        },
    )
    assert response.status_code == 200, response.text
    assert response.json()["result"]["root_uri"] == "viking://agent/skills/default-agent-skill"


async def test_add_locations_patch_rejects_bad_uris(client: httpx.AsyncClient):
    response = await client.patch(
        "/api/v1/user-settings/add-locations",
        json={"skill_uri": "viking://user/skills/nested"},
    )
    assert response.status_code == 400
    body = response.json()
    assert body["status"] == "error"
    assert body["error"]["code"] == "INVALID_ARGUMENT"

    response = await client.patch(
        "/api/v1/user-settings/add-locations",
        json={"resource_uri": "viking://user/skills"},
    )
    assert response.status_code == 400
    assert response.json()["error"]["code"] == "INVALID_ARGUMENT"


def test_load_server_config_rejects_bad_add_locations(tmp_path):
    config_path = tmp_path / "ov.conf"
    config_path.write_text(
        json.dumps(
            {
                "server": {
                    "user_config_defaults": {
                        "add_targets": {"resource_uri": "viking://user/skills"}
                    }
                }
            }
        ),
        encoding="utf-8",
    )

    with pytest.raises(ValueError, match="resource_uri"):
        load_server_config(str(config_path))
