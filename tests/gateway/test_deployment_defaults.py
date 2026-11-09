"""Deployment defaults route OpenViking keys without account configuration."""

import json

import pytest
from pydantic import ValidationError

from openviking_gateway.app import seed_defaults
from openviking_gateway.cli import load_config
from openviking_gateway.config import GatewayConfig
from openviking_gateway.records import RecordKind as K
from openviking_gateway.storage import digest

HEADERS = {"Authorization": "Bearer other", "X-OpenViking-Session": "deployment"}
BODY = {"model": "deployed", "messages": [{"role": "user", "content": "Deploy the cluster?"}]}
CHAT = "/v1/chat/completions"


@pytest.fixture
def gateway_config(gateway_backend):
    return {
        "openviking_keys": True,
        "default_upstreams": {
            "deployment-chat": {
                "name": "Deployment chat",
                "protocol": "chat",
                "base_url": gateway_backend[0] + "/v1",
                "api_key": "deployment-secret",
                "models": ["deployed"],
            }
        },
        "default_profile": {
            "name": "Deployment profile",
            "recall": False,
            "profile": False,
            "capture": False,
            "gateway_tools": False,
            "max_tokens": 777,
        },
    }


async def test_empty_account_routes_and_freezes_the_deployment_upstream(running_gateway):
    app, client, _, _, captured, _ = running_gateway
    response = await client.get("/v1/models", headers=HEADERS)
    assert response.status_code == 200, response.text
    assert [item["id"] for item in response.json()["data"]] == ["deployed"]
    response = await client.post(CHAT, headers=HEADERS, json=BODY)
    assert response.status_code == 200, response.text
    assert captured[-1][2]["Authorization"] == "Bearer deployment-secret"
    records = await app.state.store.replay.read(
        digest("other\0alice\0chat"), digest("deployment"), [""]
    )
    assert records[K.ROOT, ""]["upstream_id"] == "deployment-chat"
    assert records[K.ROOT, ""]["policy"]["max_tokens"] == 777
    assert await app.state.management.list("other", "upstreams") == []
    assert await app.state.management.get("other", "policies", "default") is None


@pytest.mark.parametrize("enabled", [True, False])
async def test_stored_upstreams_replace_all_deployment_upstreams(running_gateway, enabled):
    app, client, _, _, _, _ = running_gateway
    upstream = app.state.config.default_upstreams["deployment-chat"].model_dump()
    await app.state.management.save(
        "other", "upstreams", "stored", {**upstream, "models": ["stored-model"], "enabled": enabled}
    )
    response = await client.get("/v1/models", headers=HEADERS)
    assert response.status_code == 200, response.text
    assert [item["id"] for item in response.json()["data"]] == (["stored-model"] if enabled else [])
    response = await client.post(CHAT, headers=HEADERS, json=BODY)
    assert response.status_code == 404
    response = await client.post(CHAT, headers=HEADERS, json={**BODY, "model": "stored-model"})
    assert response.status_code == (200 if enabled else 404), response.text


@pytest.mark.parametrize("stored", [False, True])
async def test_default_profile_precedence_and_client_overrides(running_gateway, stored):
    app, client, _, _, _, _ = running_gateway
    calls = []

    async def handler(request):
        calls.append(request.path)

    app.state.test_backend["handler"] = handler
    if stored:
        await app.state.management.save(
            "other",
            "policies",
            "default",
            {**app.state.config.default_profile.model_dump(), "recall": True, "max_tokens": 888},
        )
    response = await client.post(CHAT, headers=HEADERS, json=BODY)
    assert response.status_code == 200, response.text
    assert ("/api/v1/search/search" in calls) == stored
    calls.clear()
    response = await client.post(
        CHAT,
        headers={
            **HEADERS,
            "X-OpenViking-Session": "override",
            "X-OpenViking-Context": json.dumps({"recall": not stored}),
        },
        json=BODY,
    )
    assert response.status_code == 200, response.text
    assert ("/api/v1/search/search" in calls) != stored
    records = await app.state.store.replay.read(
        digest("other\0alice\0chat"), digest("override"), [""]
    )
    assert records[K.ROOT, ""]["policy"]["max_tokens"] == (888 if stored else 777)


async def test_seed_uses_deployment_profile_and_preserves_saved_changes(running_gateway):
    app = running_gateway[0]
    await seed_defaults(app, "fresh")
    saved = await app.state.management.get("fresh", "policies", "default")
    assert saved["name"] == "Deployment profile" and saved["max_tokens"] == 777
    await app.state.management.save("fresh", "policies", "default", {**saved, "max_tokens": 999})
    await seed_defaults(app, "fresh")
    assert (await app.state.management.get("fresh", "policies", "default"))["max_tokens"] == 999


@pytest.mark.parametrize(
    "settings",
    [
        {"default_upstreams": {"chat": {"protocol": "chat", "base_url": "https://model.test"}}},
        {
            "default_upstreams": {
                "chat": {
                    "name": "Chat",
                    "protocol": "chat",
                    "base_url": "https://model.test",
                    "id": "chat",
                }
            }
        },
        {"default_profile": {"unknown_setting": True}},
    ],
)
def test_invalid_defaults_fail_when_loading_config(tmp_path, monkeypatch, settings):
    path = tmp_path / "gateway.conf"
    path.write_text(json.dumps({"openviking_url": "https://ov.test", **settings}))
    monkeypatch.setenv("OPENVIKING_GATEWAY_CONFIG_FILE", str(path))
    with pytest.raises(ValidationError):
        load_config()


def test_json_environment_overrides_and_file_secret_expansion(tmp_path, monkeypatch):
    path = tmp_path / "gateway.conf"
    upstream = {"name": "Chat", "protocol": "chat", "base_url": "https://model.test/v1"}
    path.write_text(
        json.dumps(
            {
                "openviking_url": "https://ov.test",
                "default_upstreams": {"chat": {**upstream, "api_key": "$MODEL_API_KEY"}},
                "default_profile": {"max_tokens": 800},
            }
        )
    )
    monkeypatch.setenv("OPENVIKING_GATEWAY_CONFIG_FILE", str(path))
    monkeypatch.setenv("MODEL_API_KEY", "secret-from-environment")
    assert load_config().default_upstreams["chat"].api_key == "secret-from-environment"
    monkeypatch.setenv(
        "OPENVIKING_GATEWAY_DEFAULT_UPSTREAMS", json.dumps({"replacement": upstream})
    )
    monkeypatch.setenv("OPENVIKING_GATEWAY_DEFAULT_PROFILE", '{"max_tokens":900}')
    monkeypatch.setenv("OPENVIKING_GATEWAY_CLIENT_RULES", '{"plugin_tags":{"add":["example"]}}')
    config = load_config()
    assert list(config.default_upstreams) == ["replacement"]
    assert config.default_profile.max_tokens == 900
    assert config.client_rules.plugin_tags.add == ["example"]
    assert "default_upstreams" not in repr(config)


def test_defaults_preserve_existing_configuration():
    config = GatewayConfig()
    assert config.default_upstreams == {}
    assert config.default_profile.name == "Default"
