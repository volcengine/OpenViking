"""Every model-route request writes exactly one record, whichever way it ends."""

import ast
import asyncio
import json
from pathlib import Path
from types import SimpleNamespace

import aiohttp
import async_timeout
import pytest
from aiohttp import web
from test_app import enable_tools
from test_native_tools import PATHS, native_response, request_body, wire_response

from openviking_gateway import proxy
from openviking_gateway.observe import REASONS
from openviking_gateway.tool_protocols.common import ToolLoopError

GATEWAY = Path(proxy.__file__).parent
CHAT = {"model": "model", "messages": [{"role": "user", "content": "private prompt"}]}


@pytest.fixture
def gateway_config():
    return {"request_log_sink": "stdout"}


def records(capsys):
    lines = capsys.readouterr().out.splitlines()
    return [value for line in lines if (value := json.loads(line)).get("event") == "request"]


def only_record(capsys):
    found = records(capsys)
    assert len(found) == 1, found
    return found[0]


def assert_failure(record, status, reason, origin, error_class, attempted):
    assert record["schema"] == 2
    assert record["status"] == status
    assert record["error_reason"] == reason
    assert record["error_origin"] == origin
    assert record["error_class"] == error_class
    assert record["upstream_attempted"] is attempted


def bearer(issued, **headers):
    return {"Authorization": "Bearer " + issued["key"], **headers}


@pytest.mark.parametrize(
    "gateway_config",
    [{"request_log_sink": "stdout"}, {"request_log_sink": "store"}],
    ids=["stdout", "store"],
)
async def test_authentication_failure_is_recorded(running_gateway, capsys):
    app, client, _, _, captured, _ = running_gateway
    capsys.readouterr()
    response = await client.post(
        "/v1/chat/completions", headers={"Authorization": "Bearer ovgw_unknown"}, json=CHAT
    )
    assert response.status_code == 401
    body = response.json()
    assert body["reason"] == "gateway_key_invalid" and body["detail"].startswith("Invalid gateway")
    assert len(body["request_id"]) == 32
    assert not captured
    if app.state.config.request_log_sink == "store":
        # The store keeps records per account; a request without one is not stored.
        assert records(capsys) == []
        assert await app.state.management.port.logs("tenant") == []
        return
    record = only_record(capsys)
    assert_failure(record, 401, "gateway_key_invalid", "gateway", "api_key_invalid", False)
    assert record["account"] is None and record["user_id"] is None
    assert record["request_id"] == body["request_id"]
    assert "credential_id" not in record and record["auth_ms"] >= 0


@pytest.mark.parametrize(
    "reason",
    [
        "model_not_allowed",
        "upstream_url_not_allowed",
        "no_matching_upstream",
        "body_too_large",
        "context_override_invalid",
    ],
)
async def test_routing_failures_are_recorded(running_gateway, capsys, reason):
    app, client, admin, issued, captured, _ = running_gateway
    headers, body = bearer(issued), dict(CHAT)
    if reason == "model_not_allowed":
        minted = await client.post(
            "/admin/keys",
            headers=admin,
            json={
                "name": "narrow",
                "openviking_key": "user-key",
                "policy_id": "default",
                "upstream_ids": ["chat"],
                "models": ["model"],
            },
        )
        assert minted.status_code == 200, minted.text
        headers, body = bearer(minted.json()), {**CHAT, "model": "error"}
    elif reason == "upstream_url_not_allowed":
        headers["X-OpenViking-Upstream-URL"] = "https://elsewhere.example/v1"
    elif reason == "no_matching_upstream":
        body = {**CHAT, "model": "unknown"}
    elif reason == "body_too_large":
        app.state.config.max_body_bytes = 1024
        body = {**CHAT, "padding": "x" * 2048}
    else:
        headers["X-OpenViking-Context"] = "{"
    capsys.readouterr()
    response = await client.post("/v1/chat/completions", headers=headers, json=body)
    assert response.json()["reason"] == reason
    record = only_record(capsys)
    known = REASONS[reason]
    assert response.status_code == known.status
    assert_failure(record, known.status, reason, "gateway", known.error_class, False)
    assert record["account"] == "tenant" and record["credential_id"]
    assert not captured


async def test_unreachable_upstream_is_recorded(running_gateway, capsys, monkeypatch):
    _, client, _, issued, _, _ = running_gateway

    async def refuse(*args, **kwargs):
        raise aiohttp.ClientConnectorError(
            SimpleNamespace(host="upstream", port=443, ssl=True), ConnectionRefusedError()
        )

    monkeypatch.setattr(proxy, "open_upstream", refuse)
    capsys.readouterr()
    response = await client.post("/v1/chat/completions", headers=bearer(issued), json=CHAT)
    assert response.status_code == 502
    assert response.json()["detail"] == "Model upstream is unreachable"
    record = only_record(capsys)
    assert_failure(record, 502, "upstream_unreachable", "upstream", "model_error", True)
    assert "upstream_status" not in record and record["duration_ms"] >= 0


async def test_relayed_upstream_error_records_status_and_code(running_gateway, capsys):
    app, client, _, issued, _, _ = running_gateway
    error = {"error": {"code": "RateLimit", "message": "secret text"}}

    async def backend(request):
        if request.path == "/v1/chat/completions":
            return web.json_response(error, status=429)

    app.state.test_backend["handler"] = backend
    capsys.readouterr()
    response = await client.post("/v1/chat/completions", headers=bearer(issued), json=CHAT)
    assert response.status_code == 429 and response.json() == error
    output = capsys.readouterr().out
    assert "secret text" not in output
    (record,) = [json.loads(line) for line in output.splitlines()]
    assert_failure(record, 429, "upstream_error", "upstream", "model_error", True)
    assert record["upstream_status"] == 429 and record["upstream_error_code"] == "RateLimit"


async def test_unexpected_exception_returns_json_500_and_is_recorded(
    running_gateway, capsys, monkeypatch
):
    _, client, _, issued, captured, _ = running_gateway

    async def broken(self):
        raise RuntimeError("private failure detail")

    monkeypatch.setattr(proxy.ProxyRequest, "route", broken)
    capsys.readouterr()
    response = await client.post("/v1/chat/completions", headers=bearer(issued), json=CHAT)
    assert response.status_code == 500
    body = response.json()
    assert body["reason"] == "internal_error" and "private" not in response.text
    record = only_record(capsys)
    assert_failure(record, 500, "internal_error", "gateway", "gateway_internal", False)
    assert record["request_id"] == body["request_id"] and not captured


async def call(app, issued, receive, send, path="/v1/chat/completions"):
    """Call the gateway as the ASGI server would, with the server's own receive and send."""
    scope = {
        "type": "http",
        "asgi": {"version": "3.0", "spec_version": "2.3"},
        "http_version": "1.1",
        "method": "POST",
        "scheme": "http",
        "path": path,
        "raw_path": path.encode(),
        "query_string": b"",
        "root_path": "",
        "headers": [
            (b"authorization", ("Bearer " + issued["key"]).encode()),
            (b"content-type", b"application/json"),
            (b"x-openviking-session", b"leaving"),
        ],
        "client": ("127.0.0.1", 1),
        "server": ("gateway", 80),
        "app": app,
    }
    await app(scope, receive, send)


async def test_client_leaving_during_upload_is_recorded(running_gateway, capsys):
    app, _, _, issued, captured, _ = running_gateway
    messages = [
        {"type": "http.request", "body": b'{"model": "mo', "more_body": True},
        {"type": "http.disconnect"},
    ]

    async def receive():
        return messages.pop(0) if messages else {"type": "http.disconnect"}

    async def send(message):
        pass

    capsys.readouterr()
    await call(app, issued, receive, send)
    record = only_record(capsys)
    assert_failure(record, 499, "client_disconnected", "client", "client_cancelled", False)
    assert record["client_disconnected"] is True
    assert record["account"] == "tenant" and not captured


async def test_stream_that_raises_is_recorded(running_gateway, capsys, monkeypatch):
    _, client, _, issued, _, _ = running_gateway

    async def broken(self):
        raise RuntimeError("relay failed")

    monkeypatch.setattr(proxy.ProxyRequest, "relay", broken)
    capsys.readouterr()
    with pytest.raises(RuntimeError, match="relay failed"):
        await client.post(
            "/v1/chat/completions", headers=bearer(issued), json={**CHAT, "stream": True}
        )
    record = only_record(capsys)
    assert_failure(record, 200, "internal_error", "gateway", "gateway_internal", True)
    assert record["stream_error"] == "internal_error" and record["stream"] is True


async def test_client_leaving_mid_stream_is_recorded(running_gateway, capsys):
    app, _, _, issued, _, _ = running_gateway
    hanging = asyncio.Event()

    async def backend(request):
        if request.path != "/v1/chat/completions":
            return None
        response = web.StreamResponse(headers={"Content-Type": "text/event-stream"})
        await response.prepare(request)
        await response.write(b'data: {"choices":[{"delta":{"content":"hi"}}]}\n\n')
        await hanging.wait()
        return response

    app.state.test_backend["handler"] = backend
    body = json.dumps({**CHAT, "stream": True}).encode()
    started = asyncio.Event()
    requested = [{"type": "http.request", "body": body, "more_body": False}]

    async def receive():
        if requested:
            return requested.pop()
        await started.wait()
        return {"type": "http.disconnect"}

    async def send(message):
        if message["type"] == "http.response.body" and message.get("body"):
            started.set()

    capsys.readouterr()
    try:
        await asyncio.wait_for(call(app, issued, receive, send), 5)
    finally:
        hanging.set()
    record = only_record(capsys)
    assert_failure(record, 200, "client_disconnected", "client", "client_cancelled", True)
    assert record["stream_error"] == "client_disconnected"
    assert record["client_disconnected"] is True and record["first_chunk_ms"] >= 0


async def test_bookkeeping_failure_keeps_the_record(running_gateway, capsys, monkeypatch):
    app, client, _, issued, _, _ = running_gateway

    async def broken(*args):
        raise RuntimeError("store failed")

    monkeypatch.setattr(app.state.kernel, "completed", broken)
    capsys.readouterr()
    response = await client.post(
        "/v1/chat/completions", headers=bearer(issued, **{"X-OpenViking-Session": "s"}), json=CHAT
    )
    assert response.status_code == 200
    record = only_record(capsys)
    assert record["status"] == 200 and "error_reason" not in record
    assert record["bookkeeping_failed"] == ["completed"]


async def test_bookkeeping_that_hangs_is_cut_off(running_gateway, capsys, monkeypatch):
    app, client, _, issued, _, _ = running_gateway
    monkeypatch.setattr(proxy, "BOOKKEEPING_SECONDS", 0.05)

    async def hanging(*args):
        await asyncio.sleep(5)

    monkeypatch.setattr(app.state.kernel, "completed", hanging)
    capsys.readouterr()
    async with async_timeout.timeout(2):
        response = await client.post(
            "/v1/chat/completions",
            headers=bearer(issued, **{"X-OpenViking-Session": "s"}),
            json=CHAT,
        )
    assert response.status_code == 200
    record = only_record(capsys)
    assert record["bookkeeping_failed"] == ["completed"]


@pytest.mark.parametrize("hangs, reason", [(True, "save_timeout"), (False, "save_failed")])
async def test_save_that_fails_or_hangs_is_recorded(
    running_gateway, capsys, monkeypatch, hangs, reason
):
    app, client, _, issued, _, _ = running_gateway
    monkeypatch.setattr(proxy, "BOOKKEEPING_SECONDS", 0.05)

    async def save(*args):
        if hangs:
            await asyncio.sleep(5)
        raise RuntimeError("store failed")

    monkeypatch.setattr(app.state.kernel, "save", save)
    capsys.readouterr()
    async with async_timeout.timeout(2):
        response = await client.post(
            "/v1/chat/completions",
            headers=bearer(issued, **{"X-OpenViking-Session": "s"}),
            json=CHAT,
        )
    assert response.status_code == 200
    record = only_record(capsys)
    assert record["capture_status"] == "failed" and record["capture_reason"] == reason
    assert "error_reason" not in record and "bookkeeping_failed" not in record


@pytest.mark.parametrize("stream", [False, True])
@pytest.mark.parametrize("tools", [False, True])
async def test_one_record_per_request(running_gateway, capsys, stream, tools):
    app, client, admin, issued, _, _ = running_gateway
    path, body = "/v1/chat/completions", {**CHAT, "stream": stream}
    if tools:
        await enable_tools(client, admin, show_tool_calls=False)
        calls = []
        path, body = PATHS["responses"], request_body("responses", stream)

        async def backend(request):
            if request.path == "/mcp":
                if (await request.json()).get("method") == "tools/list":
                    return None
                return web.json_response({"id": 1, "result": {"content": []}})
            if request.path == path:
                calls.append(await request.json())
                value = native_response("responses", len(calls), owned=len(calls) == 1)
                return await wire_response(request, "responses", value, stream)

        app.state.test_backend["handler"] = backend
    capsys.readouterr()
    response = await client.post(path, headers=bearer(issued), json=body)
    assert response.status_code == 200, response.text
    record = only_record(capsys)
    assert record["status"] == 200 and "error_reason" not in record
    assert record["stream"] is stream and record["upstream_attempted"] is True
    if tools:
        assert len(calls) == 2 and record["hidden_rounds"] == 1


async def test_record_carries_ids_and_timings(running_gateway, capsys):
    _, client, _, issued, _, _ = running_gateway
    for stream in (False, True):
        capsys.readouterr()
        response = await client.post(
            "/api/v3/chat/completions",
            headers=bearer(issued, **{"X-OpenViking-Session": "timed"}),
            json={**CHAT, "stream": stream},
        )
        assert response.status_code == 200
        record = only_record(capsys)
        assert record["event"] == "request" and len(record["request_id"]) == 32
        assert record["method"] == "POST" and record["endpoint"] == "chat"
        assert record["account"] == "tenant" and record["openviking_account"] == "tenant"
        assert record["credential_id"] == issued["id"] and record["session_source"] == "header"
        assert record["upstream_status"] == 200 and record["upstream_attempted"] is True
        for name in ("read", "auth", "route", "prepare", "added", "upstream_ttfb", "duration"):
            assert record[name + "_ms"] >= 0
        assert record["total_ms"] >= record["added_ms"]
        assert 0 < record["received_at"] <= record["time"]
        assert ("first_chunk_ms" in record) is stream


async def test_degradations_lists_every_value_and_degradation_is_unchanged(running_gateway, capsys):
    app, client, _, issued, _, _ = running_gateway
    app.state.viking.unavailable_reason = "openviking_unavailable"
    capsys.readouterr()
    response = await client.post(
        "/v1/chat/completions",
        headers=bearer(issued, **{"X-OpenViking-Session": "degraded"}),
        json=CHAT,
    )
    assert response.status_code == 200, response.text
    record = only_record(capsys)
    assert record["degradation"] == "revocation_check_unavailable"
    assert record["degradations"] == ["revocation_check_unavailable", "recall_failed"]
    assert record["revocation_reason"] == "openviking_unavailable"
    assert record["recall_reason"] == "openviking_unavailable"


def literals(node):
    """The string values an argument can take: a literal, or either side of ``a if c else b``."""
    if isinstance(node, ast.Constant) and isinstance(node.value, str):
        return [node.value]
    if isinstance(node, ast.IfExp):
        return literals(node.body) + literals(node.orelse)
    return []


def calls(path, name):
    tree = ast.parse(path.read_text())
    return [
        node
        for node in ast.walk(tree)
        if isinstance(node, ast.Call)
        and getattr(node.func, "id", getattr(node.func, "attr", None)) == name
    ]


def test_every_raise_on_model_routes_has_a_listed_reason():
    assert calls(GATEWAY / "proxy.py", "HTTPException") == []
    reasons = [
        value
        for name in ("app.py", "proxy.py")
        for node in calls(GATEWAY / name, "GatewayError")
        for value in literals(node.args[0])
    ]
    assert len(reasons) >= 15 and set(reasons) <= REASONS.keys()
    codes = [
        value
        for path in [GATEWAY / "tool_loop.py", *GATEWAY.glob("tool_protocols/*.py")]
        for node in calls(path, "ToolLoopError")
        for keyword in node.keywords
        if keyword.arg == "code"
        for value in literals(keyword.value)
    ]
    assert len(codes) >= 10 and set(codes) <= REASONS.keys()
    assert ToolLoopError("default").code in REASONS
