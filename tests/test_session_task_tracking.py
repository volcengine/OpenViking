# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: Apache-2.0

"""Integration tests for session commit task tracking via HTTP API."""

import asyncio
from typing import AsyncGenerator, Tuple

import httpx
import pytest_asyncio

from openviking import AsyncOpenViking
from openviking.server.app import create_app
from openviking.server.config import ServerConfig
from openviking.server.dependencies import set_service
from openviking.service.core import OpenVikingService
from openviking.service.task_tracker import reset_task_tracker


@pytest_asyncio.fixture
async def api_client(temp_dir) -> AsyncGenerator[Tuple[httpx.AsyncClient, OpenVikingService], None]:
    """Create in-process HTTP client for API endpoint tests."""
    reset_task_tracker()
    service = OpenVikingService(path=str(temp_dir / "api_data"))
    await service.initialize()
    app = create_app(config=ServerConfig(), service=service)
    set_service(service)

    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://testserver") as client:
        yield client, service

    await service.close()
    await AsyncOpenViking.reset()
    reset_task_tracker()


async def _new_session_with_message(client: httpx.AsyncClient) -> str:
    resp = await client.post("/api/v1/sessions", json={})
    assert resp.status_code == 200
    session_id = resp.json()["result"]["session_id"]
    await client.post(
        f"/api/v1/sessions/{session_id}/messages",
        json={"role": "user", "content": "hello world"},
    )
    return session_id


# ── wait=false returns task_id ──


async def test_commit_wait_false_returns_task_id(api_client):
    """wait=false should return a task_id for polling."""
    client, service = api_client
    session_id = await _new_session_with_message(client)

    done = asyncio.Event()

    async def fake_commit(_sid, _ctx):
        await asyncio.sleep(0.1)
        done.set()
        return {"session_id": _sid, "status": "committed", "memories_extracted": 0}

    service.sessions.commit_async = fake_commit

    resp = await client.post(f"/api/v1/sessions/{session_id}/commit", params={"wait": False})
    assert resp.status_code == 200
    body = resp.json()
    assert body["result"]["status"] == "accepted"
    assert "task_id" in body["result"]

    await asyncio.wait_for(done.wait(), timeout=2.0)


async def test_commit_wait_false_rejects_full_telemetry(api_client):
    """wait=false should reject telemetry payload requests."""
    client, _ = api_client
    session_id = await _new_session_with_message(client)

    resp = await client.post(
        f"/api/v1/sessions/{session_id}/commit",
        params={"wait": False},
        json={"telemetry": True},
    )
    assert resp.status_code == 400
    body = resp.json()
    assert body["status"] == "error"
    assert body["error"]["code"] == "INVALID_ARGUMENT"
    assert "wait=false" in body["error"]["message"]


async def test_commit_wait_false_rejects_summary_only_telemetry(api_client):
    """wait=false should also reject summary-only telemetry requests."""
    client, _ = api_client
    session_id = await _new_session_with_message(client)

    resp = await client.post(
        f"/api/v1/sessions/{session_id}/commit",
        params={"wait": False},
        json={"telemetry": {"summary": True}},
    )
    assert resp.status_code == 400
    body = resp.json()
    assert body["status"] == "error"
    assert body["error"]["code"] == "INVALID_ARGUMENT"
    assert "wait=false" in body["error"]["message"]


# ── Task lifecycle: pending → running → completed ──


async def test_task_lifecycle_success(api_client):
    """Task should transition pending→running→completed on success."""
    client, service = api_client
    session_id = await _new_session_with_message(client)

    commit_started = asyncio.Event()
    commit_gate = asyncio.Event()

    async def gated_commit(_sid, _ctx):
        commit_started.set()
        await commit_gate.wait()
        return {"session_id": _sid, "status": "committed", "memories_extracted": 5}

    service.sessions.commit_async = gated_commit

    # Fire background commit
    resp = await client.post(f"/api/v1/sessions/{session_id}/commit", params={"wait": False})
    task_id = resp.json()["result"]["task_id"]

    # Wait for commit to start
    await asyncio.wait_for(commit_started.wait(), timeout=2.0)

    # Task should be running
    task_resp = await client.get(f"/api/v1/tasks/{task_id}")
    assert task_resp.status_code == 200
    assert task_resp.json()["result"]["status"] == "running"

    # Release the commit
    commit_gate.set()
    await asyncio.sleep(0.1)

    # Task should be completed
    task_resp = await client.get(f"/api/v1/tasks/{task_id}")
    assert task_resp.status_code == 200
    result = task_resp.json()["result"]
    assert result["status"] == "completed"
    assert result["result"]["memories_extracted"] == 5


# ── Task lifecycle: pending → running → failed ──


async def test_task_lifecycle_failure(api_client):
    """Task should transition to failed on commit error."""
    client, service = api_client
    session_id = await _new_session_with_message(client)

    async def failing_commit(_sid, _ctx):
        raise RuntimeError("LLM provider timeout")

    service.sessions.commit_async = failing_commit

    resp = await client.post(f"/api/v1/sessions/{session_id}/commit", params={"wait": False})
    task_id = resp.json()["result"]["task_id"]

    await asyncio.sleep(0.2)

    task_resp = await client.get(f"/api/v1/tasks/{task_id}")
    assert task_resp.status_code == 200
    result = task_resp.json()["result"]
    assert result["status"] == "failed"
    assert "LLM provider timeout" in result["error"]


async def test_task_failed_when_memory_extraction_raises(api_client):
    """Extractor failures should propagate to task error instead of silent completed+0."""
    client, service = api_client
    session_id = await _new_session_with_message(client)

    async def failing_extract(_context, _user, _session_id):
        raise RuntimeError("memory_extraction_failed: synthetic extractor error")

    service.sessions._session_compressor.extractor.extract = failing_extract

    resp = await client.post(f"/api/v1/sessions/{session_id}/commit", params={"wait": False})
    task_id = resp.json()["result"]["task_id"]

    result = None
    for _ in range(120):
        await asyncio.sleep(0.1)
        task_resp = await client.get(f"/api/v1/tasks/{task_id}")
        assert task_resp.status_code == 200
        result = task_resp.json()["result"]
        if result["status"] in {"completed", "failed"}:
            break

    assert result is not None
    assert result["status"] in {"completed", "failed"}
    assert result["status"] == "failed"
    assert "memory_extraction_failed" in result["error"]


# ── Duplicate commit rejection ──


async def test_duplicate_commit_rejected(api_client):
    """Second commit on same session should be rejected while first is running."""
    client, service = api_client
    session_id = await _new_session_with_message(client)

    gate = asyncio.Event()

    async def slow_commit(_sid, _ctx):
        await gate.wait()
        return {"session_id": _sid, "status": "committed", "memories_extracted": 0}

    service.sessions.commit_async = slow_commit

    # First commit
    resp1 = await client.post(f"/api/v1/sessions/{session_id}/commit", params={"wait": False})
    assert resp1.json()["result"]["status"] == "accepted"

    # Second commit should be rejected
    resp2 = await client.post(f"/api/v1/sessions/{session_id}/commit", params={"wait": False})
    assert resp2.json()["status"] == "error"
    assert "already has a commit in progress" in resp2.json()["error"]["message"]

    gate.set()
    await asyncio.sleep(0.1)


async def test_wait_true_rejected_while_background_commit_running(api_client):
    """wait=true must also reject duplicate commits for the same session."""
    client, service = api_client
    session_id = await _new_session_with_message(client)

    gate = asyncio.Event()

    async def slow_commit(_sid, _ctx):
        await gate.wait()
        return {"session_id": _sid, "status": "committed", "memories_extracted": 0}

    service.sessions.commit_async = slow_commit

    resp1 = await client.post(f"/api/v1/sessions/{session_id}/commit", params={"wait": False})
    assert resp1.json()["result"]["status"] == "accepted"

    resp2 = await client.post(
        f"/api/v1/sessions/{session_id}/commit",
        params={"wait": True},
        json={"telemetry": True},
    )
    assert resp2.status_code == 200
    assert resp2.json()["status"] == "error"
    assert "already has a commit in progress" in resp2.json()["error"]["message"]

    gate.set()
    await asyncio.sleep(0.1)


# ── GET /tasks/{id} 404 ──


async def test_get_nonexistent_task_returns_404(api_client):
    client, _ = api_client
    resp = await client.get("/api/v1/tasks/nonexistent-id")
    assert resp.status_code == 404


# ── GET /tasks list ──


async def test_list_tasks(api_client):
    client, service = api_client
    session_id = await _new_session_with_message(client)

    async def instant_commit(_sid, _ctx):
        return {"session_id": _sid, "status": "committed", "memories_extracted": 0}

    service.sessions.commit_async = instant_commit

    await client.post(f"/api/v1/sessions/{session_id}/commit", params={"wait": False})
    await asyncio.sleep(0.1)

    resp = await client.get("/api/v1/tasks", params={"task_type": "session_commit"})
    assert resp.status_code == 200
    tasks = resp.json()["result"]
    assert len(tasks) >= 1
    assert tasks[0]["task_type"] == "session_commit"


async def test_list_tasks_filter_status(api_client):
    client, service = api_client

    async def instant_commit(_sid, _ctx):
        return {"session_id": _sid, "status": "committed", "memories_extracted": 0}

    service.sessions.commit_async = instant_commit

    session_id = await _new_session_with_message(client)
    await client.post(f"/api/v1/sessions/{session_id}/commit", params={"wait": False})
    await asyncio.sleep(0.1)

    # completed tasks
    resp = await client.get("/api/v1/tasks", params={"status": "completed"})
    assert resp.status_code == 200
    for t in resp.json()["result"]:
        assert t["status"] == "completed"


# ── wait=true still works (backward compat) ──


async def test_wait_true_still_works(api_client):
    """wait=true should return inline result, no task_id."""
    client, service = api_client
    session_id = await _new_session_with_message(client)

    async def instant_commit(_sid, _ctx):
        return {"session_id": _sid, "status": "committed", "memories_extracted": 2}

    service.sessions.commit_async = instant_commit

    resp = await client.post(f"/api/v1/sessions/{session_id}/commit", params={"wait": True})
    assert resp.status_code == 200
    body = resp.json()
    assert body["result"]["status"] == "committed"
    assert "task_id" not in body["result"]


# ── Error sanitization in task ──


async def test_error_sanitized_in_task(api_client):
    """Errors stored in tasks should have secrets redacted."""
    client, service = api_client
    session_id = await _new_session_with_message(client)

    async def leaky_commit(_sid, _ctx):
        raise RuntimeError("Auth failed with key sk-ant-api03-DAqSsuperSecretKey123")

    service.sessions.commit_async = leaky_commit

    resp = await client.post(f"/api/v1/sessions/{session_id}/commit", params={"wait": False})
    task_id = resp.json()["result"]["task_id"]

    await asyncio.sleep(0.2)

    task_resp = await client.get(f"/api/v1/tasks/{task_id}")
    error = task_resp.json()["result"]["error"]
    assert "superSecretKey" not in error
    assert "[REDACTED]" in error
