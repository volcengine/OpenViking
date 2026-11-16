"""Request logs select storage or promptly flushed JSON on stdout."""

import json
import sys
import time

import pytest
from aiohttp import web
from conftest import make_due


@pytest.fixture(params=["store", "stdout"])
def gateway_config(request):
    return {"request_log_sink": request.param}


async def test_requests_use_selected_sink(running_gateway, capsys):
    app, client, admin, issued, _, _ = running_gateway
    stdout = app.state.config.request_log_sink == "stdout"
    key = issued["key"]
    request_ids = set()
    for index, (stream, model, status) in enumerate(
        [(False, "model", 200), (True, "model", 200), (False, "error", 429)]
    ):
        session = f"private-session-{index}"
        response = await client.post(
            "/v1/chat/completions",
            headers={"Authorization": f"Bearer {key}", "X-OpenViking-Session": session},
            json={
                "model": model,
                "stream": stream,
                "messages": [{"role": "user", "content": "private prompt\n你好"}],
            },
        )
        assert response.status_code == status, response.text
        output = capsys.readouterr().out
        stored = (await client.get("/admin/logs", headers=admin)).json()
        if stdout:
            assert len(output.splitlines()) == 1
            record = json.loads(output)
            assert record["account"] == "tenant" and record["user_id"] == "alice"
            assert stored == []
            assert await app.state.management.port.logs("tenant") == []
        else:
            assert output == ""
            assert len(stored) == index + 1
            record = stored[0]
        assert 0 <= time.time() - record["time"] < 10
        assert record["model"] == model and record["status"] == status
        assert record["kind"] == "user" and record["protocol"] == "chat"
        assert record["duration_ms"] >= 0
        if stream:
            assert record["input_tokens"] == 123 and record["cached_tokens"] == 100
        request_ids.add(record["request_id"])
        for secret in (key, "user-key", "model-secret", session, "private prompt"):
            assert secret not in output + json.dumps(stored)
    assert len(request_ids) == 3
    overview = (await client.get("/admin/overview", headers=admin)).json()
    assert overview["requests"] == (0 if stdout else 3)


async def test_failure_records_carry_no_secrets(running_gateway, capsys):
    app, client, admin, issued, _, _ = running_gateway
    stdout = app.state.config.request_log_sink == "stdout"

    async def backend(request):
        if request.path == "/v1/chat/completions":
            return web.json_response({"error": {"message": "upstream private detail"}}, status=500)

    app.state.test_backend["handler"] = backend
    body = {"model": "model", "messages": [{"role": "user", "content": "private prompt"}]}
    capsys.readouterr()
    for headers, status in [
        ({"Authorization": "Bearer ovgw_guessed-secret"}, 401),
        ({"Authorization": "Bearer " + issued["key"], "X-OpenViking-Context": "{secret"}, 400),
        ({"Authorization": "Bearer " + issued["key"]}, 500),
    ]:
        response = await client.post("/v1/chat/completions", headers=headers, json=body)
        assert response.status_code == status, response.text
    output = capsys.readouterr().out
    stored = (await client.get("/admin/logs", headers=admin)).json()
    found = [json.loads(line) for line in output.splitlines()] if stdout else stored
    # Without an account, the store sink cannot keep the authentication failure.
    expected = {"context_override_invalid", "upstream_error"}
    assert {r["error_reason"] for r in found} == (
        expected | {"gateway_key_invalid"} if stdout else expected
    )
    for secret in (
        "guessed-secret",
        issued["key"],
        "user-key",
        "model-secret",
        "private prompt",
        "{secret",
        "upstream private detail",
    ):
        assert secret not in output + json.dumps(stored)


async def test_capture_events_use_selected_sink(running_gateway, capsys):
    app, client, _, issued, _, writes = running_gateway
    response = await client.post(
        "/v1/chat/completions",
        headers={"Authorization": "Bearer " + issued["key"], "X-OpenViking-Session": "capture"},
        json={"model": "model", "messages": [{"role": "user", "content": "Remember this"}]},
    )
    assert response.status_code == 200, response.text
    capsys.readouterr()

    async def unavailable(request):
        if request.path.startswith("/api/v1/sessions"):
            return web.Response(status=503)

    for handler, status, reason in [
        (unavailable, "retrying", "openviking_http_503"),
        (None, "active", "delivery_recovered"),
    ]:
        app.state.test_backend["handler"] = handler
        await make_due(app.state.store)
        assert await app.state.worker.once()
        output = capsys.readouterr().out
        stored = await app.state.management.logs("tenant")
        if app.state.config.request_log_sink == "stdout":
            assert len(output.splitlines()) == 1
            record = json.loads(output)
            assert record["account"] == "tenant" and record["user_id"] == "alice"
            assert stored == []
        else:
            assert output == ""
            record = stored[0]
        assert record["kind"] == "capture"
        assert record["capture_status"] == status and record["capture_reason"] == reason
        for secret in (issued["key"], "user-key", "model-secret", "Remember this"):
            assert secret not in output + json.dumps(stored)
    assert writes


async def test_stdout_flushes_each_line(monkeypatch):
    from types import SimpleNamespace

    from openviking_gateway.app import stdout_log

    calls = []
    buffer = SimpleNamespace(
        write=lambda data: calls.append(data), flush=lambda: calls.append("flush")
    )
    monkeypatch.setattr(sys, "stdout", SimpleNamespace(buffer=buffer))
    await stdout_log("tenant\n你好", {"kind": "capture"})
    line, flushed = calls
    assert line.endswith(b"\n") and line.count(b"\n") == 1
    record = json.loads(line)
    assert record["account"] == "tenant\n你好" and record["user_id"] is None
    assert record["kind"] == "capture" and record["time"] <= time.time()
    assert flushed == "flush"
