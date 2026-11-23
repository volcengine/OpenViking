"""An OpenViking that stops answering is skipped per account, and waited on for one budget."""

import asyncio
import time

import aiohttp
import pytest
from aiohttp import web
from conftest import serve_gateway

from openviking_gateway.client import CALL, CallContext, VikingClient, VikingError
from openviking_gateway.config import GatewayConfig

CHAT = "/v1/chat/completions"
# Each request starts a new conversation, so the profile is fetched every time.
BODY = {"model": "model", "messages": [{"role": "user", "content": "What did we deploy?"}]}


async def post(client, headers):
    """A model request in its own task, as a server runs each; its call context stays there."""
    return await asyncio.create_task(client.post(CHAT, headers=headers, json=BODY))


def is_openviking_call(request, key="user-key"):
    """A request-path call to OpenViking with ``key``; saving writes are not."""
    return request.headers.get("X-API-Key") == key and not request.path.startswith(
        "/api/v1/sessions"
    )


@pytest.fixture
async def viking(gateway_backend):
    base, _, _, override = gateway_backend
    async with aiohttp.ClientSession() as http:
        yield VikingClient(http, base, "0", breaker_failures=2, breaker_seconds=0.2), override


def answer(override, status):
    calls = []

    async def handler(request):
        if request.path == "/probe":
            calls.append(request.headers.get("X-API-Key"))
            return web.json_response({"result": {}}, status=status[0])

    override["handler"] = handler
    return calls


async def call(viking, account):
    token = CALL.set(CallContext("req", circuit=account))
    try:
        return await viking.request("GET", "/probe", "key", timeout=2)
    finally:
        CALL.reset(token)


async def test_circuit_opens_after_consecutive_unanswered_calls_and_closes_on_a_good_trial(viking):
    viking, override = viking
    status = [503]
    calls = answer(override, status)
    for _ in range(2):
        with pytest.raises(VikingError, match="openviking_http_503"):
            await call(viking, "tenant")
    assert viking.breaker.open_count() == 1
    # Open: the call fails at once, without reaching OpenViking.
    with pytest.raises(VikingError, match="openviking_circuit_open"):
        await call(viking, "tenant")
    assert len(calls) == 2
    # After the open time one trial goes out; a failed trial opens the circuit again.
    await asyncio.sleep(0.25)
    with pytest.raises(VikingError, match="openviking_http_503"):
        await call(viking, "tenant")
    with pytest.raises(VikingError, match="openviking_circuit_open"):
        await call(viking, "tenant")
    assert len(calls) == 3
    status[0] = 200
    await asyncio.sleep(0.25)
    assert await call(viking, "tenant") == {}
    assert viking.breaker.open_count() == 0
    # Closed again: one more failure does not reopen it.
    status[0] = 503
    with pytest.raises(VikingError, match="openviking_http_503"):
        await call(viking, "tenant")
    status[0] = 200
    assert await call(viking, "tenant") == {}
    assert len(calls) == 6


async def test_circuits_are_per_account(viking):
    viking, override = viking
    status = [429]
    calls = answer(override, status)
    for _ in range(2):
        with pytest.raises(VikingError, match="openviking_http_429"):
            await call(viking, "tenant")
    with pytest.raises(VikingError, match="openviking_circuit_open"):
        await call(viking, "tenant")
    status[0] = 200
    assert await call(viking, "other") == {}
    # Calls outside a request, such as saving, neither consult nor feed the breaker.
    assert await viking.request("GET", "/probe", "key") == {}
    assert len(calls) == 4


@pytest.mark.parametrize("status", [401, 402, 403, 404, 409])
async def test_refusals_and_not_found_do_not_open_the_circuit(viking, status):
    viking, override = viking
    calls = answer(override, [status])
    for _ in range(4):
        with pytest.raises(VikingError, match=f"openviking_http_{status}"):
            await call(viking, "tenant")
    assert len(calls) == 4 and viking.breaker.open_count() == 0


async def test_timeouts_are_reported_apart_from_unreachable(gateway_backend):
    base, _, _, override = gateway_backend
    release = asyncio.Event()

    async def handler(request):
        if request.path == "/probe":
            await release.wait()
            return web.json_response({})

    override["handler"] = handler
    async with aiohttp.ClientSession() as http:
        try:
            with pytest.raises(VikingError, match="openviking_timeout"):
                await VikingClient(http, base, "0").request("GET", "/probe", "key", timeout=0.1)
        finally:
            release.set()
        with pytest.raises(VikingError, match="openviking_unavailable"):
            await VikingClient(http, "http://127.0.0.1:9", "0").request("GET", "/probe", "key")


async def test_open_circuit_skips_identity_recall_profile_and_tools(tmp_path, gateway_backend):
    config = {"openviking_breaker_failures": 1, "openviking_breaker_seconds": 30}
    async with serve_gateway(tmp_path, gateway_backend, config) as gateway:
        app, client, admin, issued, _, _ = gateway
        policy = {"name": "Default", "gateway_tools": True}
        assert (
            await client.put("/admin/policies/default", headers=admin, json=policy)
        ).status_code == 200
        seen = []

        async def handler(request):
            if is_openviking_call(request):
                seen.append(request.path)
                if request.path == "/health":
                    return web.json_response({}, status=503)

        app.state.test_backend["handler"] = handler
        app.state.viking.identities.clear()
        headers = {"Authorization": "Bearer " + issued["key"]}
        # The identity check goes unanswered and opens the account's circuit.
        assert (await post(client, headers)).status_code == 200
        assert seen[0] == "/health"
        assert app.state.viking.breaker.open_count() == 1
        seen.clear()
        app.state.viking.tools_cache.clear()
        app.state.viking.last_tools = None
        response = await post(client, headers)
        assert response.status_code == 200, response.text
        assert seen == []
        record = (await client.get("/admin/logs", headers=admin)).json()[0]
        assert record["revocation_reason"] == "openviking_circuit_open"
        assert record["recall_reason"] == "openviking_circuit_open"
        assert "recall_failed" in record["degradations"]
        assert record["tool_skip_reason"] == "tools_unavailable"
        assert record["profile_reason"] == "openviking_circuit_open"
        line = await app.state.stats.snapshot(app)
        assert line["openviking"]["open_circuits"] == 1


async def test_first_seen_key_is_not_subject_to_the_circuit(tmp_path, gateway_backend):
    config = {"openviking_breaker_failures": 1, "openviking_keys": True}
    async with serve_gateway(tmp_path, gateway_backend, config) as gateway:
        app, client, _, issued, _, _ = gateway
        checks = []

        async def handler(request):
            if request.path == "/health" and request.headers.get("X-API-Key"):
                checks.append(request.headers["X-API-Key"])
                if request.headers["X-API-Key"] == "user-key":
                    return web.json_response({}, status=503)

        app.state.test_backend["handler"] = handler
        app.state.viking.identities.clear()
        headers = {"Authorization": "Bearer " + issued["key"]}
        assert (await post(client, headers)).status_code == 200
        assert app.state.viking.breaker.open_count() == 1
        # bob's key is new: its own check runs although the account's circuit is open.
        bob = {"Authorization": "Bearer bob-key"}
        response = await post(client, bob)
        assert response.status_code == 200, response.text
        assert checks == ["user-key", "bob-key"]
        assert "X-OpenViking-Degraded" not in response.headers or (
            "revocation_check_unavailable" not in response.headers["X-OpenViking-Degraded"]
        )


async def test_profile_timeouts_open_the_circuit(tmp_path, gateway_backend, monkeypatch):
    config = {"openviking_breaker_failures": 2, "openviking_budget_seconds": 0.2}
    async with serve_gateway(tmp_path, gateway_backend, config) as gateway:
        app, client, admin, issued, _, _ = gateway
        policy = {"name": "Default", "gateway_tools": False, "recall": False}
        assert (
            await client.put("/admin/policies/default", headers=admin, json=policy)
        ).status_code == 200
        await app.state.viking.identify("user-key")
        release = asyncio.Event()

        async def handler(request):
            # Only the profile's calls hang; recall is off.
            if is_openviking_call(request) and request.path != "/health":
                await asyncio.wait_for(release.wait(), 10)

        app.state.test_backend["handler"] = handler
        request = app.state.viking.request

        async def delayed(*args, **kwargs):
            # Time passes before the HTTP call starts its own timer, as in a pool wait.
            await asyncio.sleep(0.02)
            return await request(*args, **kwargs)

        monkeypatch.setattr(app.state.viking, "request", delayed)
        headers = {"Authorization": "Bearer " + issued["key"]}
        try:
            for _ in range(3):
                assert (await post(client, headers)).status_code == 200
        finally:
            release.set()
        assert app.state.viking.breaker.open_count() == 1
        record = (await client.get("/admin/logs", headers=admin)).json()[0]
        assert record["profile_reason"] == "openviking_circuit_open"


@pytest.mark.parametrize("cached", [True, False])
async def test_one_budget_covers_identity_recall_and_profile(tmp_path, gateway_backend, cached):
    budget = 1.0
    async with serve_gateway(tmp_path, gateway_backend, {"openviking_budget_seconds": budget}) as (
        app,
        client,
        admin,
        issued,
        _,
        _,
    ):
        headers = {"Authorization": "Bearer " + issued["key"]}
        if cached:
            await app.state.viking.identify("user-key")
        else:
            app.state.viking.identities.clear()
        release, hung = asyncio.Event(), []

        async def handler(request):
            # The model call comes once the request path gave up on OpenViking; the
            # save after the reply then finds it answering.
            if request.path == CHAT:
                release.set()
            elif is_openviking_call(request) and not release.is_set():
                hung.append(request.path)
                await asyncio.wait_for(release.wait(), 10)

        app.state.test_backend["handler"] = handler
        started = time.monotonic()
        try:
            response = await post(client, headers)
        finally:
            elapsed = time.monotonic() - started
            release.set()
        assert response.status_code == 200, response.text
        # The time includes saving the turn after the reply, three quick calls here.
        assert elapsed < budget + 0.5
        assert ("/health" in hung) is not cached
        assert "/api/v1/search/search" in hung or not cached
        logs = (await client.get("/admin/logs", headers=admin)).json()
        assert isinstance(logs, list), logs
        record = logs[0]
        assert record["recall_reason"] in {"openviking_timeout", "openviking_budget_exhausted"}
        if not cached:
            assert record["revocation_reason"] == "openviking_timeout"


async def test_breaker_and_budget_are_off_by_default(tmp_path, gateway_backend):
    config = GatewayConfig()
    assert config.openviking_budget_seconds == 0
    assert config.openviking_breaker_failures == 0
    assert config.openviking_breaker_seconds == 30
    async with serve_gateway(tmp_path, gateway_backend, {}) as gateway:
        app, client, _, issued, _, _ = gateway
        assert app.state.viking.breaker is None
        checks = []

        async def handler(request):
            if request.path == "/health" and request.headers.get("X-API-Key") == "user-key":
                checks.append(1)
                return web.json_response({}, status=503)

        app.state.test_backend["handler"] = handler
        app.state.viking.identities.clear()
        headers = {"Authorization": "Bearer " + issued["key"]}
        for _ in range(4):
            assert (await post(client, headers)).status_code == 200
        # Each request checks its key, and its save the server version.
        assert len(checks) == 8
        assert app.state.viking.budget(5) == 5


async def test_budget_covers_the_first_confirmation_of_a_key(tmp_path, gateway_backend):
    config = {"openviking_keys": True, "openviking_budget_seconds": 0.2}
    async with serve_gateway(tmp_path, gateway_backend, config) as gateway:
        app, client = gateway[0], gateway[1]
        release = asyncio.Event()

        async def handler(request):
            if request.path == "/health" and request.headers.get("X-API-Key") == "new-key":
                await asyncio.wait_for(release.wait(), 10)

        app.state.test_backend["handler"] = handler
        started = time.monotonic()
        try:
            response = await post(client, {"Authorization": "Bearer new-key"})
        finally:
            elapsed = time.monotonic() - started
            release.set()
    assert response.status_code == 503, response.text
    assert response.json()["reason"] == "openviking_timeout"
    assert response.headers["X-OpenViking-Error-Origin"] == "openviking"
    assert response.headers["X-OpenViking-Upstream-Attempted"] == "0"
    assert elapsed < 0.8


async def test_first_confirmation_keeps_its_own_timeout_without_a_budget(
    tmp_path, gateway_backend, monkeypatch
):
    async with serve_gateway(tmp_path, gateway_backend, {"openviking_keys": True}) as gateway:
        app, client = gateway[0], gateway[1]
        waits = []
        request = app.state.viking.request

        async def recorded(method, path, key, *args, timeout=30, **kwargs):
            if key == "new-key":
                waits.append(timeout)
                raise VikingError("openviking_timeout")
            return await request(method, path, key, *args, timeout=timeout, **kwargs)

        monkeypatch.setattr(app.state.viking, "request", recorded)
        response = await post(client, {"Authorization": "Bearer new-key"})
    assert response.status_code == 503 and response.json()["reason"] == "openviking_timeout"
    assert waits == [5]
