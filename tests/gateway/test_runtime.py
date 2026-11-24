"""Requests save their turns concurrently, and gateway resources close on shutdown."""

import asyncio
import json

import async_timeout
import pytest
from conftest import serve_gateway

from openviking_gateway import app as app_module
from openviking_gateway import capture


@pytest.fixture
def gateway_config():
    return {"health_interval_seconds": 1}


async def converse(gateway, session, key=None, idle_seconds=600):
    _, client, _, minted, _, _ = gateway
    response = await client.post(
        "/v1/chat/completions",
        headers={
            "Authorization": "Bearer " + (key or minted["key"]),
            "X-OpenViking-Session": session,
            "X-OpenViking-Context": json.dumps(
                {"recall": False, "profile": False, "idle_seconds": idle_seconds}
            ),
        },
        json={"model": "model", "messages": [{"role": "user", "content": session}]},
    )
    assert response.status_code == 200, response.text


async def test_two_saves_are_in_flight_together(running_gateway):
    app = running_gateway[0]
    entered, release = asyncio.Event(), asyncio.Event()
    health = asyncio.Event()
    sessions = set()

    async def handler(request):
        if request.path == "/health" and not request.headers.get("X-API-Key") and entered.is_set():
            health.set()
        if request.path.endswith("/messages/batch"):
            sessions.add(request.path.split("/")[4])
            if len(sessions) == 2:
                entered.set()
            await release.wait()

    app.state.test_backend["handler"] = handler
    requests = [
        asyncio.create_task(converse(running_gateway, session)) for session in ("first", "second")
    ]
    try:
        await asyncio.wait_for(entered.wait(), 3)
        await asyncio.wait_for(health.wait(), 3)
    finally:
        release.set()
        await asyncio.gather(*requests)
    assert len(sessions) == 2
    assert len(running_gateway[5]) == 2


async def test_idle_commit_due_while_stopped_runs_after_a_restart(
    tmp_path, gateway_backend, gateway_config, monkeypatch
):
    monkeypatch.setattr(capture, "SWEEP_SECONDS", 0.05)
    monkeypatch.setattr(app_module, "SWEEP_SECONDS", 0.05)
    async with serve_gateway(tmp_path, gateway_backend, gateway_config) as gateway:
        app = gateway[0]
        key = gateway[3]["key"]
        await converse(gateway, "first", idle_seconds=1)
        assert gateway[5]
    # The gateway stopped before the idle time passed: nothing was committed.
    assert app.state.http.closed and "commits" not in app.state.test_backend
    async with serve_gateway(tmp_path, gateway_backend, gateway_config) as gateway:
        backend = gateway[0].state.test_backend
        async with async_timeout.timeout(5):
            while "commits" not in backend:
                await asyncio.sleep(0.05)
        ((_, commit),) = backend["commits"]
        assert commit["keep_recent_count"] == 0
        # Once committed, only a conversation with a later turn is still waiting.
        await converse(gateway, "second", key=key)
        assert len(backend["commits"]) == 1
        assert len(await gateway[0].state.store.capture.waiting(float("inf"))) == 1


async def test_idle_commit_waiting_at_the_switch_to_openviking_is_still_made(
    tmp_path, gateway_backend, gateway_config, monkeypatch
):
    monkeypatch.setattr(capture, "SWEEP_SECONDS", 0.05)
    monkeypatch.setattr(app_module, "SWEEP_SECONDS", 0.05)
    async with serve_gateway(tmp_path, gateway_backend, gateway_config) as gateway:
        await converse(gateway, "first", idle_seconds=1)
        (created,) = gateway[0].state.test_backend["sessions"]
    # OpenViking does not commit this session: its conversation never saves again.
    assert not any(created["auto_commit_policy"].values())
    config = {**gateway_config, "openviking_auto_commit": True}
    async with serve_gateway(tmp_path, gateway_backend, config) as gateway:
        backend = gateway[0].state.test_backend
        async with async_timeout.timeout(5):
            while "commits" not in backend:
                await asyncio.sleep(0.05)
        ((_, commit),) = backend["commits"]
        assert commit["keep_recent_count"] == 0


async def test_openviking_commits_by_itself_when_configured(
    tmp_path, gateway_backend, gateway_config
):
    config = {**gateway_config, "openviking_auto_commit": True}
    async with serve_gateway(tmp_path, gateway_backend, config) as gateway:
        app = gateway[0]
        await converse(gateway, "first")
        (created,) = app.state.test_backend["sessions"]
        assert created["auto_commit_policy"] == {
            "pending_token_threshold": 20000,
            "message_count_threshold": 0,
            "idle_timeout_seconds": 600,
            "keep_recent_count": 10,
        }
    assert "commits" not in app.state.test_backend
