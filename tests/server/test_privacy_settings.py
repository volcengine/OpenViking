# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0

import asyncio
import json

import httpx
import pytest

from openviking.core.namespace import canonical_user_root
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
        "effective": {"mode": "off", "restore": True, "custom_rules": [], "never_mask": []},
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
        (
            {"mode": "auto_strict"},
            {"mode": "auto_strict", "restore": True},
            {"mode": "auto_strict", "restore": True},
        ),
    ]:
        response = await client.patch(URL, json=patch)
        assert response.status_code == 200, response.text
        assert response.json()["result"] == {
            "override": override,
            "effective": {**effective, "custom_rules": [], "never_mask": []},
        }
        persisted = json.loads(await service.viking_fs.read_file(user_config_uri(owner), ctx=owner))
        assert persisted["privacy"] == override

    response = await client.delete(URL)
    assert response.status_code == 200, response.text
    assert response.json()["result"] == {
        "override": {},
        "effective": {"mode": "auto", "restore": False, "custom_rules": [], "never_mask": []},
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
    assert (await read_user_privacy(service.viking_fs, owner)).model_dump(
        exclude_none=True
    ) == expected

    other = RequestContext(user=UserIdentifier("default", "bob"), role=Role.USER)
    app.dependency_overrides[get_request_context] = lambda: other
    response = await client.get(URL)
    assert response.json()["result"]["override"] == {}
    await client.delete(URL)
    assert (await read_user_privacy(service.viking_fs, owner)).model_dump(
        exclude_none=True
    ) == expected


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
        owner, "credential", "settings-test", {"key": "auto_strict"}
    )
    for patch in ({"mode": "auto_strict"}, {"restore": False}):
        response = await client.patch(URL, json=patch)
        assert response.status_code == 200, response.text
    for uri in (user_config_uri(owner), user_config_backup_uri(owner)):
        stored = json.loads(await service.viking_fs.read_file(uri, ctx=owner))
        assert stored["privacy"]["mode"] == "auto_strict"


def test_privacy_deployment_defaults_load(isolated_config):
    payload = json.loads(isolated_config.read_text(encoding="utf-8"))
    payload["server"] = {
        "user_config_defaults": {"privacy": {"mode": "auto_strict", "restore": False}}
    }
    isolated_config.write_text(json.dumps(payload), encoding="utf-8")
    config = load_server_config(str(isolated_config))
    assert config.user_config_defaults.privacy == PrivacyConfig(mode="auto_strict", restore=False)


async def test_custom_rules_inherit_replace_and_validate_before_writing(
    client, app, service, owner
):
    defaults = [{"id": "company", "prefix": "company_", "min_length": 12}]
    app.state.config.user_config_defaults.privacy = PrivacyConfig(
        custom_rules=defaults, never_mask=["public-value"]
    )
    response = await client.get(URL)
    assert response.json()["result"]["effective"]["custom_rules"] == defaults
    response = await client.patch(URL, json={"custom_rules": [], "never_mask": []})
    assert response.status_code == 200, response.text
    assert response.json()["result"]["effective"]["custom_rules"] == []
    before = await service.viking_fs.read_file(user_config_uri(owner), ctx=owner)
    invalid_rules = [
        [{"id": "x", "regex": "("}],
        [{"id": "x", "regex": "a*"}],
        [{"id": "x", "regex": "(?=secret)"}],
        [{"id": "x", "prefix": "acme_"}],
        [{"id": "x", "prefix": "acme_", "min_length": True}],
        [{"id": "x", "prefix": "acme_", "min_length": 0}],
        [{"id": "x", "prefix": "acme_", "min_length": 12, "regex": "secret"}],
        [{"id": "x", "keys": ["password"]}],
        [{"id": "x", "regex": "a"}, {"id": "x", "regex": "b"}],
        [{"id": "x", "regex": "[a-zA-Z0-9]{1000000000}"}],
    ]
    for rules in invalid_rules:
        response = await client.patch(URL, json={"custom_rules": rules})
        assert response.status_code == 400, response.text
        assert await service.viking_fs.read_file(user_config_uri(owner), ctx=owner) == before
    response = await client.patch(URL, json={"custom_rules": None, "never_mask": None})
    assert response.status_code == 200, response.text
    assert response.json()["result"]["override"] == {}
    assert response.json()["result"]["effective"]["custom_rules"] == defaults
    assert response.json()["result"]["effective"]["never_mask"] == ["public-value"]


async def test_custom_rules_and_never_mask_apply_to_skill_session_and_registered_values(
    client, app, service, owner
):
    from openviking.message import TextPart
    from openviking.privacy.reference import build_privacy_field_reference

    value = "company_aB3dE5gH7jK9mN2pQ4sT"
    excluded = "company_xY7zA9bC2dE4fG6hJ8kL"
    rules = [{"id": "company", "prefix": "company_", "min_length": 20}]
    response = await client.patch(
        URL,
        json={"mode": "auto", "custom_rules": rules, "never_mask": [excluded, "managed-secret"]},
    )
    assert response.status_code == 200, response.text
    await service.privacy_configs.upsert(owner, "credential", "managed", {"key": "managed-secret"})
    original = f"正文🙂 `{value}` {excluded} managed-secret; password=sample"
    session = await service.sessions.create(owner, "custom-discovery")
    await session.add_message_async("user", [TextPart(original)])
    current = await service.privacy_configs.get_current(owner, "session", session.session_id)
    assert list(current.values.values()) == [value]
    stored = await service.viking_fs.read_file(session.uri + "/messages.jsonl", ctx=owner)
    assert value not in stored and excluded in stored and "managed-secret" in stored
    assert (
        original
        in json.loads(await service.fs.read(session.uri + "/messages.jsonl", ctx=owner))["parts"][
            0
        ]["text"]
    )
    await service.resources.add_skill(
        data={
            "name": "custom-rules",
            "description": "custom detector",
            "content": f"`{value}` {excluded}",
        },
        ctx=owner,
        wait=False,
    )
    current = await service.privacy_configs.get_current(owner, "skill", "custom-rules")
    assert list(current.values.values()) == [value]
    reference = build_privacy_field_reference(owner.user.user_id, "credential", "managed", "key")
    await session.add_message_async("user", [TextPart(reference)])
    restored = await service.fs.read(session.uri + "/messages.jsonl", ctx=owner)
    assert json.loads(restored.splitlines()[1])["parts"][0]["text"] == "managed-secret"


async def test_overlapping_rules_count_session_discoveries_once(client, service, owner):
    from openviking.message import TextPart

    rules = [{"id": f"rule{i}", "prefix": "corp_", "min_length": 20} for i in range(64)]
    response = await client.patch(URL, json={"mode": "auto", "custom_rules": rules})
    assert response.status_code == 200, response.text
    value = "corp_A7b9C2d4E6f8G0h1J3k5"
    original = (value + " ") * 313
    session = await service.sessions.create(owner, "overlapping-rules")
    await session.add_message_async("user", [TextPart(original)])
    current = await service.privacy_configs.get_current(owner, "session", session.session_id)
    assert list(current.values.values()) == [value]
    stored = await service.viking_fs.read_file(session.uri + "/messages.jsonl", ctx=owner)
    assert value not in stored
    restored = await service.fs.read(session.uri + "/messages.jsonl", ctx=owner)
    assert json.loads(restored)["parts"][0]["text"] == original


@pytest.mark.parametrize("mode", ["off", "registered", "auto", "auto_strict"])
@pytest.mark.parametrize("restore", [False, True])
async def test_privacy_policy_applies_across_session_skill_and_content(
    client, service, owner, mode, restore
):
    from openviking.message import TextPart
    from openviking.privacy.reference import build_privacy_field_reference

    response = await client.patch(URL, json={"mode": mode, "restore": restore})
    assert response.status_code == 200, response.text
    known = "managed-secret"
    secret = "AKLT" + "aB3dE5gH7jK9mN2pQ4sT6vW8yZ0cF1rU"
    reference = build_privacy_field_reference(owner.user.user_id, "credential", "managed", "key")
    await service.privacy_configs.upsert(owner, "credential", "managed", {"key": known})
    body = f"known={known}\nnew={secret}\nexplicit={reference}"
    root = canonical_user_root(owner)
    resource = root + "/memories/preferences/policy.md"
    await service.viking_fs.mkdir(root + "/memories/preferences", ctx=owner)
    await service.viking_fs.write_file(resource, body, ctx=owner)
    resource_body = body.replace(known, reference) if mode == "auto_strict" else body
    assert await service.fs.read_visible(resource, ctx=owner) == resource_body
    assert resource_body in await service.fs.read(resource, ctx=owner)
    session = await service.sessions.create(owner, "policy-matrix")
    await session.add_message_async("user", [TextPart(body)])
    await service.resources.add_skill(
        data={"name": "policy-matrix", "description": "Policy test", "content": body},
        ctx=owner,
        wait=False,
    )
    for uri, session_file in [
        (session.uri + "/messages.jsonl", True),
        (root + "/skills/policy-matrix/SKILL.md", False),
    ]:
        stored = await service.viking_fs.read_file(uri, ctx=owner)
        if session_file:
            stored = json.loads(stored)["parts"][0]["text"]
        assert (known not in stored) == (mode != "off")
        discovered = mode in {"auto", "auto_strict"}
        assert (secret not in stored) == discovered
        visible = await service.fs.read_visible(uri, ctx=owner)
        assert reference in visible
        raw = await service.fs.read(uri, ctx=owner)
        if session_file:
            raw = json.loads(raw)["parts"][0]["text"]
        assert (reference not in raw) == restore
        assert (secret in raw) == (restore or not discovered)


async def test_credential_warnings_do_not_block_or_disclose_values(client, service, owner):
    secret = "AKLT" + "aB3dE5gH7jK9mN2pQ4sT6vW8yZ0cF1rU"
    endpoint = "/api/v1/privacy-configs/credential/warnings"
    response = await client.post(endpoint, json={"values": {"ordinary": "sample", "key": secret}})
    assert response.status_code == 200, response.text
    result = response.json()["result"]
    assert result["values"] == {"ordinary": "sample", "key": secret}
    assert [(w["code"], w["field"]) for w in result["warnings"]] == [
        ("weak_credential", "ordinary")
    ]
    assert all(value not in json.dumps(result["warnings"]) for value in [secret, "sample"])
    response = await client.patch(endpoint, json={"values": {"ordinary": secret}})
    assert response.status_code == 200 and "warnings" not in response.json()["result"]
