"""Accounting evidence must retain hidden costs without changing model replies."""

import json

import pytest

from openviking_gateway.billing import UpstreamBilling, reported_usage
from openviking_gateway.observe import RequestRecord
from openviking_gateway.proxy import ProxyRequest


def test_ledger_preserves_missing_counters_and_redacts_payloads():
    metrics = {}
    ledger = UpstreamBilling(metrics)
    call = ledger.begin(
        "model",
        {"base_url": "https://api.openai.com/v1?api_key=private"},
        {"model": "model", "input": "private-prompt"},
        authenticated=True,
    )
    usage = {
        "input_tokens": 10,
        "input_tokens_details": {"cached_tokens": 0},
        "output_tokens_details": {"reasoning_tokens": 2},
        "private": "private-credential",
    }
    ledger.end(
        call,
        status=200,
        envelope={"id": "r1", "service_tier": "default"},
        usage=usage,
        complete=True,
    )
    assert call["usage"] == reported_usage(usage)
    assert "cache_write_tokens" not in call["usage"]["input_tokens_details"]
    assert call["usage"]["output_tokens_details"]["reasoning_tokens"] == 2
    assert "private" not in json.dumps(metrics)
    assert call["duration_ms"] >= 0


@pytest.mark.parametrize("stream", [False, True])
async def test_model_reply_has_correlatable_per_call_log(running_gateway, stream):
    app, client, admin, issued, _, _ = running_gateway
    response = await client.post(
        "/v1/chat/completions",
        headers={"Authorization": "Bearer " + issued["key"]},
        json={
            "model": "model",
            "stream": stream,
            "messages": [{"role": "user", "content": "hello"}],
        },
    )
    assert response.status_code == 200
    [row] = (await client.get("/admin/logs", headers=admin)).json()
    assert response.headers["x-openviking-request-id"] == row["request_id"]
    assert row["upstream_billing_schema"] == "openviking-upstream-billing-v1"
    [call] = row["upstream_calls"]
    assert call["kind"] == "model" and call["auth_mode"] == "api_key"
    assert call["complete"] is True
    assert call["started_at"] <= call["finished_at"]
    if stream:
        assert call["usage"]["prompt_tokens"] == 123
        assert call["usage"]["prompt_tokens_details"]["cached_tokens"] == 100


async def test_summary_usage_is_saved_even_when_summary_is_not_usable(running_gateway):
    from types import SimpleNamespace

    from aiohttp import web

    app, _, _, _, _, _ = running_gateway
    upstream = {
        "id": "responses",
        "protocol": "responses",
        "base_url": app.state.viking.base_url + "/v1",
        "api_key": "model-secret",
    }

    # Use the actual local transport; no kernel summary-text validation is involved.
    async def backend(request):
        if request.path.endswith("/responses"):
            return web.json_response(
                {
                    "id": "summary-1",
                    "status": "incomplete",
                    "usage": {
                        "input_tokens": 900,
                        "output_tokens": 100,
                        "input_tokens_details": {"cached_tokens": 400, "cache_write_tokens": 300},
                        "output_tokens_details": {"reasoning_tokens": 80},
                    },
                }
            )

    app.state.test_backend["handler"] = backend
    proxy = ProxyRequest(
        app,
        SimpleNamespace(headers={}, url=SimpleNamespace(query="")),
        "v1/responses",
        {},
        RequestRecord(app, None, recorded=False),
    )
    proxy.protocol = "responses"
    proxy.path = "/v1/responses"
    proxy.model = "model"
    prepared = SimpleNamespace(upstream=upstream)

    async def outgoing(body, upstream, prepared):
        return body

    proxy.outgoing = outgoing
    result = await proxy.summarize(prepared, {"model": "model", "stream": False})
    assert result["status"] == "incomplete"
    [call] = proxy.metrics["upstream_calls"]
    assert call["kind"] == "summary"
    assert call["usage"]["output_tokens_details"]["reasoning_tokens"] == 80
    assert call["usage"]["input_tokens_details"]["cache_write_tokens"] == 300


@pytest.mark.parametrize("stream", [False, True])
async def test_hidden_tool_rounds_each_keep_their_reported_usage(running_gateway, stream):
    from aiohttp import web
    from test_app import enable_tools
    from test_native_tools import native_response, request_body, wire_response

    app, client, admin, issued, _, _ = running_gateway
    await enable_tools(client, admin)
    number = 0

    async def backend(request):
        nonlocal number
        if request.path == "/mcp":
            return web.json_response(
                {
                    "jsonrpc": "2.0",
                    "id": 1,
                    "result": {"content": [{"type": "text", "text": "blue"}]},
                }
            )
        if request.path != "/v1/responses":
            return None
        number += 1
        value = native_response("responses", number, owned=number == 1)
        value["usage"]["input_tokens_details"]["cache_write_tokens"] = number
        return await wire_response(request, "responses", value, stream)

    app.state.test_backend["handler"] = backend
    response = await client.post(
        "/v1/responses",
        headers={"Authorization": "Bearer " + issued["key"]},
        json=request_body("responses", stream),
    )
    assert response.status_code == 200, response.text
    [row] = (await client.get("/admin/logs", headers=admin)).json()
    first, second = row["upstream_calls"]
    assert first["kind"] == "model" and second["kind"] == "tool_continuation"
    assert first["complete"] and second["complete"]
    assert [
        call["usage"]["input_tokens_details"]["cache_write_tokens"]
        for call in row["upstream_calls"]
    ] == [1, 2]
