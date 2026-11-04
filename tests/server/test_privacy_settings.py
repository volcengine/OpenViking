# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0

import asyncio
import json

import httpx
import pytest

from openviking.server.auth import get_request_context
from openviking.server.config import PrivacyConfig, UserConfig, load_server_config
from openviking.server.identity import RequestContext, Role
from openviking.server.user_config import (
    patch_user_config,
    read_user_config,
    read_user_privacy,
    user_config_backup_uri,
    user_config_uri,
)
from openviking_cli.session.user_id import UserIdentifier
from openviking_cli.utils.config import OPENVIKING_CONFIG_ENV
from openviking_cli.utils.config.open_viking_config import OpenVikingConfigSingleton

URL = "/api/v1/user-settings/privacy"


@pytest.fixture(autouse=True)
def isolated_config(monkeypatch, tmp_path):
    config_path = tmp_path / "ov.conf"
    config_path.write_text(
        json.dumps(
            {
                "storage": {"workspace": str(tmp_path / "data")},
                "embedding": {
                    "dense": {
                        "provider": "openai",
                        "model": "test",
                        "api_key": "test-only-key",
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
    yield config_path
    OpenVikingConfigSingleton.reset_instance()


@pytest.fixture
def owner(app):
    ctx = RequestContext(user=UserIdentifier("default", "alice"), role=Role.USER)
    app.dependency_overrides[get_request_context] = lambda: ctx
    yield ctx
    app.dependency_overrides.pop(get_request_context, None)


async def test_privacy_settings_inherit_patch_and_reset(
    client: httpx.AsyncClient, app, service, owner
):
    response = await client.get(URL)
    assert response.status_code == 200, response.text
    assert response.json()["result"] == {
        "override": {},
        "effective": {"mode": "off", "restore": True},
    }

    app.state.config.user_config_defaults.privacy = PrivacyConfig(mode="auto", restore=False)
    for patch, override, effective in [
        ({"mode": "registered"}, {"mode": "registered"}, {"mode": "registered", "restore": False}),
        (
            {"restore": True},
            {"mode": "registered", "restore": True},
            {"mode": "registered", "restore": True},
        ),
        ({"mode": None}, {"restore": True}, {"mode": "auto", "restore": True}),
        ({}, {"restore": True}, {"mode": "auto", "restore": True}),
    ]:
        response = await client.patch(URL, json=patch)
        assert response.status_code == 200, response.text
        assert response.json()["result"] == {"override": override, "effective": effective}
        persisted = json.loads(await service.viking_fs.read_file(user_config_uri(owner), ctx=owner))
        assert persisted["privacy"] == override

    response = await client.delete(URL)
    assert response.status_code == 200, response.text
    assert response.json()["result"] == {
        "override": {},
        "effective": {"mode": "auto", "restore": False},
    }


async def test_privacy_patch_rejects_invalid_input_before_persistence(
    client: httpx.AsyncClient, service, owner
):
    for patch in ({"mode": "enabled"}, {"restore": "false"}, {"unknown": True}):
        response = await client.patch(URL, json=patch)
        assert response.status_code == 400, response.text
    assert not await service.viking_fs.exists(user_config_uri(owner), ctx=owner)


async def test_privacy_patches_are_locked_and_owner_scoped(
    client: httpx.AsyncClient, app, service, owner
):
    patches = [{"mode": "registered"}, {"restore": False}]
    responses = await asyncio.gather(*(client.patch(URL, json=patch) for patch in patches))
    for patch, response in zip(patches, responses, strict=True):
        assert response.status_code in (200, 409), response.text
        if response.status_code == 409:
            response = await client.patch(URL, json=patch)
            assert response.status_code == 200, response.text
    expected = {"mode": "registered", "restore": False}
    assert (await read_user_privacy(service.viking_fs, owner)).model_dump() == expected

    other = RequestContext(user=UserIdentifier("default", "bob"), role=Role.USER)
    app.dependency_overrides[get_request_context] = lambda: other
    response = await client.get(URL)
    assert response.json()["result"]["override"] == {}
    await client.delete(URL)
    assert (await read_user_privacy(service.viking_fs, owner)).model_dump() == expected


async def test_privacy_patch_rolls_back_failed_write(service, owner, monkeypatch):
    fs = service.viking_fs
    await patch_user_config(fs, owner, {"privacy": {"mode": "registered"}})
    uri = user_config_uri(owner)
    before = await read_user_config(fs, owner)
    original_write = fs.write_file
    failed = False

    async def fail_once(target, content, **kwargs):
        nonlocal failed
        if target == uri and not failed:
            failed = True
            raise OSError("injected write failure")
        return await original_write(target, content, **kwargs)

    monkeypatch.setattr(fs, "write_file", fail_once)
    with pytest.raises(OSError, match="injected write failure"):
        await patch_user_config(fs, owner, {"privacy": {"restore": False}})
    assert await read_user_config(fs, owner) == before
    backup = json.loads(await fs.read_file(user_config_backup_uri(owner), ctx=owner))
    assert UserConfig.model_validate(backup) == before


async def test_settings_and_backups_preserve_registered_literals(
    client: httpx.AsyncClient, service, owner
):
    await service.privacy_configs.upsert(
        owner, "credential", "settings-test", {"key": "registered"}
    )
    for patch in ({"mode": "registered"}, {"restore": False}):
        response = await client.patch(URL, json=patch)
        assert response.status_code == 200, response.text
    for uri in (user_config_uri(owner), user_config_backup_uri(owner)):
        stored = json.loads(await service.viking_fs.read_file(uri, ctx=owner))
        assert stored["privacy"]["mode"] == "registered"


def test_privacy_deployment_defaults_load(isolated_config):
    payload = json.loads(isolated_config.read_text(encoding="utf-8"))
    payload["server"] = {
        "user_config_defaults": {"privacy": {"mode": "registered", "restore": False}}
    }
    isolated_config.write_text(json.dumps(payload), encoding="utf-8")
    config = load_server_config(str(isolated_config))
    assert config.user_config_defaults.privacy == PrivacyConfig(mode="registered", restore=False)
