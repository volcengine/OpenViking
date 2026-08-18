# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0

"""Tests for async session commit support."""

import asyncio
from typing import AsyncGenerator, Tuple

import httpx
import pytest_asyncio

from openviking.server.app import create_app
from openviking.server.auth.plugins import DevAuthPlugin
from openviking.server.config import ServerConfig
from openviking.server.dependencies import set_service
from openviking.service.core import OpenVikingService
from openviking.service.task_tracker import TaskStatus, get_task_tracker


@pytest_asyncio.fixture
async def api_client(
    service: OpenVikingService,
) -> AsyncGenerator[Tuple[httpx.AsyncClient, OpenVikingService], None]:
    """Create in-process HTTP client for API endpoint tests."""
    app = create_app(config=ServerConfig(), service=service)
    set_service(service)
    app.state.auth_plugin = DevAuthPlugin()

    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://testserver") as client:
        yield client, service

    set_service(None)


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


async def test_commit_endpoint_returns_accepted_with_task_id(api_client):
    """Commit endpoint should return status=accepted with a task_id."""
    client, service = api_client
    session_id = await _new_session_with_one_message(client)

    resp = await client.post(f"/api/v1/sessions/{session_id}/commit")

    assert resp.status_code == 200
    body = resp.json()
    assert body["status"] == "ok"
    assert body["result"]["status"] == "accepted"
    assert "task_id" in body["result"]

    # Wait for background task to finish
    task_id = body["result"]["task_id"]
    if task_id:
        tracker = get_task_tracker()
        for _ in range(300):
            task = await tracker.get(task_id)
            if task and task.status in (TaskStatus.COMPLETED, TaskStatus.FAILED):
                break
            await asyncio.sleep(0.1)
