"""The status line each process writes every snapshot_interval_seconds."""

import asyncio
import json
import logging
import time
from types import SimpleNamespace

import async_timeout
import pytest
from aiohttp import web
from conftest import serve_gateway

from openviking_gateway.sinks import write_snapshot

CHAT = "/v1/chat/completions"
BODY = {"model": "model", "messages": [{"role": "user", "content": "private prompt"}]}
FIELDS = {
    "event",
    "schema",
    "time",
    "interval_s",
    "uptime_s",
    "version",
    "inflight_requests",
    "inflight_streams",
    "requests",
    "errors",
    "degradations",
    "stream_errors",
    "rejected_overload",
    "records_unlogged",
    "records_failed",
    "records_dropped",
    "store_errors",
    "openviking",
    "capture",
}


def auth(key):
    return {"Authorization": f"Bearer {key}", "X-OpenViking-Session": "private-session"}


async def test_snapshot_line_has_the_documented_fields(tmp_path, gateway_backend):
    async with serve_gateway(tmp_path, gateway_backend, {}) as gateway:
        app, client, _, issued, _, _ = gateway
        release = asyncio.Event()

        async def backend(request):
            if request.path == CHAT and (await request.json()).get("stream"):
                response = web.StreamResponse(headers={"Content-Type": "text/event-stream"})
                await response.prepare(request)
                await response.write(b'data: {"choices":[{"delta":{"content":"hi"}}]}\n\n')
                await release.wait()
                await response.write(b"data: [DONE]\n\n")
                return response
            return None

        app.state.test_backend["handler"] = backend
        assert (await client.post(CHAT, headers=auth(issued["key"]), json=BODY)).status_code == 200
        assert (await client.post(CHAT, headers=auth("ovgw_guess"), json=BODY)).status_code == 401
        stream = asyncio.create_task(
            client.post(CHAT, headers=auth(issued["key"]), json={**BODY, "stream": True})
        )
        async with async_timeout.timeout(5):
            while app.state.stats.inflight_streams == 0:
                await asyncio.sleep(0.01)
        line = await app.state.stats.snapshot(app)
        release.set()
        assert (await stream).status_code == 200

        assert set(line) == FIELDS
        assert line["event"] == "snapshot" and line["schema"] == 1
        assert 0 <= time.time() - line["time"] < 10
        assert line["interval_s"] > 0 and line["uptime_s"] >= line["interval_s"]
        assert line["inflight_requests"] == 1 and line["inflight_streams"] == 1
        assert line["requests"] == 2
        assert line["errors"] == {"gateway_key_invalid": 1}
        # The store sink keeps no record without an account.
        assert line["records_unlogged"] == 1
        assert line["records_failed"] == line["records_dropped"] == line["store_errors"] == 0
        assert line["openviking"]["state"] in {"ok", "starting"}
        assert line["openviking"]["open_circuits"] == 0
        assert set(line["capture"]) == {"delivered", "failed"}
        assert line["capture"]["failed"] == 0
        stats = app.state.stats
        assert stats.inflight_requests == stats.inflight_streams == 0


async def test_snapshot_counts_reset_each_interval(tmp_path, gateway_backend):
    async with serve_gateway(tmp_path, gateway_backend, {}) as gateway:
        app, client, _, issued, _, _ = gateway

        async def broken(request):
            raise RuntimeError("store down")

        await client.post(CHAT, headers=auth("ovgw_guess"), json=BODY)
        first = await app.state.stats.snapshot(app)
        app.state.management.log = broken
        await client.post(CHAT, headers=auth(issued["key"]), json=BODY)
        second = await app.state.stats.snapshot(app)
        third = await app.state.stats.snapshot(app)
    assert first["requests"] == 1 and first["errors"] == {"gateway_key_invalid": 1}
    assert second["requests"] == 1 and second["errors"] == {}
    assert second["records_failed"] == 1 and second["records_unlogged"] == 0
    assert third["requests"] == 0 and third["errors"] == {} and third["records_failed"] == 0
    assert third["uptime_s"] > first["uptime_s"]


def snapshot_lines(text):
    lines = [json.loads(line) for line in text.splitlines() if '"snapshot"' in line]
    return [line for line in lines if line.get("event") == "snapshot"]


@pytest.mark.parametrize("sink", ["store", "stdout", "file"])
async def test_snapshot_carries_no_tenant_fields(tmp_path, gateway_backend, capsys, caplog, sink):
    path = tmp_path / "requests.jsonl"
    config = {
        "request_log_sink": sink,
        "request_log_path": str(path) if sink == "file" else "",
        "snapshot_interval_seconds": 0.05,
    }
    caplog.set_level(logging.INFO, logger="openviking_gateway.snapshot")
    async with serve_gateway(tmp_path, gateway_backend, config) as gateway:
        app, client, _, issued, _, _ = gateway
        assert (await client.post(CHAT, headers=auth(issued["key"]), json=BODY)).status_code == 200
        await asyncio.sleep(0.3)
    if sink == "file":
        text = path.read_text()
    elif sink == "stdout":
        text = capsys.readouterr().out
    else:
        text = "\n".join(r.getMessage() for r in caplog.records if r.name.endswith("snapshot"))
    lines = snapshot_lines(text)
    assert len(lines) >= 2
    assert sum(line["requests"] for line in lines) == 1
    for line in lines:
        assert set(line) == FIELDS
        flat = json.dumps(line)
        for value in ("tenant", "alice", "private-session", "model", issued["key"], "user-key"):
            assert value not in flat
        assert not {"account", "user_id", "session", "request_id"} & set(line)


def test_store_sink_status_line_is_written_without_logging_setup(monkeypatch, capsys):
    # Under uvicorn's default config the root logger has no handler.
    monkeypatch.setattr(logging.root, "handlers", [])
    app = SimpleNamespace(state=SimpleNamespace(config=SimpleNamespace(request_log_sink="store")))
    write_snapshot(app, {"event": "snapshot", "requests": 3})
    lines = snapshot_lines(capsys.readouterr().err)
    assert lines == [{"event": "snapshot", "requests": 3}]


async def test_no_snapshot_by_default(tmp_path, gateway_backend, capsys):
    async with serve_gateway(tmp_path, gateway_backend, {"request_log_sink": "stdout"}) as gateway:
        app, client, _, issued, _, _ = gateway
        assert (await client.post(CHAT, headers=auth(issued["key"]), json=BODY)).status_code == 200
        running = {task.get_coro().__name__ for task in asyncio.all_tasks()}
    assert "snapshot_loop" not in running
    assert snapshot_lines(capsys.readouterr().out) == []


async def save_turn(gateway, session):
    _, client, _, issued, _, _ = gateway
    headers = {**auth(issued["key"]), "X-OpenViking-Session": session}
    assert (await client.post(CHAT, headers=headers, json=BODY)).status_code == 200


async def counted(stats, name):
    async with async_timeout.timeout(5):
        while not getattr(stats, name):
            await asyncio.sleep(0.01)


async def test_snapshot_counts_deliveries_and_failed_attempts(tmp_path, gateway_backend):
    async with serve_gateway(tmp_path, gateway_backend, {}) as gateway:
        app, stats = gateway[0], gateway[0].state.stats
        await save_turn(gateway, "saved")
        await counted(stats, "capture_delivered")
        delivered = await stats.snapshot(app)
        after = await stats.snapshot(app)

        async def unavailable(request):
            if request.path.endswith("/messages/batch"):
                return web.json_response({"error": "down"}, status=503)
            return None

        app.state.test_backend["handler"] = unavailable
        await save_turn(gateway, "stuck")
        await counted(stats, "capture_failed")
        failed = await stats.snapshot(app)
    assert delivered["capture"]["delivered"] == 1 and delivered["capture"]["failed"] == 0
    assert after["capture"]["delivered"] == after["capture"]["failed"] == 0
    assert failed["capture"]["failed"] == 1 and failed["capture"]["delivered"] == 0
