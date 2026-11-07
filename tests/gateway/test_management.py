"""Identity, independent management authorization, and standalone hosting contracts."""

import asyncio
import json

import httpx
import pytest
from aiohttp import web

from openviking_gateway.app import create_app, health_loop, seed_defaults
from openviking_gateway.config import StandaloneGatewayConfig
from openviking_gateway.standalone import check_openviking, create_standalone_app
from openviking_gateway.storage import digest

P = "/admin"
MANAGER = {"X-OpenViking-Gateway-Management-Key": "ovgm_" + "m" * 40}


@pytest.mark.parametrize(
    "role,key",
    [("user", "user-key"), ("admin", "admin-key"), ("root", "root"), ("dev", "")],
)
@pytest.mark.parametrize("managed", [False, True])
async def test_permissions_matrix(running_gateway, role, key, managed):
    app, client, _, _, _, _ = running_gateway
    if managed:
        app.state.config.management_key = MANAGER[next(iter(MANAGER))]
    if role == "dev":
        app.state.test_backend["auth_mode"] = "dev"
        app.state.viking.identities.clear()
        chat = await app.state.management.get("tenant", "upstreams", "chat")
        await app.state.management.save("default", "upstreams", "chat", chat)
    headers = {"X-API-Key": key} if key else {}
    access = (await client.get(P + "/access", headers=headers)).json()
    assert access["role"] == ("root" if role == "dev" else role)
    assert access["can_write_objects"] is not managed
    # Dev has a single default user, so there is nobody else to issue for.
    assert access["can_issue_for_others"] is (role in {"admin", "root"})
    response = await client.put(P + "/policies/matrix", headers=headers, json={"name": "matrix"})
    assert response.status_code == (403 if managed else 200)
    if managed:
        response = await client.put(
            P + "/policies/matrix", headers={**headers, **MANAGER}, json={"name": "matrix"}
        )
        assert response.status_code == 200
    issue = await client.post(
        P + "/keys",
        headers=headers,
        json={
            "name": "self",
            "openviking_key": "user-key",
            "policy_id": "matrix",
            "upstream_ids": ["chat"],
        },
    )
    assert issue.status_code == 200, issue.text
    keys = (await client.get(P + "/keys", headers=headers)).json()
    assert issue.json()["id"] in [key["id"] for key in keys]


async def test_management_key_alone_never_grants_ov_data_access(running_gateway):
    app, client, _, _, _, _ = running_gateway
    app.state.config.management_key = MANAGER[next(iter(MANAGER))]
    app.state.test_backend["health_status"] = {"": 401}
    path = P + "/policies/automation?account_id=separate"
    assert (await client.put(path, headers=MANAGER, json={"name": "automation"})).status_code == 200
    assert (await client.get(P + "/access?account_id=separate", headers=MANAGER)).json()[
        "can_write_objects"
    ]
    for path in ("/logs", "/overview", "/keys"):
        assert (await client.get(P + path, headers=MANAGER)).status_code == 401
    assert (await client.post(P + "/keys", headers=MANAGER, json={})).status_code == 401
    bad = await client.get(
        P + "/access",
        headers={"X-API-Key": "user-key", "X-OpenViking-Gateway-Management-Key": "wrong"},
    )
    assert bad.status_code == 401 and bad.json()["reason"] == "management_key_invalid"


async def test_manager_cannot_elevate_user_scope(running_gateway):
    app, client, _, _, _, _ = running_gateway
    app.state.config.management_key = MANAGER[next(iter(MANAGER))]
    headers = {"X-API-Key": "user-key", **MANAGER}
    assert (
        await client.put(P + "/policies/p?account_id=other", headers=headers, json={})
    ).status_code == 200
    assert (await client.get(P + "/logs?account_id=other", headers=headers)).status_code == 403
    issued = await client.post(
        P + "/keys",
        headers=headers,
        json={"name": "x", "user_id": "bob", "policy_id": "default", "upstream_ids": ["chat"]},
    )
    assert issued.status_code == 403


async def test_readonly_secret_redaction_and_base_change(running_gateway):
    app, client, admin, _, _, _ = running_gateway
    old = await app.state.management.get("tenant", "upstreams", "chat")
    await app.state.management.save(
        "tenant", "upstreams", "chat", {**old, "headers": {"Secret-Header": "hidden"}}
    )
    app.state.config.management_key = MANAGER[next(iter(MANAGER))]
    view = (await client.get(P + "/upstreams/chat", headers=admin)).json()
    assert view["has_api_key"] and view["auth_mode"] == "managed"
    assert not {"api_key", "headers", "header_names"} & view.keys()
    changed = await client.put(
        P + "/upstreams/chat",
        headers={**admin, **MANAGER},
        json={"name": "chat", "protocol": "chat", "base_url": "https://new.example/v1"},
    )
    assert changed.status_code == 200 and not changed.json()["has_api_key"]
    assert not (await app.state.management.get("tenant", "upstreams", "chat"))["api_key"]


async def test_user_keys_logs_and_revoke_are_scoped(running_gateway):
    app, client, admin, alice, _, _ = running_gateway
    minted = await client.post(
        P + "/keys",
        headers=admin,
        json={"name": "bob", "user_id": "bob", "policy_id": "default", "upstream_ids": ["chat"]},
    )
    assert minted.status_code == 200 and minted.json()["user_id"] == "bob"
    bob = minted.json()
    for user in ("alice", "bob", None):
        await app.state.management.log(
            "tenant", {"kind": "user", "session": str(user)}, user_id=user
        )
    headers = {"X-API-Key": "user-key"}
    assert [row["id"] for row in (await client.get(P + "/keys", headers=headers)).json()] == [
        alice["id"]
    ]
    assert [row["session"] for row in (await client.get(P + "/logs", headers=headers)).json()] == [
        "alice"
    ]
    assert (await client.get(P + "/overview", headers=headers)).json()["requests"] == 1
    assert (await client.delete(P + "/keys/" + bob["id"], headers=headers)).status_code == 404
    assert (await client.delete(P + "/users/bob/data", headers=headers)).status_code == 403
    assert (await client.delete(P + "/keys/" + alice["id"], headers=headers)).status_code == 200


@pytest.mark.parametrize("caller_key", ["admin-key", "root"])
async def test_user_selection_and_unreadable_key(running_gateway, caller_key):
    app, client, _, _, _, _ = running_gateway
    headers = {"X-API-Key": caller_key}
    payload = {"name": "bob", "user_id": "bob", "policy_id": "default", "upstream_ids": ["chat"]}
    response = await client.post(P + "/keys?account_id=tenant", headers=headers, json=payload)
    assert response.status_code == 200 and response.json()["user_id"] == "bob"
    assert app.state.test_backend["users_calls"][-1][1] == caller_key
    assert "&name=bob" in app.state.test_backend["users_calls"][-1][0]
    # OpenViking omits api_key when it stores only a hash.
    app.state.test_backend["users"] = [{"user_id": "bob", "role": "user"}]
    response = await client.post(P + "/keys", headers=headers, json=payload)
    assert response.status_code == 409 and response.json()["reason"] == "user_key_unreadable"
    response = await client.post(
        P + "/keys", headers=headers, json={**payload, "openviking_key": "bob-key"}
    )
    assert response.status_code == 200


async def test_root_must_name_the_user_it_issues_for(running_gateway):
    _, client, _, _, _, _ = running_gateway
    payload = {"name": "k", "policy_id": "default", "upstream_ids": ["chat"]}
    response = await client.post(
        P + "/keys?account_id=tenant", headers={"X-API-Key": "root"}, json=payload
    )
    assert response.status_code == 400 and response.json()["reason"] == "user_required"


async def test_root_reads_tools_with_an_account_key(running_gateway):
    app, client, _, _, _, _ = running_gateway
    response = await client.get(P + "/tools?account_id=tenant", headers={"X-API-Key": "root"})
    assert response.status_code == 200 and response.json()
    assert app.state.test_backend["tools_calls"] == ["user-key"]


async def test_unavailable_openviking_skips_the_revocation_check(running_gateway):
    app, client, _, key, _, _ = running_gateway
    health_keys = []

    async def handler(request):
        if request.path == "/health":
            health_keys.append(request.headers.get("X-API-Key"))

    app.state.test_backend["handler"] = handler
    app.state.viking.identities.clear()
    app.state.viking.unavailable_reason = "openviking_unavailable"
    response = await client.get("/v1/models", headers={"X-API-Key": key["key"]})
    assert response.status_code == 200 and health_keys == []
    # Any successful identify shows OV is back, before the next health probe.
    await app.state.viking.identify("admin-key")
    assert not app.state.viking.unavailable_reason


async def test_revocation_removes_only_key_and_keeps_user_data(running_gateway):
    app, client, admin, key, _, _ = running_gateway
    await app.state.management.log("tenant", {"kind": "user"}, user_id="alice")
    await client.post(
        "/v1/chat/completions",
        headers={"X-API-Key": key["key"]},
        json={
            "model": "model",
            "messages": [{"role": "user", "content": "Remember the blue cluster"}],
        },
    )
    scope = digest("tenant\0alice\0chat")
    with app.state.store.connect() as c:
        before = c.execute("SELECT count(*) FROM sessions WHERE scope=?", (scope,)).fetchone()[0]
    assert before
    app.state.test_backend["health_status"] = {"user-key": 401}
    app.state.viking.identities.clear()
    response = await client.get("/v1/models", headers={"X-API-Key": key["key"]})
    assert response.status_code == 401 and "regenerated" in response.text
    assert await app.state.management.get("tenant", "keys", key["id"]) is None
    assert await app.state.management.logs("tenant", user_id="alice")
    with app.state.store.connect() as c:
        assert (
            c.execute("SELECT count(*) FROM sessions WHERE scope=?", (scope,)).fetchone()[0]
            == before
        )
    assert (await client.delete(P + "/users/alice/data", headers=admin)).status_code == 200
    assert not await app.state.management.logs("tenant", user_id="alice")


@pytest.mark.parametrize("failure", ["unreachable", "server_error", "identity_missing"])
async def test_unconfirmed_revocation_keeps_key_and_serves(running_gateway, failure):
    app, client, _, key, _, _ = running_gateway
    backend = app.state.test_backend

    async def handler(request):
        if request.path == "/health" and request.headers.get("X-API-Key") == "user-key":
            if failure == "server_error":
                return web.json_response({}, status=503)
            # OV swallows account-store errors and answers without an identity.
            return web.json_response({"status": "ok", "auth_mode": "api_key"})

    backend["handler"] = handler
    if failure == "unreachable":
        app.state.viking.base_url = "http://127.0.0.1:9"
    app.state.viking.identities.clear()
    response = await client.post(
        "/v1/chat/completions",
        headers={"X-API-Key": key["key"]},
        json={"model": "model", "messages": [{"role": "user", "content": "hello"}]},
    )
    assert response.status_code == 200
    assert await app.state.management.get("tenant", "keys", key["id"])
    degradation = (await app.state.management.logs("tenant"))[0]["degradation"]
    # With OV down, recall reports its own failure instead.
    assert degradation == "revocation_check_unavailable" or failure == "unreachable"
    # Nothing unconfirmed is cached: OV recovering takes effect on the next request.
    assert not app.state.viking.identities.entries


async def test_dev_mode_does_not_revoke_keys_of_named_users(running_gateway):
    app, client, _, key, _, _ = running_gateway
    app.state.test_backend["auth_mode"] = "dev"
    app.state.viking.identities.clear()
    response = await client.get("/v1/models", headers={"X-API-Key": key["key"]})
    assert response.status_code == 200
    assert await app.state.management.get("tenant", "keys", key["id"])


async def test_health_401_is_reachable_and_recall_works(running_gateway):
    app, client, _, key, _, _ = running_gateway
    app.state.test_backend["health_status"] = {"": 401}
    health = await app.state.viking.health(require_identity=False)
    assert health["requires_authentication"] and not app.state.viking.unavailable_reason
    response = await client.post(
        "/v1/chat/completions",
        headers={"X-API-Key": key["key"]},
        json={
            "model": "model",
            "messages": [{"role": "user", "content": "Remember our deploy blue cluster"}],
        },
    )
    assert response.status_code == 200
    # The request log carries the key's user, so that user's own view finds it.
    logs = await app.state.management.logs("tenant", user_id="alice")
    assert any(row.get("recall_count", 0) for row in logs)


async def test_dev_and_trusted_identity(running_gateway):
    app, client, _, _, _, _ = running_gateway
    app.state.test_backend["auth_mode"] = "dev"
    app.state.viking.identities.clear()
    identity = await app.state.viking.health()
    assert identity["user_id"] == identity["account_id"] == "default"
    access = (await client.get(P + "/access")).json()
    assert access["role"] == "root" and access["can_write_objects"]
    await client.put(P + "/policies/default", json={})
    await client.put(
        P + "/upstreams/local",
        json={"name": "local", "protocol": "chat", "base_url": "http://localhost/v1"},
    )
    response = await client.post(
        P + "/keys", json={"name": "dev", "policy_id": "default", "upstream_ids": ["local"]}
    )
    assert response.status_code == 200 and response.json()["user_id"] == "default"
    app.state.test_backend["auth_mode"] = "trusted"
    app.state.viking.identities.clear()
    response = await client.get(P + "/access")
    assert response.status_code == 403 and response.json()["reason"] == "trusted_mode_unsupported"


@pytest.mark.parametrize(
    "path", ["/api/v1/admin/gateway/keys", "/arbitrary", "/api/v1/sessions", "/api/v3evil/models"]
)
async def test_data_catchall_is_narrow(running_gateway, path):
    _, client, _, key, _, _ = running_gateway
    assert (await client.get(path, headers={"X-API-Key": key["key"]})).status_code == 404


@pytest.mark.parametrize(
    "status,body,ok,reason",
    [
        (
            200,
            {"choices": [{"message": {"role": "assistant", "content": "OK"}}]},
            True,
            "passed",
        ),
        (
            200,
            {"choices": [{"message": {"role": "assistant", "content": None}}]},
            False,
            "invalid_response",
        ),
        (200, {"error": {"code": "bad"}}, False, "invalid_response"),
        (401, {}, False, "upstream_key_rejected"),
        (403, {}, False, "upstream_key_rejected"),
        (400, {}, False, "model_request_rejected"),
        (404, {}, False, "model_request_rejected"),
        (405, {}, False, "model_request_rejected"),
        (429, {}, False, "rate_limited"),
        (503, {}, False, "upstream_unavailable"),
    ],
)
async def test_upstream_test_status_contract(running_gateway, status, body, ok, reason):
    app, client, admin, _, _, _ = running_gateway

    async def handler(request):
        if request.path == "/v1/chat/completions":
            return web.json_response(body, status=status)

    app.state.test_backend["handler"] = handler
    result = (
        await client.post(P + "/upstreams/chat/test", headers=admin, json={"model": "model"})
    ).json()
    assert (
        result["ok"] is ok
        and result["kind"] == "model"
        and result["reason"] == reason
        and result["url"].endswith("/v1/chat/completions")
    )
    if ok:
        assert "response" not in result
    else:
        upstream_response = result["response"]
        assert upstream_response["status"] == status
        assert upstream_response["http_version"].startswith("HTTP/")
        assert upstream_response["reason"]
        assert any(name.lower() == "content-type" for name in upstream_response["headers"])
        assert json.loads(upstream_response["body"]) == body


@pytest.mark.parametrize(
    "status,body,ok,reason",
    [
        (200, {"data": []}, True, "passed"),
        (200, {"error": {"code": "bad"}}, False, "upstream_key_rejected"),
        (401, {}, False, "upstream_key_rejected"),
        (404, {}, True, "models_unavailable"),
        (405, {}, True, "models_unavailable"),
        (429, {}, True, "rate_limited"),
        (503, {}, False, "upstream_unavailable"),
    ],
)
async def test_upstream_connection_status_contract(running_gateway, status, body, ok, reason):
    app, client, admin, _, _, _ = running_gateway
    requests = []

    async def handler(request):
        if request.path == "/v1/models":
            requests.append((request.method, await request.read()))
            return web.json_response(body, status=status)

    app.state.test_backend["handler"] = handler
    result = (await client.post(P + "/upstreams/chat/test", headers=admin, json={})).json()

    assert result["ok"] is ok
    assert result["kind"] == "connection"
    assert result["reason"] == reason
    assert result["url"].endswith("/v1/models")
    assert requests == [("GET", b"")]


async def test_upstream_test_reads_chunked_response_to_eof(running_gateway):
    app, client, admin, _, _, _ = running_gateway
    body = json.dumps({"choices": [{"message": {"role": "assistant", "content": "OK"}}]}).encode()

    async def handler(request):
        if request.path == "/v1/chat/completions":
            response = web.StreamResponse(headers={"Content-Type": "application/json"})
            await response.prepare(request)
            for start in range(0, len(body), 7):
                await response.write(body[start : start + 7])
                await asyncio.sleep(0)
            await response.write_eof()
            return response

    app.state.test_backend["handler"] = handler
    result = (
        await client.post(P + "/upstreams/chat/test", headers=admin, json={"model": "model"})
    ).json()

    assert result["ok"] and result["reason"] == "passed"


@pytest.mark.parametrize(
    "protocol,path,input_field,limit_field",
    [
        ("chat", "/v1/chat/completions", "messages", "max_tokens"),
        ("responses", "/v1/responses", "input", "max_output_tokens"),
        ("anthropic", "/v1/messages", "messages", "max_tokens"),
    ],
)
async def test_upstream_test_generates_with_protocol_and_alias(
    running_gateway, protocol, path, input_field, limit_field
):
    app, client, admin, _, captured, _ = running_gateway
    upstream = await app.state.management.get("tenant", "upstreams", protocol)
    await app.state.management.save(
        "tenant",
        "upstreams",
        protocol,
        {**upstream, "aliases": {"client-model": "model"}},
    )

    result = (
        await client.post(
            P + f"/upstreams/{protocol}/test",
            headers=admin,
            json={"model": "client-model"},
        )
    ).json()

    assert result["ok"] and result["reason"] == "passed"
    sent_path, raw, headers = captured[-1]
    payload = json.loads(raw)
    assert sent_path == path
    assert payload["model"] == "model"
    assert input_field in payload and payload[limit_field] == 64
    assert payload["stream"] is False
    assert "reasoning" not in payload and "reasoning_effort" not in payload
    assert headers["Authorization"] == "Bearer model-secret"
    assert payload["tools"]
    if protocol == "chat":
        assert payload["tools"][0]["function"]["name"] == "openviking_find"
        assert "parameters" in payload["tools"][0]["function"]
    elif protocol == "responses":
        assert payload["tools"][0]["name"] == "openviking_find"
        assert "parameters" in payload["tools"][0]
        assert "reasoning.encrypted_content" in payload["include"]
    else:
        assert payload["tools"][0]["name"] == "openviking_find"
        assert "input_schema" in payload["tools"][0]
    if protocol == "anthropic":
        assert headers["anthropic-version"] == "2023-06-01"


async def test_upstream_test_honors_exact_endpoint_url(running_gateway):
    app, client, admin, _, captured, _ = running_gateway
    upstream = await app.state.management.get("tenant", "upstreams", "chat")
    await app.state.management.save(
        "tenant",
        "upstreams",
        "chat",
        {
            **upstream,
            "append_endpoint_path": False,
            "allow_gateway_tools": False,
            "headers": {
                "X-Test-Header": "stored-value",
                "content-type": "application/vnd.test+json",
            },
        },
    )

    result = (
        await client.post(
            P + "/upstreams/chat/test",
            headers=admin,
            json={"model": "model"},
        )
    ).json()

    assert result["ok"] and result["url"].endswith("/v1")
    assert captured[-1][0] == "/v1"
    assert captured[-1][2]["X-Test-Header"] == "stored-value"
    sent_headers = {name.lower(): value for name, value in captured[-1][2].items()}
    assert sent_headers["content-type"] == "application/vnd.test+json"
    assert "tools" not in json.loads(captured[-1][1])


async def test_upstream_test_continues_without_unavailable_tools(running_gateway):
    app, client, admin, _, captured, _ = running_gateway
    app.state.test_backend["tools_status"] = {"admin-key": 503}

    result = (
        await client.post(
            P + "/upstreams/chat/test",
            headers=admin,
            json={"model": "model"},
        )
    ).json()

    assert result["ok"]
    assert "tools" not in json.loads(captured[-1][1])


async def test_upstream_test_requires_write_access(running_gateway):
    app, client, admin, _, captured, _ = running_gateway
    app.state.config.management_key = MANAGER[next(iter(MANAGER))]

    response = await client.post(
        P + "/upstreams/chat/test",
        headers=admin,
        json={"model": "model"},
    )

    assert response.status_code == 403
    assert response.json()["reason"] == "management_key_required"
    assert not [path for path, _, _ in captured if path.endswith("/chat/completions")]


async def test_vlm_permissions_idempotency_and_confirmation(running_gateway):
    app, client, admin, _, _, _ = running_gateway

    async def source(account):
        return {
            "upstreams": [
                {
                    "id": "vlm-plan",
                    "name": "Plan",
                    "protocol": "chat",
                    "base_url": "https://model.example/coding",
                    "coding_plan": True,
                }
            ],
            "skipped": [{"id": "azure", "reason": "azure_unsupported"}],
        }

    app.state.vlm_source = source
    assert not (await client.get(P + "/access", headers=admin)).json()["can_import_vlm"]
    assert (await client.post(P + "/upstreams/import-vlm", headers=admin)).status_code == 403
    root = {"X-API-Key": "root"}
    for _ in range(2):
        result = (await client.post(P + "/upstreams/import-vlm", headers=root)).json()
        assert result["needs_confirmation"] == [{"id": "vlm-plan", "name": "Plan"}]
    assert (
        len(
            [
                u
                for u in await app.state.management.list("tenant", "upstreams")
                if u["id"] == "vlm-plan"
            ]
        )
        == 1
    )
    app.state.config.management_key = MANAGER[next(iter(MANAGER))]
    assert (await client.post(P + "/upstreams/import-vlm", headers=root)).status_code == 403
    assert (
        await client.post(P + "/upstreams/import-vlm?account_id=elsewhere", headers=MANAGER)
    ).status_code == 200


async def test_standalone_health_studio_and_readonly_forwarding(running_gateway, tmp_path):
    upstream, _, _, _, _, _ = running_gateway
    studio = tmp_path / "studio"
    studio.mkdir()
    (studio / "index.html").write_text("<html>Studio</html>")
    config = StandaloneGatewayConfig(
        storage_path=str(tmp_path / "standalone"), openviking_url=upstream.state.openviking_url
    )
    upstream.state.test_backend["health_status"] = {"stale-key": 401}
    app = create_standalone_app(config, studio_dir=studio)
    async with (
        app.router.lifespan_context(app),
        httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://gateway"
        ) as client,
    ):
        result = (await client.get("/health")).json()
        assert (
            result["service"] == "openviking-gateway"
            and result["openviking_url"] == config.openviking_url
        )
        assert "role" not in result and result["auth_mode"] == "api_key"
        assert (await client.get("/health", headers={"X-API-Key": "root"})).json()["role"] == "root"
        stale = await client.get("/health", headers={"X-API-Key": "stale-key"})
        assert stale.status_code == 200 and "role" not in stale.json()
        access = await client.get("/gateway" + P + "/access", headers={"X-API-Key": "stale-key"})
        assert access.status_code == 401 and access.json()["reason"] == "openviking_http_401"
        assert (await client.get("/")).headers["location"] == "/studio/"
        assert "Studio" in (await client.get("/studio/gateway/keys")).text
        response = await client.get(
            "/api/v1/admin/accounts/tenant/users?include_credentials=true",
            headers={"X-API-Key": "admin-key"},
        )
        assert response.status_code == 200 and "api_key" not in response.json()["result"][0]
        assert "include_credentials=false" in upstream.state.test_backend["users_calls"][-1][0]
        assert (
            await client.post("/api/v1/admin/accounts", headers={"X-API-Key": "root"})
        ).status_code == 405
        assert (await client.get("/api/v1/admin/accounts/tenant/secrets")).status_code == 404
        guides = await client.get("/gateway" + P + "/guides", headers={"X-API-Key": "user-key"})
        assert guides.json()["base_url"] == "http://gateway/gateway"
        # The gateway lives only under /gateway; the root keeps Studio and the OV proxy.
        assert (await client.get(P + "/access", headers={"X-API-Key": "root"})).status_code == 404


async def test_base_change_drops_all_retained_secrets(running_gateway):
    app, client, admin, _, _, _ = running_gateway
    previous = await app.state.management.get("tenant", "upstreams", "chat")
    previous["headers"] = {"Authorization": "Bearer private", "X-Private": "private"}
    await app.state.management.save("tenant", "upstreams", "chat", previous)
    response = await client.put(
        P + "/upstreams/chat",
        headers=admin,
        json={
            "name": "new",
            "protocol": "chat",
            "base_url": "https://other.example/v1",
            "headers": {"Authorization": "", "New-Header": "explicit"},
        },
    )
    assert response.status_code == 200
    saved = await app.state.management.get("tenant", "upstreams", "chat")
    assert saved["api_key"] == "" and saved["headers"] == {"New-Header": "explicit"}


async def test_safe_validation_detail_and_typed_request_reason(running_gateway):
    _, client, admin, key, _, _ = running_gateway
    response = await client.put(
        P + "/upstreams/bad",
        headers=admin,
        json={
            "name": "bad",
            "protocol": "anthropic",
            "base_url": "https://example.com/v1",
            "api_key": "never-echo-this-secret",
        },
    )
    assert response.status_code == 422 and response.json()["reason"] == "invalid_configuration"
    assert "/v1/messages" in response.json()["detail"] and "never-echo" not in response.text
    response = await client.post(
        P + "/keys/" + key["id"] + "/capture/reset",
        headers=admin,
        json={"session": "s", "protocol": "invalid"},
    )
    assert response.status_code == 422 and response.json()["reason"] == "invalid_configuration"


async def test_revoked_key_keeps_pending_capture(running_gateway):
    from conftest import make_due

    app, client, admin, key, _, _ = running_gateway
    await client.post(
        "/v1/chat/completions",
        headers={"X-API-Key": key["key"], "X-OpenViking-Session": "pending"},
        json={
            "model": "model",
            "messages": [{"role": "user", "content": "Keep this unsaved conversation"}],
        },
    )
    scope, session = digest("tenant\0alice\0chat"), digest("pending")
    before = (await app.state.store.capture.get(scope, session)).value
    assert before["pending"]
    await client.delete(P + "/keys/" + key["id"], headers=admin)
    await make_due(app.state.store)
    assert await app.state.worker.once()
    after = (await app.state.store.capture.get(scope, session)).value
    assert after["pending"][0]["messages"] == before["pending"][0]["messages"]
    assert after["error"]["reason"] == "gateway_key_revoked"
    logs = await app.state.management.logs("tenant", user_id="alice")
    assert logs[0]["capture_status"] == "paused"
    assert logs[0]["capture_reason"] == "gateway_key_revoked"
    overview = (await client.get(P + "/overview", headers=admin)).json()
    assert overview["capture_issues"]["paused"] == 1


async def test_identity_cache_accepts_root_does_not_cache_failures():
    from openviking_gateway.client import VikingClient, VikingError

    client = VikingClient(None, "http://unused", "0.4.16")
    calls = []

    async def request(method, path, key, **kwargs):
        calls.append(key)
        if key == "invalid":
            raise VikingError("openviking_http_401", 401)
        return {"role": "root", "account_id": "default", "user_id": "root", "version": "0.4.16"}

    client.request = request
    assert (await client.identify("root"))["role"] == "root"
    assert (await client.identify("root"))["role"] == "root"
    for _ in range(2):
        with pytest.raises(VikingError):
            await client.identify("invalid")
    assert calls == ["root", "invalid", "invalid"]
    assert "root" not in client.identities.entries
    with pytest.raises(VikingError, match="root_key_not_allowed"):
        await client.health("root")


async def test_first_probe_does_not_block_startup(tmp_path, monkeypatch):
    import asyncio

    from openviking_gateway.client import VikingClient
    from openviking_gateway.config import GatewayConfig

    entered = asyncio.Event()

    async def health(self, *args, **kwargs):
        entered.set()
        await asyncio.Event().wait()

    monkeypatch.setattr(VikingClient, "health", health)
    app = create_app(GatewayConfig(storage_path=str(tmp_path)), openviking_url="http://unused")
    async with app.router.lifespan_context(app):
        await asyncio.wait_for(entered.wait(), 1)
        assert app.state.health == {"status": "starting"}


async def test_standalone_dev_requires_loopback(tmp_path, monkeypatch):
    from openviking_gateway.client import VikingClient

    async def health(self, *args, **kwargs):
        return {"auth_mode": "dev"}

    monkeypatch.setattr(VikingClient, "health", health)
    config = StandaloneGatewayConfig(
        storage_path=str(tmp_path), openviking_url="http://localhost:1933", host="0.0.0.0"
    )
    with pytest.raises(ValueError, match="loopback"):
        await check_openviking(config)


async def test_connection_test_is_available_for_missing_key_and_blocked_plan(running_gateway):
    app, client, admin, _, captured, _ = running_gateway
    upstream = await app.state.management.get("tenant", "upstreams", "chat")
    for change in ({"api_key": ""}, {"auth_mode": "passthrough"}):
        await app.state.management.save("tenant", "upstreams", "chat", {**upstream, **change})
        response = await client.post(
            P + "/upstreams/chat/test", headers=admin, json={"model": "model"}
        )
        assert response.status_code == 200 and response.json()["reason"] == "upstream_key_missing"
    assert not [path for path, _, _ in captured if path.endswith("/chat/completions")]
    await app.state.management.save(
        "tenant", "upstreams", "chat", {**upstream, "coding_plan": True, "allow_coding_plan": False}
    )
    response = await client.post(P + "/upstreams/chat/test", headers=admin, json={"model": "model"})
    assert response.status_code == 200 and response.json()["reason"] == "coding_plan_blocked"
    assert not [path for path, _, _ in captured if path.endswith("/chat/completions")]


@pytest.mark.parametrize("mode", ["trusted", "unknown"])
async def test_public_standalone_requires_confirmed_api_key_auth(tmp_path, monkeypatch, mode):
    from openviking_gateway.client import VikingClient, VikingError

    async def health(self, *args, **kwargs):
        if mode == "trusted":
            raise VikingError("trusted_mode_unsupported", 403)
        return {"status": "ok"}

    monkeypatch.setattr(VikingClient, "health", health)
    config = StandaloneGatewayConfig(
        storage_path=str(tmp_path), openviking_url="http://localhost:1933", host="0.0.0.0"
    )
    with pytest.raises(ValueError, match="trusted|successful synchronous probe"):
        await check_openviking(config)


async def test_public_standalone_accepts_auth_required_health(tmp_path, monkeypatch):
    from openviking_gateway.client import VikingClient

    async def health(self, *args, **kwargs):
        return {"status": "ok", "auth_mode": "api_key", "requires_authentication": True}

    monkeypatch.setattr(VikingClient, "health", health)
    config = StandaloneGatewayConfig(
        storage_path=str(tmp_path), openviking_url="http://localhost:1933", host="0.0.0.0"
    )
    assert (await check_openviking(config))["requires_authentication"]


async def test_loopback_standalone_refuses_trusted_only(tmp_path, monkeypatch):
    from openviking_gateway.client import VikingClient, VikingError

    failure = VikingError("openviking_unavailable")

    async def health(self, *args, **kwargs):
        raise failure

    monkeypatch.setattr(VikingClient, "health", health)
    config = StandaloneGatewayConfig(storage_path=str(tmp_path), openviking_url="http://ov.test")
    assert (await check_openviking(config))["reason"] == "openviking_unavailable"
    failure = VikingError("trusted_mode_unsupported", 403)
    with pytest.raises(ValueError, match="trusted"):
        await check_openviking(config)


async def test_non_ascii_management_header_is_rejected_cleanly(running_gateway):
    app, client, _, _, _, _ = running_gateway
    app.state.config.management_key = MANAGER[next(iter(MANAGER))]
    response = await client.get(
        P + "/access",
        headers=[
            (b"X-API-Key", b"user-key"),
            (b"X-OpenViking-Gateway-Management-Key", b"wrong-\xff"),
        ],
    )
    assert response.status_code == 401 and response.json()["reason"] == "management_key_invalid"


async def test_revoked_capture_is_idle_until_ttl_expires(running_gateway):
    import time

    from conftest import make_due

    app, client, admin, key, _, _ = running_gateway
    headers = {"X-API-Key": key["key"], "X-OpenViking-Session": "ttl-pending"}
    await client.post(
        "/v1/chat/completions",
        headers=headers,
        json={
            "model": "model",
            "messages": [{"role": "user", "content": "Preserve until retention expires"}],
        },
    )
    scope, session = digest("tenant\0alice\0chat"), digest("ttl-pending")
    await client.delete(P + "/keys/" + key["id"], headers=admin)
    await make_due(app.state.store)
    assert await app.state.worker.once()
    paused = (await app.state.store.capture.get(scope, session)).value
    assert paused["pending"] and paused["error"]["retry_at"] is None
    with app.state.store.connect() as connection:
        touched = connection.execute(
            "SELECT touched FROM sessions WHERE scope=? AND session=?", (scope, session)
        ).fetchone()[0]
    assert not await app.state.worker.once()
    with app.state.store.connect() as connection:
        assert (
            connection.execute(
                "SELECT touched FROM sessions WHERE scope=? AND session=?", (scope, session)
            ).fetchone()[0]
            == touched
        )
    await app.state.store.expire(time.time() + app.state.config.session_ttl_seconds)
    assert not (await app.state.store.capture.get(scope, session)).value


async def test_replacement_key_resumes_paused_capture(running_gateway):
    from conftest import make_due

    app, client, admin, key, _, writes = running_gateway
    messages = [{"role": "user", "content": "Remember this retained conversation"}]
    headers = {"X-API-Key": key["key"], "X-OpenViking-Session": "resume-paused"}
    body = {"model": "model", "messages": messages}
    assert (
        await client.post("/v1/chat/completions", headers=headers, json=body)
    ).status_code == 200
    await client.delete(P + "/keys/" + key["id"], headers=admin)
    await make_due(app.state.store)
    assert await app.state.worker.once()
    scope, session = digest("tenant\0alice\0chat"), digest("resume-paused")
    assert (await app.state.store.capture.get(scope, session)).value["error"]["retry_at"] is None
    replacement = (
        await client.post(
            P + "/keys",
            headers=admin,
            json={
                "name": "replacement",
                "user_id": "alice",
                "policy_id": "default",
                "upstream_ids": ["chat"],
            },
        )
    ).json()
    headers["X-API-Key"] = replacement["key"]
    body["messages"] = [
        *messages,
        {"role": "assistant", "content": "hello"},
        {"role": "user", "content": "Continue this conversation"},
    ]
    assert (
        await client.post("/v1/chat/completions", headers=headers, json=body)
    ).status_code == 200
    state = (await app.state.store.capture.get(scope, session)).value
    assert not state["error"] and state["credential_id"] == replacement["id"]
    assert await app.state.worker.once()
    assert writes


async def test_version_error_includes_required_version(running_gateway):
    app, client, admin, _, _, _ = running_gateway
    app.state.config.min_server_version = app.state.viking.min_version = "99.0.0"
    response = await client.post(
        P + "/keys",
        headers=admin,
        json={
            "name": "version",
            "openviking_key": "user-key",
            "policy_id": "default",
            "upstream_ids": ["chat"],
        },
    )
    assert response.status_code == 503 and response.json()["required_version"] == "99.0.0"


async def test_vlm_reimport_refreshes_source_fields_and_keeps_edits(running_gateway):
    app, client, _, _, _, _ = running_gateway
    candidate = {
        "id": "vlm-main",
        "name": "main",
        "protocol": "chat",
        "base_url": "https://model.example/v1",
        "api_key": "first",
        "models": ["model-a"],
    }

    async def source(account):
        return {
            "upstreams": [
                candidate,
                {"id": "vlm-bad", "name": "bad", "protocol": "chat", "base_url": "ftp://x"},
            ],
            "skipped": [],
        }

    app.state.vlm_source = source
    root = {"X-API-Key": "root"}
    result = (await client.post(P + "/upstreams/import-vlm", headers=root)).json()
    assert result["skipped"] == [{"id": "vlm-bad", "reason": "invalid_configuration"}]
    previous = await app.state.management.get("tenant", "upstreams", "vlm-main")
    edited = {**previous, "name": "renamed", "enabled": False, "priority": 5, "aliases": {"a": "b"}}
    await app.state.management.save("tenant", "upstreams", "vlm-main", edited)
    candidate.update(api_key="second", base_url="https://model.example/v2", models=["model-b"])
    await client.post(P + "/upstreams/import-vlm", headers=root)
    value = await app.state.management.get("tenant", "upstreams", "vlm-main")
    assert value["api_key"] == "second" and value["base_url"] == "https://model.example/v2"
    assert value["name"] == "renamed" and value["enabled"] is False and value["priority"] == 5
    assert value["models"] == ["model-a"] and value["aliases"] == {"a": "b"}


async def test_seed_defaults_is_idempotent(running_gateway):
    app, _, _, _, _, _ = running_gateway
    calls = []

    async def source(account):
        calls.append(account)
        return {
            "upstreams": [
                {"id": "vlm-x", "name": "x", "protocol": "chat", "base_url": "https://x.test"}
            ]
        }

    app.state.vlm_source = source
    for _ in range(2):
        await seed_defaults(app, "fresh")
    assert calls == ["fresh"]
    assert [u["id"] for u in await app.state.management.list("fresh", "upstreams")] == ["vlm-x"]
    assert await app.state.management.get("fresh", "policies", "default")


@pytest.mark.parametrize("base_url", [5, None, ["https://x.test"]])
async def test_non_string_base_url_is_invalid_configuration(running_gateway, base_url):
    _, client, admin, _, _, _ = running_gateway
    response = await client.put(
        P + "/upstreams/chat",
        headers=admin,
        json={"name": "chat", "protocol": "chat", "base_url": base_url},
    )
    assert response.status_code == 422 and response.json()["reason"] == "invalid_configuration"


async def test_keys_are_immutable_and_errors_explain_themselves(running_gateway):
    app, client, admin, key, _, _ = running_gateway
    response = await client.put(P + "/keys/" + key["id"], headers=admin, json={})
    assert response.status_code == 405 and response.json()["reason"] == "gateway_http_405"
    response = await client.get(P + "/upstreams/missing", headers=admin)
    body = response.json()
    assert response.status_code == 404 and body["reason"] == "not_found"
    assert body["detail"] != body["reason"]
    app.state.test_backend["health_status"] = {"stale-key": 401}
    response = await client.get(P + "/keys", headers={"X-API-Key": "stale-key"})
    assert response.json() == {
        "reason": "openviking_http_401",
        "detail": "OpenViking rejected the API key.",
    }


async def test_degraded_probe_backs_off_until_success():
    from types import SimpleNamespace

    from openviking_gateway.client import VikingError

    delays, results = [], [False, False, True, False, False, False, False]

    async def health(**kwargs):
        if not results.pop(0):
            raise VikingError("openviking_unavailable")
        return {"status": "ok"}

    async def sleep(delay):
        delays.append(delay)
        if not results:
            raise asyncio.CancelledError

    app = SimpleNamespace(state=SimpleNamespace(viking=SimpleNamespace(health=health)))
    with pytest.raises(asyncio.CancelledError):
        await health_loop(app, 4, sleep)
    # Every second until OV first answers; later outages back off 1, 2, 4 capped at the interval.
    assert delays == [1, 1, 4, 1, 2, 4, 4]


async def test_upstream_test_fills_url_key_and_reaches_exact_endpoints(running_gateway):
    app, client, admin, _, captured, _ = running_gateway
    upstream = await app.state.management.get("tenant", "upstreams", "chat")
    stored = upstream["base_url"] + "?tenant=a&key={api_key}"
    await app.state.management.save("tenant", "upstreams", "chat", {**upstream, "base_url": stored})
    seen = []

    async def handler(request):
        if request.path.startswith("/v1/"):
            seen.append((request.method, request.path, request.query_string, dict(request.headers)))

    app.state.test_backend["handler"] = handler
    result = (await client.post(P + "/upstreams/chat/test", headers=admin, json={})).json()
    assert result["ok"] and result["url"] == upstream["base_url"] + "/models?tenant=a&key={api_key}"
    assert seen[-1][2] == "tenant=a&key=model-secret" and "Authorization" not in seen[-1][3]

    endpoint = upstream["base_url"] + "/chat/completions?api-version=1"
    exact = {**upstream, "base_url": endpoint, "append_endpoint_path": False}
    await app.state.management.save("tenant", "upstreams", "chat", exact)
    before = len(captured)
    result = (await client.post(P + "/upstreams/chat/test", headers=admin, json={})).json()
    assert result == {
        "ok": False,
        "reason": "exact_endpoint_untestable",
        "url": endpoint,
        "kind": "connection",
    }
    assert len(captured) == before
    model = {"model": "model"}
    result = (await client.post(P + "/upstreams/chat/test", headers=admin, json=model)).json()
    assert result["kind"] == "model" and result["url"] == endpoint
    assert seen[-1][:3] == ("POST", "/v1/chat/completions", "api-version=1")
