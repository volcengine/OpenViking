"""X-OpenViking-Context: per-request overrides of the key's context profile."""

import json

import orjson
import pytest

CHAT = "/v1/chat/completions"


def headers(key, session, context=None):
    values = {"Authorization": "Bearer " + key["key"], "X-OpenViking-Session": session}
    if context is not None:
        values["X-OpenViking-Context"] = (
            context if isinstance(context, str) else json.dumps(context)
        )
    return values


def message(text):
    return {"model": "model", "messages": [{"role": "user", "content": text}]}


def openviking_calls(app):
    """Record the OpenViking paths the gateway calls, besides its health checks."""
    calls = []

    async def handler(request):
        if request.path.startswith("/api/"):
            calls.append(request.path)

    app.state.test_backend["handler"] = handler
    return calls


async def test_override_takes_effect_and_is_logged(running_gateway):
    app, client, admin, key, seen, _ = running_gateway
    calls = openviking_calls(app)
    response = await client.post(CHAT, headers=headers(key, "plain"), json=message("Deploy?"))
    assert response.status_code == 200, response.text
    assert "/api/v1/search/search" in calls
    calls.clear()
    response = await client.post(
        CHAT, headers=headers(key, "no-recall", {"recall": False}), json=message("Deploy?")
    )
    assert response.status_code == 200, response.text
    assert "/api/v1/search/search" not in calls
    assert "x-openviking-context" not in {name.lower() for name in seen[-1][2]}
    logs = (await client.get("/admin/logs", headers=admin)).json()
    assert logs[0]["context_override"] == ["recall"]
    assert "context_override" not in logs[1]


async def test_empty_disabled_tools_offers_write_tools(running_gateway):
    _, client, _, key, seen, _ = running_gateway

    async def offered(session, context):
        response = await client.post(
            CHAT, headers=headers(key, session, context), json=message("Save a note")
        )
        assert response.status_code == 200, response.text
        return {tool["function"]["name"] for tool in orjson.loads(seen[-1][1]).get("tools", [])}

    read_only = await offered("read-only", {"gateway_tools": True})
    assert "openviking_find" in read_only and "openviking_write" not in read_only
    every = await offered("all-tools", {"gateway_tools": True, "disabled_tools": []})
    assert {"openviking_write", "openviking_add_resource"} <= every


@pytest.mark.parametrize(
    "context",
    [
        "{",
        "[]",
        {"bogus": 1},
        {"max_tokens": 10},
        {"window_soft_ratio": 0.9},
        {"name": "Other"},
        {"client_overrides": False},
        '{"idle_seconds": 1e999}',
        '{"session_max_tokens": NaN}',
        {"commit_tokens": 10**30},
        {"quotas": {"events": -1}},
    ],
)
async def test_invalid_override_is_rejected(running_gateway, context):
    app, client, _, key, seen, _ = running_gateway
    calls = openviking_calls(app)
    response = await client.post(CHAT, headers=headers(key, "bad", context), json=message("Hi"))
    assert response.status_code == 400
    assert response.json()["reason"] == "context_override_invalid"
    assert not seen and not calls


async def test_override_needs_the_profile_switch(running_gateway):
    _, client, admin, key, seen, _ = running_gateway
    response = await client.put(
        "/admin/policies/default",
        headers=admin,
        json={"name": "Locked", "gateway_tools": False, "client_overrides": False},
    )
    assert response.status_code == 200, response.text
    response = await client.post(
        CHAT, headers=headers(key, "locked", {"recall": False}), json=message("Hi")
    )
    assert response.status_code == 403
    assert response.json()["reason"] == "context_override_not_allowed"
    assert not seen
    response = await client.post(CHAT, headers=headers(key, "locked"), json=message("Hi"))
    assert response.status_code == 200, response.text
    # Requests the gateway passes through unchanged check the header too.
    response = await client.post(
        CHAT,
        headers={**headers(key, "locked", "garbage"), "Content-Encoding": "identity"},
        json=message("Hi"),
    )
    assert response.status_code == 403


async def test_profiles_saved_before_the_switch_accept_overrides(running_gateway):
    app, client, _, key, _, _ = running_gateway
    policy = await app.state.management.get("tenant", "policies", "default")
    policy.pop("client_overrides")
    await app.state.management.save("tenant", "policies", "default", policy)
    response = await client.post(
        CHAT, headers=headers(key, "legacy", {"recall": False}), json=message("Hi")
    )
    assert response.status_code == 200, response.text


async def test_large_stored_settings_keep_working(running_gateway):
    app, client, _, key, _, _ = running_gateway
    policy = await app.state.management.get("tenant", "policies", "default")
    large = {"quotas": {"events": 5000}, "tool_max_rounds": 20000, "session_max_tokens": 2 * 10**9}
    await app.state.management.save("tenant", "policies", "default", {**policy, **large})
    calls = openviking_calls(app)
    response = await client.post(CHAT, headers=headers(key, "large"), json=message("Deploy?"))
    assert response.status_code == 200, response.text
    assert "/api/v1/search/search" in calls
    response = await client.post(
        CHAT, headers=headers(key, "large-override", {"recall": False}), json=message("Hi")
    )
    assert response.status_code == 200, response.text


async def test_conversation_keeps_the_policy_it_started_with(running_gateway):
    app, client, _, key, _, _ = running_gateway
    calls = openviking_calls(app)
    body = message("Deploy?")
    response = await client.post(CHAT, headers=headers(key, "frozen", {"recall": False}), json=body)
    assert response.status_code == 200, response.text
    body["messages"] += [
        {"role": "assistant", "content": "hello"},
        {"role": "user", "content": "And the cluster?"},
    ]
    response = await client.post(CHAT, headers=headers(key, "frozen", {"recall": True}), json=body)
    assert response.status_code == 200, response.text
    assert "/api/v1/search/search" not in calls


async def test_missing_passthrough_key_fails_before_side_effects(running_gateway):
    app, client, _, key, seen, _ = running_gateway
    upstream = await app.state.management.get("tenant", "upstreams", "chat")
    await app.state.management.save(
        "tenant", "upstreams", "chat", {**upstream, "api_key": "", "auth_mode": "passthrough"}
    )
    calls = openviking_calls(app)
    response = await client.post(CHAT, headers=headers(key, "byok"), json=message("Hi"))
    assert response.status_code == 401
    assert not seen and not calls
    with app.state.store.connect() as c:
        assert not list(c.execute("SELECT 1 FROM replay"))
    response = await client.post(
        CHAT,
        headers={**headers(key, "byok"), "X-OpenViking-Upstream-Key": "client-secret"},
        json=message("Hi"),
    )
    assert response.status_code == 200, response.text
    assert seen[-1][2]["Authorization"] == "Bearer client-secret"


async def test_pinned_conversation_keeps_its_upstream_key(running_gateway):
    app, client, _, key, seen, _ = running_gateway
    body = message("Hi")
    response = await client.post(CHAT, headers=headers(key, "pinned"), json=body)
    assert response.status_code == 200, response.text
    # A higher-priority passthrough upstream added later does not take over the conversation.
    chat = await app.state.management.get("tenant", "upstreams", "chat")
    await app.state.management.save(
        "tenant",
        "upstreams",
        "anthropic",
        {**chat, "id": "anthropic", "api_key": "", "auth_mode": "passthrough", "priority": 10},
    )
    body["messages"] += [
        {"role": "assistant", "content": "hello"},
        {"role": "user", "content": "And now?"},
    ]
    response = await client.post(CHAT, headers=headers(key, "pinned"), json=body)
    assert response.status_code == 200, response.text
    assert seen[-1][2]["Authorization"] == "Bearer model-secret"
