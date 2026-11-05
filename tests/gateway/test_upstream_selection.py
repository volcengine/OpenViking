"""A host in front of the gateway picks the upstream with ``X-OpenViking-Upstream-URL``."""

import pytest

CHAT = "/v1/chat/completions"
BODY = {"model": "deployed", "messages": [{"role": "user", "content": "Deploy the cluster?"}]}


@pytest.fixture
def gateway_config(gateway_backend):
    base = gateway_backend[0]

    def upstream(protocol, path, **fields):
        return {
            "name": protocol,
            "protocol": protocol,
            "base_url": base + path,
            "api_key": "deployment-secret",
            "models": ["deployed"],
            **fields,
        }

    return {
        "openviking_keys": True,
        "default_upstreams": {
            "chat": upstream("chat", "/v1", priority=1),
            "responses": upstream("responses", "/v1"),
            "alt": upstream("chat", "/alt/v1", api_key="", auth_mode="passthrough"),
            "plan": upstream("chat", "/plan/v1", coding_plan=True),
            "alt-responses": upstream("responses", "/alt/v1", priority=5),
        },
        "default_profile": {
            "name": "Deployment profile",
            "recall": False,
            "profile": False,
            "capture": False,
            "gateway_tools": False,
        },
    }


def headers(url=None, **extra):
    selected = {"X-OpenViking-Upstream-URL": url} if url else {}
    return {"Authorization": "Bearer other", **selected, **extra}


async def test_header_selects_an_allowed_upstream_and_is_not_forwarded(running_gateway):
    app, client, _, _, captured, _ = running_gateway
    base = app.state.config.default_upstreams["alt"].base_url
    response = await client.post(CHAT, headers=headers(), json=BODY)
    assert response.status_code == 200, response.text
    assert captured[-1][0] == "/v1/chat/completions"
    assert captured[-1][2]["Authorization"] == "Bearer deployment-secret"
    # A trailing slash is the only difference tolerated; the key is the passthrough one.
    selected = headers(base + "/", **{"X-OpenViking-Upstream-Key": "host-key"})
    response = await client.post(CHAT, headers=selected, json=BODY)
    assert response.status_code == 200, response.text
    path, _, forwarded = captured[-1]
    assert path == "/alt/v1/chat/completions"
    assert forwarded["Authorization"] == "Bearer host-key"
    assert not any(name.lower().startswith("x-openviking-") for name in forwarded)


@pytest.mark.parametrize(
    "url", ["https://unlisted.example/v1", "{base}/v1/chat", "{base}/other/v1"]
)
async def test_unconfigured_url_is_rejected_without_contacting_it(running_gateway, url):
    app, client, _, _, captured, _ = running_gateway
    base = app.state.config.default_upstreams["chat"].base_url.removesuffix("/v1")
    response = await client.post(CHAT, headers=headers(url.format(base=base)), json=BODY)
    assert response.status_code == 403
    assert response.json()["detail"] == "Upstream URL is not allowed"
    assert captured == []


async def test_upstreams_sharing_a_url_are_picked_by_protocol(running_gateway):
    app, client, _, _, captured, _ = running_gateway
    url = app.state.config.default_upstreams["chat"].base_url
    request = {"model": "deployed", "input": "Deploy?"}
    response = await client.post("/v1/responses", headers=headers(), json=request)
    assert response.status_code == 200, response.text
    assert captured[-1][0] == "/alt/v1/responses"
    response = await client.post("/v1/responses", headers=headers(url), json=request)
    assert response.status_code == 200, response.text
    assert captured[-1][0] == "/v1/responses"
    response = await client.post(CHAT, headers=headers(url), json=BODY)
    assert response.status_code == 200, response.text
    assert captured[-1][0] == "/v1/chat/completions"
    logs = await app.state.management.logs("other")
    assert sorted(log["upstream_id"] for log in logs) == ["alt-responses", "chat", "responses"]


async def test_selected_coding_plan_upstream_is_still_refused(running_gateway):
    app, client, _, _, captured, _ = running_gateway
    url = app.state.config.default_upstreams["plan"].base_url
    response = await client.post(CHAT, headers=headers(url), json=BODY)
    assert response.status_code == 403
    assert "Coding Plan" in response.json()["detail"]
    assert captured == []


async def test_url_change_mid_session_degrades_to_the_new_upstream(running_gateway):
    app, client, _, _, captured, _ = running_gateway
    upstreams = app.state.config.default_upstreams
    session = {"X-OpenViking-Session": "moved"}
    response = await client.post(
        CHAT, headers=headers(upstreams["chat"].base_url, **session), json=BODY
    )
    assert response.status_code == 200, response.text
    reply = {"role": "assistant", "content": "hello"}
    follow = {"role": "user", "content": "And then?"}
    moved = headers(
        upstreams["alt"].base_url, **session, **{"X-OpenViking-Upstream-Key": "host-key"}
    )
    body = {**BODY, "messages": [*BODY["messages"], reply, follow]}
    response = await client.post(CHAT, headers=moved, json=body)
    assert response.status_code == 200, response.text
    assert captured[-1][0] == "/alt/v1/chat/completions"
    logs = await app.state.management.logs("other")
    moved_log = next(log for log in logs if log["upstream_id"] == "alt")
    assert moved_log["degradation"] == "upstream_changed"
