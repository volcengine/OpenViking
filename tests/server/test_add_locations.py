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


@pytest.fixture
def user_role_client(app, client: httpx.AsyncClient):
    """Client whose requests carry a USER-role identity.

    The dev auth plugin resolves every request to ROOT, which deliberately skips
    current-user alias resolution; add-location targets are a user-facing feature,
    so these tests exercise them as a real user.
    """
    from openviking.server.auth import get_request_context
    from openviking.server.identity import RequestContext, Role
    from openviking_cli.session.user_id import UserIdentifier

    app.dependency_overrides[get_request_context] = lambda: RequestContext(
        user=UserIdentifier("default", "default"),
        role=Role.USER,
    )
    yield client
    app.dependency_overrides.pop(get_request_context, None)


async def _add_resource(client: httpx.AsyncClient, filename: str, **extra):
    payload = {"temp_file_id": filename, "wait": True, **extra}
    response = await client.post("/api/v1/resources", json=payload)
    assert response.status_code == 200, response.text
    return response.json()["result"]["root_uri"]


async def test_add_locations_resource_server_default_and_precedence(
    app,
    service,
    user_role_client: httpx.AsyncClient,
    sample_markdown_file,
    upload_temp_dir,
):
    client = user_role_client
    app.state.config.user_config_defaults = UserConfig(
        add_targets=AddTargetsConfig(resource_uri="viking://~/resources")
    )
    assert (
        app.state.config.user_config_defaults.add_targets.resource_uri == "viking://~/resources"
    )

    root_uri = await _add_resource(client, sample_markdown_file.name)
    assert root_uri.startswith("viking://user/default/resources/")

    response = await client.patch(
        "/api/v1/user-settings/add-locations",
        json={"resource_uri": "viking://~/resources/project-a"},
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

    from openviking.server.identity import RequestContext, Role
    from openviking.storage.acl import AclMode
    from openviking_cli.session.user_id import UserIdentifier

    restricted_uri = "viking://resources/one-off/sample"
    await service.runtime_config_manager.patch_account("default", {"acl": {"enabled": True}})
    await service.viking_fs.set_acl(
        restricted_uri,
        [{"principal": "user:editor", "level": "write"}],
        acl_mode=AclMode.RESTRICTED,
        ctx=RequestContext(user=UserIdentifier("default", "admin"), role=Role.ADMIN),
    )
    response = await client.patch(
        "/api/v1/user-settings/add-locations", json={"resource_uri": restricted_uri}
    )
    assert response.status_code == 200, response.text
    loaded = await client.get("/api/v1/user-settings/add-locations")
    assert loaded.status_code == 200, loaded.text
    assert loaded.json()["result"]["effective"]["resource_uri"] == restricted_uri
    denied = await client.post(
        "/api/v1/resources", json={"temp_file_id": sample_markdown_file.name, "wait": True}
    )
    assert denied.status_code == 403, denied.text
    app.state.config.user_config_defaults.add_targets.resource_uri = restricted_uri
    cleared = await client.delete("/api/v1/user-settings/add-locations")
    assert cleared.status_code == 200, cleared.text
    assert cleared.json()["result"]["effective"]["resource_uri"] == restricted_uri


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


async def test_add_locations_patch_accepts_home_alias_uris(
    app,
    user_role_client: httpx.AsyncClient,
    sample_markdown_file,
    upload_temp_dir,
):
    client = user_role_client
    response = await client.patch(
        "/api/v1/user-settings/add-locations",
        json={"resource_uri": "viking://~/resources/project-a", "skill_uri": "viking://~/skills"},
    )
    assert response.status_code == 200, response.text
    effective = response.json()["result"]["effective"]
    assert effective["resource_uri"] == "viking://user/default/resources/project-a"
    assert effective["skill_uri"] == "viking://user/default/skills"

    root_uri = await _add_resource(client, sample_markdown_file.name)
    assert root_uri.startswith("viking://user/default/resources/project-a/")


async def test_add_locations_patch_rejects_bad_uris(client: httpx.AsyncClient):
    response = await client.patch(
        "/api/v1/user-settings/add-locations",
        json={"skill_uri": "viking://user/skills/nested"},
    )
    assert response.status_code == 400
    body = response.json()
    assert body["status"] == "error"
    assert body["error"]["code"] == "INVALID_ARGUMENT"
    assert "skill_uri must be viking://~/skills" in body["error"]["message"]

    response = await client.patch(
        "/api/v1/user-settings/add-locations",
        json={"resource_uri": "viking://user/skills"},
    )
    assert response.status_code == 400
    body = response.json()
    assert body["error"]["code"] == "INVALID_ARGUMENT"
    assert "resource_uri must be a resource directory URI" in body["error"]["message"]


@pytest.mark.parametrize(
    "uri",
    [
        "viking://~/resources",
        "viking://~/resources/project-a",
        "viking://resources/shared",
        "viking://~/peers/work/resources/project-a",
    ],
)
def test_add_targets_accepts_supported_resource_uris(uri):
    assert AddTargetsConfig(resource_uri=uri).resource_uri == uri


@pytest.mark.parametrize(
    "uri",
    ["viking://~/skills", "viking://agent/skills"],
)
def test_add_targets_accepts_supported_skill_uris(uri):
    assert AddTargetsConfig(skill_uri=uri).skill_uri == uri


@pytest.mark.parametrize(
    "uri",
    [
        "viking://user/skills",
        "viking://user/memories",
        "viking://~/memories",
        "viking://agent/skills",
        "viking://user/alice/resources/project-a",
        "viking://user/alice/peers/work/resources",
    ],
)
def test_add_targets_rejects_invalid_resource_uris(uri):
    with pytest.raises(ValueError, match="resource_uri must be a resource directory URI"):
        AddTargetsConfig(resource_uri=uri)


@pytest.mark.parametrize(
    "uri",
    [
        "viking://user/skills/nested",
        "viking://user/resources",
        "viking://~/skills/nested",
        "viking://resources/skills",
        "viking://user/alice/skills",
    ],
)
def test_add_targets_rejects_invalid_skill_uris(uri):
    with pytest.raises(ValueError, match="skill_uri must be viking://~/skills"):
        AddTargetsConfig(skill_uri=uri)


def test_load_server_config_add_locations(tmp_path):
    config_path = tmp_path / "ov.conf"
    config_path.write_text(
        json.dumps(
            {
                "server": {
                    "user_config_defaults": {
                        "add_targets": {
                            "resource_uri": "viking://user/resources",
                            "skill_uri": "viking://user/skills",
                        }
                    }
                }
            }
        ),
        encoding="utf-8",
    )

    config = load_server_config(str(config_path))
    assert config.user_config_defaults.add_targets.model_dump() == {
        "resource_uri": "viking://~/resources",
        "skill_uri": "viking://~/skills",
    }

    for field, uri in [
        ("resource_uri", "viking://user/skills"),
        ("resource_uri", "viking://user/alice/resources"),
        ("skill_uri", "viking://user/alice/skills"),
    ]:
        config_path.write_text(
            json.dumps({"server": {"user_config_defaults": {"add_targets": {field: uri}}}}),
            encoding="utf-8",
        )
        with pytest.raises(ValueError, match=field):
            load_server_config(str(config_path))
