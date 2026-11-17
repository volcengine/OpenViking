"""The per-process limit of model requests in flight (``max_inflight_requests``)."""

import asyncio

import pytest
from aiohttp import web

LIMITED = {"max_inflight_requests": 1}


def chat(client, issued, stream=False, session="overload"):
    """One chat request in a task of its own, as each request runs in production."""
    body = {"model": "model", "messages": [{"role": "user", "content": "Hello there"}]}
    if stream:
        body["stream"] = True
    return asyncio.create_task(
        client.post(
            "/v1/chat/completions",
            headers={"Authorization": "Bearer " + issued["key"], "X-OpenViking-Session": session},
            json=body,
        )
    )


async def until(condition):
    for _ in range(500):
        if condition():
            return
        await asyncio.sleep(0.01)
    raise AssertionError("condition not reached")


def hold_upstream(app):
    """Hold model requests at the provider until the returned event is set."""
    gate, entered = asyncio.Event(), []

    async def handler(request):
        if request.path == "/v1/chat/completions":
            entered.append(request.path)
            await gate.wait()

    app.state.test_backend["handler"] = handler
    return gate, entered


def assert_overloaded(response):
    assert response.status_code == 503, response.text
    assert response.json()["reason"] == "gateway_overloaded"
    assert response.headers["Retry-After"] == "1"
    assert response.headers["X-OpenViking-Error-Reason"] == "gateway_overloaded"
    assert response.headers["X-OpenViking-Error-Origin"] == "gateway"
    assert response.headers["X-OpenViking-Upstream-Attempted"] == "0"


@pytest.mark.parametrize("gateway_config", [LIMITED])
async def test_request_over_the_limit_is_rejected_with_503(running_gateway, monkeypatch):
    app, client, _, issued, _, _ = running_gateway
    gate, entered = hold_upstream(app)
    first = chat(client, issued)
    await until(lambda: entered)
    management, lookups = app.state.management, []
    authenticate = management.authenticate

    async def counted(key):
        lookups.append(key)
        return await authenticate(key)

    monkeypatch.setattr(management, "authenticate", counted)
    try:
        assert_overloaded(await asyncio.wait_for(chat(client, issued), 5))
    finally:
        gate.set()
    # Refused before any store or OpenViking work.
    assert lookups == [] and len(entered) == 1
    assert app.state.stats.rejected_overload == 1
    assert (await first).status_code == 200
    assert (await chat(client, issued)).status_code == 200
    assert app.state.stats.inflight_requests == 0


@pytest.mark.parametrize("gateway_config", [LIMITED])
async def test_stream_holds_its_slot_until_the_body_ends(running_gateway):
    app, client, _, issued, _, _ = running_gateway
    stats, release = app.state.stats, asyncio.Event()
    original = app.state.test_backend.get("handler")

    async def handler(request):
        # The provider sends headers at once, then waits before the body.
        if request.path == "/v1/chat/completions":
            response = web.StreamResponse(headers={"Content-Type": "text/event-stream"})
            await response.prepare(request)
            await response.write(b'data: {"choices":[{"delta":{"content":"hi"}}]}\n\n')
            await release.wait()
            await response.write(b"data: [DONE]\n\n")
            await response.write_eof()
            return response
        return await original(request) if original else None

    app.state.test_backend["handler"] = handler
    first = chat(client, issued, stream=True)
    await until(lambda: stats.inflight_streams == 1)
    try:
        assert_overloaded(await asyncio.wait_for(chat(client, issued, stream=True), 5))
    finally:
        release.set()
    assert (await first).status_code == 200
    assert stats.inflight_requests == stats.inflight_streams == 0
    assert (await chat(client, issued)).status_code == 200


@pytest.mark.parametrize("gateway_config", [LIMITED])
async def test_failed_and_cancelled_requests_release_their_slot(running_gateway):
    app, client, _, issued, _, _ = running_gateway
    stats = app.state.stats
    refused = await client.post(
        "/v1/chat/completions",
        headers={"Authorization": "Bearer ovgw_unknown"},
        json={"model": "model", "messages": [{"role": "user", "content": "Hello"}]},
    )
    assert refused.status_code == 401
    assert stats.inflight_requests == 0
    gate, entered = hold_upstream(app)
    held = chat(client, issued)
    await until(lambda: entered)
    held.cancel()
    with pytest.raises(asyncio.CancelledError):
        await held
    assert stats.inflight_requests == 0
    gate.set()
    assert (await chat(client, issued)).status_code == 200
    assert stats.rejected_overload == 0


async def test_no_limit_by_default(running_gateway):
    app, client, _, issued, _, _ = running_gateway
    assert app.state.config.max_inflight_requests == 0
    gate, entered = hold_upstream(app)
    held = [chat(client, issued, session=f"s{i}") for i in range(5)]
    await until(lambda: len(entered) == 5)
    gate.set()
    assert [(await task).status_code for task in held] == [200] * 5
    assert app.state.stats.rejected_overload == 0


@pytest.mark.parametrize("gateway_config", [LIMITED])
async def test_models_route_is_not_limited(running_gateway):
    app, client, _, issued, _, _ = running_gateway
    gate, entered = hold_upstream(app)
    held = chat(client, issued)
    await until(lambda: entered)
    models = await client.get("/v1/models", headers={"Authorization": "Bearer " + issued["key"]})
    assert models.status_code == 200, models.text
    assert app.state.stats.rejected_overload == 0
    gate.set()
    assert (await held).status_code == 200
