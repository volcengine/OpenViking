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
    else:
        assert call["usage"] == {}


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
    assert call["complete"] is False
    assert call["error"] == "summary_incomplete"
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


@pytest.mark.parametrize("protocol", ["chat", "responses", "anthropic"])
@pytest.mark.parametrize("stream", [False, True])
async def test_original_usage_across_protocols(running_gateway, protocol, stream):
    from test_native_tools import native_response, request_body, wire_response

    app, client, admin, issued, _, _ = running_gateway
    value = native_response(protocol, 1)
    value["model"] = "model-snapshot"
    value["usage"] = {
        "input_tokens": 100,
        "output_tokens": 20,
        "total_tokens": 120,
        "output_tokens_details": {"reasoning_tokens": 8},
        "cached_creation_tokens": 3,
    }
    if protocol == "anthropic":
        value["usage"].update(
            service_tier="priority",
            cache_read_input_tokens=17,
            cache_creation={"ephemeral_5m_input_tokens": 2, "ephemeral_1h_input_tokens": 1},
        )
    else:
        value["service_tier"] = "priority"

    async def backend(request):
        if protocol == "chat" and stream:
            from aiohttp import web

            from openviking_gateway.tool_protocols.common import sse

            event = {
                **value,
                "choices": [{"index": 0, "delta": {"content": "hello"}, "finish_reason": "stop"}],
            }
            return web.Response(
                body=sse(event) + b"data: [DONE]\n\n", content_type="text/event-stream"
            )
        return await wire_response(request, protocol, value, stream)

    app.state.test_backend["handler"] = backend
    path = {
        "chat": "/v1/chat/completions",
        "responses": "/v1/responses",
        "anthropic": "/v1/messages",
    }[protocol]
    reply = await client.post(
        path,
        headers={"Authorization": "Bearer " + issued["key"]},
        json=request_body(protocol, stream),
    )
    assert reply.status_code == 200
    [row] = (await client.get("/admin/logs", headers=admin)).json()
    [call] = row["upstream_calls"]
    assert call["usage"] == reported_usage(value["usage"])
    assert call["response_id"] == value["id"]
    assert call["model"] == "model-snapshot"
    assert call["service_tier"] == "priority"
    assert call["cache_ttl"] is None


@pytest.mark.parametrize("stream", [False, True])
@pytest.mark.parametrize("failure", ["429", "incomplete"])
async def test_continuation_failure_uses_its_own_status(running_gateway, stream, failure):
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
        if number == 2:
            if failure == "429":
                return web.json_response({"error": {"message": "rate limited"}}, status=429)
            value["status"] = "in_progress"
        return await wire_response(request, "responses", value, stream)

    app.state.test_backend["handler"] = backend
    await client.post(
        "/v1/responses",
        headers={"Authorization": "Bearer " + issued["key"]},
        json=request_body("responses", stream),
    )
    [row] = (await client.get("/admin/logs", headers=admin)).json()
    first, second = row["upstream_calls"]
    assert first["status"] == 200 and first["complete"]
    assert second["status"] == (429 if failure == "429" else 200)
    assert second["error"] == ("upstream_error" if failure == "429" else "tool_upstream_incomplete")
    assert second["complete"] is False
    assert second["finished_at"] and second["duration_ms"] >= 0


def test_metadata_is_explicit_and_usage_depth_is_bounded():
    from openviking_gateway.billing import cache_ttl

    assert cache_ttl({}) is None
    assert (
        cache_ttl({"input": [{"content": [{"prompt_cache_breakpoint": {"ttl": "12h"}}]}]}) == "12h"
    )
    usage = {"input_tokens": 1}
    usage["input_tokens_details"] = usage
    assert reported_usage(usage) == {"input_tokens": 1, "input_tokens_details": {"input_tokens": 1}}
    for tier in ("auto", "scale"):
        call = UpstreamBilling({}).begin(
            "model", {"base_url": "https://provider", "coding_plan": True}, {"service_tier": tier}
        )
        assert call["service_tier"] == tier
        assert call["auth_mode"] == "coding_plan"


@pytest.mark.parametrize("tools", [False, True])
@pytest.mark.parametrize("cancelled", [False, True])
async def test_read_failure_closes_ledger(running_gateway, monkeypatch, tools, cancelled):
    import asyncio

    import aiohttp
    from test_app import enable_tools
    from test_native_tools import request_body

    from openviking_gateway import proxy as module

    app, client, admin, issued, _, _ = running_gateway
    if tools:
        await enable_tools(client, admin)
    original_open = module.open_upstream
    original_begin = UpstreamBilling.begin
    calls = []

    def begin(self, *args, **kwargs):
        result = original_begin(self, *args, **kwargs)
        calls.append(result)
        return result

    async def open_response(*args, **kwargs):
        response = await original_open(*args, **kwargs)

        async def read():
            if cancelled:
                raise asyncio.CancelledError()
            raise aiohttp.ClientPayloadError("truncated body")

        response.read = read
        return response

    monkeypatch.setattr(UpstreamBilling, "begin", begin)
    monkeypatch.setattr(module, "open_upstream", open_response)

    async def request():
        return await client.post(
            "/v1/responses",
            headers={"Authorization": "Bearer " + issued["key"]},
            json=request_body("responses", False),
        )

    if cancelled:
        with pytest.raises(asyncio.CancelledError):
            await request()
    else:
        assert (await request()).status_code == 502
    [call] = calls
    assert call["status"] == 200
    assert call["complete"] is False
    assert call["error"]
    assert call["finished_at"] and call["duration_ms"] >= 0


@pytest.mark.parametrize("tools", [False, True])
async def test_stream_error_event_is_recorded(running_gateway, tools):
    from aiohttp import web
    from test_app import enable_tools
    from test_native_tools import request_body

    from openviking_gateway.tool_protocols.common import sse

    app, client, admin, issued, _, _ = running_gateway
    if tools:
        await enable_tools(client, admin)

    async def backend(request):
        if request.path != "/v1/responses":
            return None
        return web.Response(
            body=sse({"type": "error", "error": {"message": "capacity", "code": "server_error"}}),
            content_type="text/event-stream",
        )

    app.state.test_backend["handler"] = backend
    await client.post(
        "/v1/responses",
        headers={"Authorization": "Bearer " + issued["key"]},
        json=request_body("responses", True),
    )
    [row] = (await client.get("/admin/logs", headers=admin)).json()
    [call] = row["upstream_calls"]
    assert call["status"] == 200
    assert call["complete"] is False
    assert call["error"] == ("tool_upstream_stream_error" if tools else "upstream_error")
