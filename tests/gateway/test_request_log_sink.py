"""Request logs select storage or promptly flushed JSON on stdout."""

import json
import sys
import time

import pytest
from aiohttp import web


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


async def test_capture_outcomes_use_selected_sink(running_gateway, capsys):
    app, client, _, issued, _, writes = running_gateway
    stdout = app.state.config.request_log_sink == "stdout"

    async def unavailable(request):
        if request.path.startswith("/api/v1/sessions"):
            return web.Response(status=503)

    messages = [{"role": "user", "content": "Remember this"}]
    for handler, expected in [
        (unavailable, {"capture_status": "failed", "capture_reason": "openviking_http_503"}),
        (None, {"capture_status": "active", "capture_messages": 4}),
    ]:
        app.state.test_backend["handler"] = handler
        capsys.readouterr()
        response = await client.post(
            "/v1/chat/completions",
            headers={"Authorization": "Bearer " + issued["key"], "X-OpenViking-Session": "capture"},
            json={"model": "model", "messages": messages},
        )
        assert response.status_code == 200, response.text
        output = capsys.readouterr().out
        stored = await app.state.management.logs("tenant")
        if stdout:
            assert len(output.splitlines()) == 1
            record = json.loads(output)
            assert record["account"] == "tenant" and record["user_id"] == "alice"
            assert stored == []
        else:
            assert output == ""
            record = stored[0]
        assert record["kind"] == "user"
        assert expected.items() <= record.items()
        for secret in (issued["key"], "user-key", "model-secret", "Remember this"):
            assert secret not in output + json.dumps(stored)
        messages += [
            {"role": "assistant", "content": "hello"},
            {"role": "user", "content": "And this"},
        ]
    assert writes


async def test_stdout_flushes_each_line(monkeypatch):
    from types import SimpleNamespace

    from openviking_gateway.app import stdout_log

    calls = []
    buffer = SimpleNamespace(
        write=lambda data: calls.append(data), flush=lambda: calls.append("flush")
    )
    monkeypatch.setattr(sys, "stdout", SimpleNamespace(buffer=buffer))
    await stdout_log("tenant\n你好", {"kind": "user"})
    line, flushed = calls
    assert line.endswith(b"\n") and line.count(b"\n") == 1
    record = json.loads(line)
    assert record["account"] == "tenant\n你好" and record["user_id"] is None
    assert record["kind"] == "user" and record["time"] <= time.time()
    assert flushed == "flush"


async def test_file_sink_writes_one_line_per_record(tmp_path, gateway_backend, capsys):
    from conftest import serve_gateway

    path = tmp_path / "requests.jsonl"
    config = {"request_log_sink": "file", "request_log_path": str(path)}
    async with serve_gateway(tmp_path, gateway_backend, config) as gateway:
        app, client, admin, issued, _, _ = gateway
        body = {"model": "model", "messages": [{"role": "user", "content": "private prompt"}]}
        for key, status in [(issued["key"], 200), ("ovgw_guessed-secret", 401)]:
            response = await client.post(
                "/v1/chat/completions", headers={"Authorization": f"Bearer {key}"}, json=body
            )
            assert response.status_code == status, response.text
        assert (await client.get("/admin/logs", headers=admin)).json() == []
    # Leaving the app closes the sink, which writes what is still queued.
    lines = path.read_text().splitlines()
    assert capsys.readouterr().out == ""
    records = [json.loads(line) for line in lines]
    assert [(r["account"], r["user_id"], r["status"]) for r in records] == [
        ("tenant", "alice", 200),
        (None, None, 401),
    ]
    assert all(0 <= time.time() - r["time"] < 10 and r["event"] == "request" for r in records)
    for secret in (issued["key"], "guessed-secret", "user-key", "model-secret", "private prompt"):
        assert secret not in path.read_text()


def test_file_sink_rotates(tmp_path):
    from openviking_gateway.sinks import FileSink

    path = tmp_path / "requests.jsonl"
    sink = FileSink(str(path), max_bytes=200)
    for index in range(40):
        sink.write({"index": index, "padding": "x" * 40})
    sink.close()
    files = sorted(p.name for p in tmp_path.iterdir())
    # Four old files are kept beside the current one.
    assert files == [f"requests.jsonl{suffix}" for suffix in ("", ".1", ".2", ".3", ".4")]
    assert all(p.stat().st_size <= 200 for p in tmp_path.iterdir())
    assert json.loads(path.read_text().splitlines()[-1])["index"] == 39


async def test_full_queue_drops_and_counts(tmp_path, monkeypatch):
    from openviking_gateway import sinks

    monkeypatch.setattr(sinks, "QUEUE_SIZE", 3)
    stats = sinks.Stats()
    sink = sinks.FileSink(str(tmp_path / "requests.jsonl"), max_bytes=1 << 20, stats=stats)
    # With the writer thread stopped, nothing leaves the queue.
    sink.listener.stop()
    for index in range(5):
        await sink.log("tenant", {"index": index})
    assert sink.dropped == 2 and stats.records_dropped == 2
    sink.close()


def test_file_sink_requires_a_path():
    from pydantic import ValidationError

    from openviking_gateway.config import GatewayConfig

    with pytest.raises(ValidationError, match="requires request_log_path"):
        GatewayConfig(request_log_sink="file")
    assert GatewayConfig(request_log_sink="file", request_log_path="/tmp/r.jsonl")
    with pytest.raises(ValidationError):
        GatewayConfig(request_log_sink="file", request_log_path="r", request_log_max_bytes=1024)


@pytest.mark.parametrize(
    "settings",
    [{"request_log_sink": "file", "request_log_path": "requests.jsonl"}, {"log_path": "gw.log"}],
)
def test_file_sink_rejects_several_workers(settings):
    from pydantic import ValidationError

    from openviking_gateway.config import StandaloneGatewayConfig

    base = {"openviking_url": "http://ov.test", **settings}
    assert StandaloneGatewayConfig(**base, workers=1)
    with pytest.raises(ValidationError, match="require workers: 1"):
        StandaloneGatewayConfig(**base, workers=2)
