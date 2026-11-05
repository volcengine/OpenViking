"""OpenViking API keys used directly as gateway keys (``openviking_keys``)."""

import pytest
from conftest import make_due

from openviking_gateway.models import Policy
from openviking_gateway.proxy import resolve_binding
from openviking_gateway.storage import digest

OV_KEY = "bob-key"
RECORD = digest("openviking\0" + OV_KEY)


def bearer(key):
    return {"Authorization": "Bearer " + key}


def record_openviking_calls(app):
    """Collect the key of every OpenViking call; the fake backend still answers them."""
    calls = []

    async def handler(request):
        if request.path.startswith(("/health", "/api/", "/mcp")):
            calls.append((request.path, request.headers.get("X-API-Key")))
        return None

    app.state.test_backend["handler"] = handler
    return calls


@pytest.fixture
def gateway_config(request):
    return getattr(request, "param", {"openviking_keys": True})


@pytest.mark.parametrize("gateway_config", [{"openviking_keys": False}], indirect=True)
async def test_switch_off_rejects_openviking_keys_without_calling_openviking(running_gateway):
    app, client, _, _, _, _ = running_gateway
    calls = record_openviking_calls(app)
    for headers in (bearer(OV_KEY), {"x-api-key": OV_KEY}):
        assert (await client.get("/v1/models", headers=headers)).status_code == 401
    # No credential at all is never an OpenViking key.
    assert (await client.get("/v1/models")).status_code == 401
    assert calls == []
    assert await app.state.management.credential(RECORD) is None


async def test_first_request_creates_the_record_and_registers_the_user(running_gateway):
    app, client, admin, _, _, _ = running_gateway
    management, store = app.state.management, app.state.store
    # Deleted gateway data blocks the user's scopes until a key is created again.
    assert (await client.delete("/admin/users/bob/data", headers=admin)).status_code == 200
    assert await management.get("tenant", "users", "bob") is None
    response = await client.get("/v1/models", headers={"x-api-key": OV_KEY})
    assert response.status_code == 200, response.text
    record = await management.credential(RECORD)
    assert record["account"] == "tenant"
    assert {k: record[k] for k in ("source", "openviking_key", "user_id", "prefix")} == {
        "source": "openviking",
        "openviking_key": OV_KEY,
        "user_id": "bob",
        "prefix": "bob-key",
    }
    assert not {"name", "policy_id", "upstream_ids", "models"} & set(record)
    user = await management.get("tenant", "users", "bob")
    assert user.pop("expires_at") > record["created_at"]
    assert user == {
        "user_id": "bob",
        "id": "bob",
        "revision": 1,
    }
    with store.connect() as c:
        assert not c.execute("SELECT * FROM deleted_scopes").fetchall()


async def test_request_during_user_data_deletion_does_not_block_the_key(running_gateway):
    app, client, admin, _, _, _ = running_gateway
    management = app.state.management
    body = {"model": "model", "messages": [{"role": "user", "content": "hi"}]}

    async def degradation():
        response = await client.post("/v1/chat/completions", headers=bearer(OV_KEY), json=body)
        assert response.status_code == 200, response.text
        return (await management.logs("tenant", user_id="bob"))[0].get("degradation")

    assert await degradation() is None
    delete_user = management.delete_user

    async def interleaved(account, user_id):
        # The client keeps sending requests while its user's data is being deleted.
        await degradation()
        await delete_user(account, user_id)
        await degradation()

    management.delete_user = interleaved
    assert (await client.delete("/admin/users/bob/data", headers=admin)).status_code == 200
    assert await degradation() is None
    assert await management.credential(RECORD)


async def test_capture_worker_delivers_with_the_stored_key(running_gateway):
    app, client, _, _, _, writes = running_gateway
    calls = record_openviking_calls(app)
    headers = {**bearer(OV_KEY), "X-OpenViking-Session": "direct"}
    messages = [{"role": "user", "content": "Remember the blue cluster"}]
    for _ in range(2):
        response = await client.post(
            "/v1/chat/completions", headers=headers, json={"model": "model", "messages": messages}
        )
        assert response.status_code == 200, response.text
        messages += [
            {"role": "assistant", "content": "hello"},
            {"role": "user", "content": "And the red one"},
        ]
    await make_due(app.state.store)
    for _ in range(5):
        if writes:
            break
        await app.state.worker.once()
    assert writes
    session_keys = {key for path, key in calls if path.startswith("/api/v1/sessions")}
    assert session_keys == {OV_KEY}


async def test_revoked_openviking_key_deletes_the_record(running_gateway):
    app, client, _, _, _, _ = running_gateway
    assert (await client.get("/v1/models", headers=bearer(OV_KEY))).status_code == 200
    app.state.test_backend["health_status"] = {OV_KEY: 401}
    app.state.viking.identities.clear()
    response = await client.get("/v1/models", headers=bearer(OV_KEY))
    assert response.status_code == 401
    assert response.json()["detail"].startswith("OpenViking rejected this API key.")
    assert await app.state.management.credential(RECORD) is None
    # Without a record, OpenViking is asked again and still rejects the key.
    response = await client.get("/v1/models", headers=bearer(OV_KEY))
    assert response.status_code == 401
    assert response.json()["reason"] == "openviking_http_401"
    assert await app.state.management.credential(RECORD) is None


async def test_binding_is_every_enabled_upstream_of_the_account(running_gateway):
    app, client, admin, key, _, _ = running_gateway
    upstream = (await client.get("/admin/upstreams/chat", headers=admin)).json()
    for identifier, models, enabled in (
        ("extra", ["extra-model"], True),
        ("off", ["off-model"], False),
    ):
        response = await client.put(
            f"/admin/upstreams/{identifier}",
            headers=admin,
            json={
                "name": identifier,
                "protocol": "chat",
                "base_url": upstream["base_url"],
                "api_key": "model-secret",
                "models": models,
                "enabled": enabled,
            },
        )
        assert response.status_code == 200, response.text

    async def model_names(headers):
        response = await client.get("/v1/models", headers=headers)
        assert response.status_code == 200, response.text
        return [item["id"] for item in response.json()["data"]]

    assert await model_names(bearer(OV_KEY)) == ["error", "extra-model", "model"]
    assert await model_names(bearer(key["key"])) == ["error", "model"]
    body = {"model": "extra-model", "messages": [{"role": "user", "content": "hi"}]}
    response = await client.post("/v1/chat/completions", headers=bearer(OV_KEY), json=body)
    assert response.status_code == 200, response.text
    response = await client.post("/v1/chat/completions", headers=bearer(key["key"]), json=body)
    assert response.status_code == 404
    body["model"] = "off-model"
    response = await client.post("/v1/chat/completions", headers=bearer(OV_KEY), json=body)
    assert response.status_code == 404


async def test_binding_falls_back_to_policy_defaults(running_gateway):
    management, config = running_gateway[0].state.management, running_gateway[0].state.config
    binding = await resolve_binding(
        management, config, {"source": "openviking", "account": "empty"}
    )
    assert binding == (Policy().model_dump(), [], [])
    binding = await resolve_binding(
        management, config, {"source": "openviking", "account": "tenant"}
    )
    assert binding.policy["name"] == "Default" and not binding.policy["gateway_tools"]


async def test_keys_listing_and_object_deletion_with_openviking_records(running_gateway):
    app, client, admin, key, _, _ = running_gateway
    assert (await client.get("/v1/models", headers=bearer(OV_KEY))).status_code == 200
    listed = (await client.get("/admin/keys", headers=admin)).json()
    record = next(item for item in listed if item["id"] == RECORD)
    assert record["source"] == "openviking" and record["user_id"] == "bob"
    assert {k: record[k] for k in ("name", "prefix", "policy_id", "upstream_ids", "models")} == {
        "name": "",
        "prefix": "bob-key",
        "policy_id": "",
        "upstream_ids": [],
        "models": [],
    }
    assert "openviking_key" not in record
    # The user sees their own record too.
    own = (await client.get("/admin/keys", headers={"X-API-Key": OV_KEY})).json()
    assert [item["id"] for item in own] == [RECORD]

    assert (
        await client.put("/admin/policies/spare", headers=admin, json={"name": "Spare"})
    ).status_code == 200
    assert (await client.delete("/admin/policies/spare", headers=admin)).status_code == 200
    # The issued key still counts; the OpenViking record does not.
    response = await client.delete("/admin/upstreams/chat", headers=admin)
    assert response.status_code == 409 and response.json()["key_count"] == 1
    assert (await client.delete("/admin/keys/" + key["id"], headers=admin)).status_code == 200
    assert (await client.delete("/admin/upstreams/chat", headers=admin)).status_code == 200
    assert (await client.delete("/admin/policies/default", headers=admin)).status_code == 200
    # Without a default profile the record still routes, with Policy() defaults.
    assert (await client.get("/v1/models", headers=bearer(OV_KEY))).status_code == 200


async def test_context_header_overrides_the_default_profile(running_gateway):
    app, client, admin, _, _, _ = running_gateway
    calls = record_openviking_calls(app)
    body = {"model": "model", "messages": [{"role": "user", "content": "Deploy?"}]}

    async def chat(session, context):
        headers = {**bearer(OV_KEY), "X-OpenViking-Session": session}
        headers["X-OpenViking-Context"] = context
        calls.clear()
        return await client.post("/v1/chat/completions", headers=headers, json=body)

    response = await chat("recall", '{"recall":true}')
    assert response.status_code == 200, response.text
    assert any(path == "/api/v1/search/search" for path, _ in calls)
    response = await chat("no-recall", '{"recall":false}')
    assert response.status_code == 200, response.text
    assert not any(path == "/api/v1/search/search" for path, _ in calls)
    logs = await app.state.management.logs("tenant", user_id="bob")
    assert logs[0]["context_override"] == ["recall"]

    response = await client.put(
        "/admin/policies/default",
        headers=admin,
        json={"name": "Locked", "gateway_tools": False, "client_overrides": False},
    )
    assert response.status_code == 200, response.text
    response = await chat("locked", '{"recall":false}')
    assert response.status_code == 403
    assert response.json()["reason"] == "context_override_not_allowed"
