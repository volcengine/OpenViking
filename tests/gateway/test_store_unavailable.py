"""A failing state store answers 503 store_unavailable, never a plain-text 500."""

import json
import sqlite3

import pytest
from aiohttp import web
from test_app import enable_tools
from test_native_tools import PATHS, native_response, request_body, wire_response

from openviking_gateway.records import RecordKind as K

CHAT = {"model": "model", "messages": [{"role": "user", "content": "Hello there"}]}


@pytest.fixture
def gateway_config():
    return {"request_log_sink": "stdout"}


def bearer(issued):
    return {"Authorization": "Bearer " + issued["key"]}


def only_record(capsys):
    lines = capsys.readouterr().out.splitlines()
    found = [value for line in lines if (value := json.loads(line)).get("event") == "request"]
    assert len(found) == 1, found
    return found[0]


def fail(monkeypatch, target, name):
    async def broken(*args, **kwargs):
        raise sqlite3.OperationalError("database is locked")

    monkeypatch.setattr(target, name, broken)


def assert_store_unavailable(response, attempted="0"):
    assert response.status_code == 503
    assert response.headers["content-type"] == "application/json"
    assert response.json()["reason"] == "store_unavailable"
    assert response.headers["retry-after"] == "1"
    assert response.headers["x-openviking-error-reason"] == "store_unavailable"
    assert response.headers["x-openviking-upstream-attempted"] == attempted


async def test_store_error_during_authentication_is_json_503(running_gateway, capsys, monkeypatch):
    app, client, _, issued, captured, _ = running_gateway
    app.state.management.cache.clear()
    fail(monkeypatch, app.state.management.port, "credential")
    capsys.readouterr()
    response = await client.post("/v1/chat/completions", headers=bearer(issued), json=CHAT)
    assert_store_unavailable(response)
    assert "database is locked" not in response.text and not captured
    record = only_record(capsys)
    assert record["status"] == 503 and record["error_reason"] == "store_unavailable"
    assert record["error_origin"] == "gateway" and record["error_class"] == "gateway_internal"
    assert record["upstream_attempted"] is False and record["account"] is None


async def test_store_error_during_binding_is_json_503(running_gateway, capsys, monkeypatch):
    app, client, _, issued, captured, _ = running_gateway
    fail(monkeypatch, app.state.management.port, "list")
    capsys.readouterr()
    response = await client.post("/v1/chat/completions", headers=bearer(issued), json=CHAT)
    assert_store_unavailable(response)
    assert not captured
    record = only_record(capsys)
    assert record["error_reason"] == "store_unavailable" and record["account"] == "tenant"


async def test_redis_cluster_error_is_store_unavailable(running_gateway, capsys, monkeypatch):
    exceptions = pytest.importorskip("redis.exceptions")
    app, client, _, issued, captured, _ = running_gateway
    app.state.management.cache.clear()

    async def broken(*args, **kwargs):
        raise exceptions.RedisClusterException("Redis Cluster cannot be connected")

    monkeypatch.setattr(app.state.management.port, "credential", broken)
    capsys.readouterr()
    response = await client.post("/v1/chat/completions", headers=bearer(issued), json=CHAT)
    assert_store_unavailable(response)
    assert not captured
    record = only_record(capsys)
    assert record["error_reason"] == "store_unavailable"


async def test_confirmed_revocation_still_returns_401_when_the_store_fails(
    running_gateway, monkeypatch
):
    app, client, _, issued, captured, _ = running_gateway
    app.state.test_backend["health_status"] = {"user-key": 401}
    app.state.viking.identities.clear()
    fail(monkeypatch, app.state.management.port, "delete")
    response = await client.post("/v1/chat/completions", headers=bearer(issued), json=CHAT)
    assert response.status_code == 401
    assert response.json()["reason"] == "gateway_key_invalid"
    assert not captured


async def test_store_error_after_the_upstream_call_reports_attempted_1(
    running_gateway, capsys, monkeypatch
):
    app, client, admin, issued, _, _ = running_gateway
    await enable_tools(client, admin, show_tool_calls=False)
    protocol, calls = "responses", []

    async def backend(request):
        if request.path == "/mcp":
            return web.json_response({"id": 1, "result": {"content": []}})
        if request.path != PATHS[protocol]:
            return None
        calls.append(await request.json())
        value = native_response(protocol, len(calls), owned=len(calls) == 1)
        return await wire_response(request, protocol, value, False)

    app.state.test_backend["handler"] = backend
    replay, put = app.state.store.replay, app.state.store.replay.put

    async def put_failing_transcripts(scope, session, kind, *args, **kwargs):
        if kind == K.HIDDEN:
            raise sqlite3.OperationalError("disk I/O error")
        return await put(scope, session, kind, *args, **kwargs)

    monkeypatch.setattr(replay, "put", put_failing_transcripts)
    capsys.readouterr()
    response = await client.post(
        PATHS[protocol], headers=bearer(issued), json=request_body(protocol, False)
    )
    assert len(calls) == 2
    assert_store_unavailable(response, attempted="1")
    record = only_record(capsys)
    assert record["error_reason"] == "store_unavailable" and record["upstream_attempted"] is True


@pytest.mark.parametrize("status", [402, 403])
async def test_openviking_refusals_still_win(running_gateway, monkeypatch, status):
    app, client, _, issued, captured, _ = running_gateway
    app.state.test_backend["health_status"] = {"user-key": status}
    app.state.viking.identities.clear()
    # The store fails on the next step after the identity check.
    fail(monkeypatch, app.state.management.port, "list")
    response = await client.post("/v1/chat/completions", headers=bearer(issued), json=CHAT)
    assert response.status_code == status
    assert response.json()["reason"] == (
        "openviking_not_ordered" if status == 402 else "openviking_forbidden"
    )
    assert not captured
