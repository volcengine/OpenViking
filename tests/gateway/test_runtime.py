"""Capture runs concurrently and drains before gateway resources close."""

import asyncio

import pytest
from conftest import make_due, serve_gateway

from openviking_gateway import app as app_module


@pytest.fixture
def gateway_config():
    return {"capture_concurrency": 2, "health_interval_seconds": 1}


async def queue_conversation(gateway, session):
    app, client, _, minted, _, _ = gateway
    response = await client.post(
        "/v1/chat/completions",
        headers={
            "Authorization": "Bearer " + minted["key"],
            "X-OpenViking-Session": session,
            "X-OpenViking-Context": '{"recall":false,"profile":false}',
        },
        json={"model": "model", "messages": [{"role": "user", "content": session}]},
    )
    assert response.status_code == 200, response.text
    await make_due(app.state.store)


async def test_two_commits_are_in_flight_together(running_gateway):
    app = running_gateway[0]
    entered, release = asyncio.Event(), asyncio.Event()
    health = asyncio.Event()
    sessions = set()

    async def handler(request):
        if request.path == "/health" and not request.headers.get("X-API-Key") and entered.is_set():
            health.set()
        if request.path.endswith("/commit"):
            sessions.add(request.path.split("/")[4])
            if len(sessions) == 2:
                entered.set()
            await release.wait()

    app.state.test_backend["handler"] = handler
    try:
        await queue_conversation(running_gateway, "first")
        await queue_conversation(running_gateway, "second")
        await asyncio.wait_for(entered.wait(), 3)
        await asyncio.wait_for(health.wait(), 3)
        assert len(sessions) == 2
        assert len(running_gateway[5]) == 2
    finally:
        release.set()


@pytest.mark.parametrize("stuck", [False, True], ids=["drain", "timeout"])
async def test_shutdown_stops_claiming_and_closes_after_delivery(
    tmp_path, gateway_backend, gateway_config, monkeypatch, stuck
):
    entered, release = asyncio.Event(), asyncio.Event()
    commits, cancelled, claims = [], [], []
    completed = []
    monkeypatch.setattr(app_module, "CAPTURE_DRAIN_SECONDS", 0.02 if stuck else 3)
    if not stuck:

        async def release_on_shutdown(*args):
            # Shutdown cancels the health loop before it waits on captures, so delivery
            # finishes only once draining has started.
            try:
                await asyncio.Event().wait()
            finally:
                release.set()

        monkeypatch.setattr(app_module, "health_loop", release_on_shutdown)

    async def handler(request):
        if request.path.endswith("/commit"):
            commits.append(request.path.split("/")[4])
            if len(commits) == 2:
                entered.set()
            await release.wait()

    try:
        async with serve_gateway(tmp_path, gateway_backend, gateway_config) as gateway:
            app = gateway[0]
            commit, claim = app.state.viking.commit, app.state.store.capture.claim

            async def observed_commit(*args, **kwargs):
                try:
                    result = await commit(*args, **kwargs)
                    completed.append(args[1])
                    return result
                except asyncio.CancelledError:
                    cancelled.append(args[1])
                    raise

            async def observed_claim(*args, **kwargs):
                claims.append(True)
                return await claim(*args, **kwargs)

            app.state.test_backend["handler"] = handler
            monkeypatch.setattr(app.state.viking, "commit", observed_commit)
            monkeypatch.setattr(app.state.store.capture, "claim", observed_claim)
            await queue_conversation(gateway, "first")
            await queue_conversation(gateway, "second")
            await asyncio.wait_for(entered.wait(), 3)
            await queue_conversation(gateway, "unclaimed")
            claims_before_shutdown = len(claims)
    finally:
        release.set()
    assert app.state.http.closed
    assert len(commits) == 2
    assert len(claims) == claims_before_shutdown
    assert len(cancelled) == (2 if stuck else 0)
    assert len(completed) == (0 if stuck else 2)
