# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: Apache-2.0

"""Tests for async session commit support."""

import asyncio
import time
from typing import AsyncGenerator, Tuple

import httpx
import pytest_asyncio

from openviking import AsyncOpenViking
from openviking.message import TextPart
from openviking.server.app import create_app
from openviking.server.config import ServerConfig
from openviking.server.dependencies import set_service
from openviking.service.core import OpenVikingService


@pytest_asyncio.fixture
async def api_client(temp_dir) -> AsyncGenerator[Tuple[httpx.AsyncClient, OpenVikingService], None]:
    """Create in-process HTTP client for API endpoint tests."""
    service = OpenVikingService(path=str(temp_dir / "api_data"))
    await service.initialize()
    app = create_app(config=ServerConfig(), service=service)
    set_service(service)

    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://testserver") as client:
        yield client, service

    await service.close()
    await AsyncOpenViking.reset()


@pytest_asyncio.fixture
async def ov_client(temp_dir) -> AsyncGenerator[AsyncOpenViking, None]:
    """Create AsyncOpenViking client for unit tests."""
    client = AsyncOpenViking(path=str(temp_dir / "ov_data"))
    await client.initialize()
    yield client
    await client.close()
    await AsyncOpenViking.reset()


async def _new_session_with_one_message(client: httpx.AsyncClient) -> str:
    create_resp = await client.post("/api/v1/sessions", json={})
    assert create_resp.status_code == 200
    session_id = create_resp.json()["result"]["session_id"]

    add_resp = await client.post(
        f"/api/v1/sessions/{session_id}/messages",
        json={"role": "user", "content": "hello"},
    )
    assert add_resp.status_code == 200
    return session_id


async def test_commit_async_returns_same_shape_as_commit(ov_client: AsyncOpenViking):
    """commit_async should keep result schema compatible with commit."""
    session = ov_client.session(session_id="async-shape-test")
    session.add_message("user", [TextPart("first")])
    sync_result = session.commit()

    session.add_message("user", [TextPart("second")])
    async_result = await session.commit_async()

    assert set(sync_result.keys()) == set(async_result.keys())
    assert async_result["status"] == "committed"


async def test_commit_endpoint_wait_false_returns_accepted_immediately(api_client):
    """wait=false should return immediately and run commit in background."""
    client, service = api_client
    session_id = await _new_session_with_one_message(client)

    done = asyncio.Event()

    async def fake_commit_async(_sid, _ctx):
        await asyncio.sleep(0.2)
        done.set()
        return {"session_id": _sid, "status": "committed", "memories_extracted": 0}

    service.sessions.commit_async = fake_commit_async  # type: ignore[method-assign]

    start = time.perf_counter()
    resp = await client.post(f"/api/v1/sessions/{session_id}/commit", params={"wait": False})
    elapsed = time.perf_counter() - start

    assert resp.status_code == 200
    body = resp.json()
    assert body["status"] == "ok"
    assert body["result"]["status"] == "accepted"
    assert elapsed < 0.15

    await asyncio.wait_for(done.wait(), timeout=1.0)


async def test_commit_endpoint_wait_true_waits_for_result(api_client):
    """wait=true should wait and return full commit result."""
    client, service = api_client
    session_id = await _new_session_with_one_message(client)

    async def fake_commit_async(_sid, _ctx):
        await asyncio.sleep(0.05)
        return {
            "session_id": _sid,
            "status": "committed",
            "memories_extracted": 2,
            "active_count_updated": 1,
            "archived": True,
            "stats": {},
        }

    service.sessions.commit_async = fake_commit_async  # type: ignore[method-assign]

    resp = await client.post(f"/api/v1/sessions/{session_id}/commit", params={"wait": True})
    assert resp.status_code == 200
    body = resp.json()
    assert body["status"] == "ok"
    assert body["result"]["status"] == "committed"
    assert body["result"]["memories_extracted"] == 2


async def test_commit_endpoint_default_wait_true_backward_compatible(api_client):
    """No wait param should behave like previous blocking commit API."""
    client, service = api_client
    session_id = await _new_session_with_one_message(client)

    async def fake_commit_async(_sid, _ctx):
        return {"session_id": _sid, "status": "committed", "memories_extracted": 1}

    service.sessions.commit_async = fake_commit_async  # type: ignore[method-assign]

    resp = await client.post(f"/api/v1/sessions/{session_id}/commit")
    assert resp.status_code == 200
    body = resp.json()
    assert body["status"] == "ok"
    assert body["result"]["status"] == "committed"
