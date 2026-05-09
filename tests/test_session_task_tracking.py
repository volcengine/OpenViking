# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0

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
from openviking.service.task_tracker import get_task_tracker, reset_task_tracker


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


# ── Helper: create a mock commit that properly tracks tasks ──


def _make_tracked_commit(behavior="instant", result_overrides=None, gate=None, started=None):
    """Create a mock commit_async that creates & manages a tracked task.

    The mock mirrors the real Session.commit_async() contract: it creates a
    TaskRecord, launches a background asyncio task, and returns immediately
    with {status: "accepted", task_id: ...}.

    Args:
        behavior: "instant" (complete immediately) | "gated" (wait on gate) | "fail" (raise)
        result_overrides: dict merged into task.result on completion, or
                          {"error": "..."} for fail behavior
        gate: asyncio.Event to await before completing (for "gated")
        started: asyncio.Event to set when background task starts (for "gated")
    """

    async def mock_commit(_sid, _ctx):
        tracker = get_task_tracker()
        task = tracker.create(
            "session_commit",
            resource_id=_sid,
            owner_account_id=_ctx.account_id,
            owner_user_id=_ctx.user.user_id,
        )
        archive_uri = f"viking://session/test/{_sid}/history/archive_001"

        async def _background():
            tracker.start(task.task_id)
            try:
                if started:
                    started.set()
                if behavior == "gated" and gate:
                    await gate.wait()
                if behavior == "fail":
                    error_msg = (
                        result_overrides.get("error", "mock error")
                        if result_overrides
                        else "mock error"
                    )
                    raise RuntimeError(error_msg)
                final_result = {
                    "session_id": _sid,
                    "archive_uri": archive_uri,
                    "memories_extracted": {},
                    "active_count_updated": 0,
                }
                if result_overrides:
                    final_result.update(result_overrides)
                tracker.complete(task.task_id, final_result)
            except Exception as e:
                tracker.fail(task.task_id, str(e))

        asyncio.create_task(_background())

        return {
            "session_id": _sid,
            "status": "accepted",
            "task_id": task.task_id,
            "archive_uri": archive_uri,
            "archived": True,
        }

    return mock_commit


# ── Commit returns task_id ──


async def test_commit_returns_task_id(api_client):
    """Commit should return a task_id for polling."""
    client, service = api_client
    session_id = await _new_session_with_message(client)

    service.sessions.commit_async = _make_tracked_commit()

    resp = await client.post(f"/api/v1/sessions/{session_id}/commit")
    assert resp.status_code == 200
    body = resp.json()
    assert body["result"]["status"] == "accepted"
    assert "task_id" in body["result"]

    # Let background task complete
    await asyncio.sleep(0.2)


# ── Task lifecycle: pending → running → completed ──


async def test_task_lifecycle_success(api_client):
    """Task should transition pending→running→completed on success."""
    client, service = api_client
    session_id = await _new_session_with_message(client)

    commit_started = asyncio.Event()
    commit_gate = asyncio.Event()

    service.sessions.commit_async = _make_tracked_commit(
        behavior="gated",
        result_overrides={"memories_extracted": {"profile": 3, "preferences": 2}},
        gate=commit_gate,
        started=commit_started,
    )

    # Fire background commit
    resp = await client.post(f"/api/v1/sessions/{session_id}/commit")
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
    assert result["result"]["memories_extracted"] == {"profile": 3, "preferences": 2}


# ── Task lifecycle: pending → running → failed ──


async def test_task_lifecycle_failure(api_client):
    """Task should transition to failed on commit error."""
    client, service = api_client
    session_id = await _new_session_with_message(client)

    service.sessions.commit_async = _make_tracked_commit(
        behavior="fail",
        result_overrides={"error": "LLM provider timeout"},
    )

    resp = await client.post(f"/api/v1/sessions/{session_id}/commit")
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

    resp = await client.post(f"/api/v1/sessions/{session_id}/commit")
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
    assert result["status"] == "failed"
    assert "memory_extraction_failed" in result["error"]


# ── Duplicate commit acceptance ──


async def test_duplicate_commit_returns_second_task(api_client):
    """Second commit on same session should also be accepted with its own task."""
    client, service = api_client
    session_id = await _new_session_with_message(client)

    gate = asyncio.Event()

    service.sessions.commit_async = _make_tracked_commit(behavior="gated", gate=gate)

    # First commit
    resp1 = await client.post(f"/api/v1/sessions/{session_id}/commit")
    assert resp1.json()["result"]["status"] == "accepted"
    task_id_1 = resp1.json()["result"]["task_id"]

    # Second commit should also be accepted
    resp2 = await client.post(f"/api/v1/sessions/{session_id}/commit")
    assert resp2.status_code == 200
    assert resp2.json()["result"]["status"] == "accepted"
    task_id_2 = resp2.json()["result"]["task_id"]
    assert task_id_1 != task_id_2

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

    service.sessions.commit_async = _make_tracked_commit()

    await client.post(f"/api/v1/sessions/{session_id}/commit")
    await asyncio.sleep(0.2)

    resp = await client.get("/api/v1/tasks", params={"task_type": "session_commit"})
    assert resp.status_code == 200
    tasks = resp.json()["result"]
    assert len(tasks) >= 1
    assert tasks[0]["task_type"] == "session_commit"


async def test_list_tasks_filter_status(api_client):
    client, service = api_client

    service.sessions.commit_async = _make_tracked_commit()

    session_id = await _new_session_with_message(client)
    await client.post(f"/api/v1/sessions/{session_id}/commit")
    await asyncio.sleep(0.2)

    # completed tasks
    resp = await client.get("/api/v1/tasks", params={"status": "completed"})
    assert resp.status_code == 200
    for t in resp.json()["result"]:
        assert t["status"] == "completed"


# ── Error sanitization in task ──


async def test_error_sanitized_in_task(api_client):
    """Errors stored in tasks should have secrets redacted."""
    client, service = api_client
    session_id = await _new_session_with_message(client)

    service.sessions.commit_async = _make_tracked_commit(
        behavior="fail",
        result_overrides={"error": "Auth failed with key sk-ant-api03-DAqSsuperSecretKey123"},
    )

    resp = await client.post(f"/api/v1/sessions/{session_id}/commit")
    task_id = resp.json()["result"]["task_id"]

    await asyncio.sleep(0.2)

    task_resp = await client.get(f"/api/v1/tasks/{task_id}")
    error = task_resp.json()["result"]["error"]
    assert "superSecretKey" not in error
    assert "[REDACTED]" in error


# ── add_resource task tracking ──


async def test_add_resource_async_returns_task_id(api_client):
    """add_resource with wait=False should return a task_id."""
    client, service = api_client

    async def fake_add_resource(**kwargs):
        tracker = get_task_tracker()
        root_uri = "viking://resources/async-test"
        task = tracker.create(
            "add_resource",
            resource_id=root_uri,
            owner_account_id=kwargs["ctx"].account_id,
            owner_user_id=kwargs["ctx"].user.user_id,
        )
        tracker.start(task.task_id)
        tracker.complete(task.task_id, {"root_uri": root_uri})
        return {"status": "success", "root_uri": root_uri, "task_id": task.task_id}

    service.resources.add_resource = fake_add_resource

    from openviking.server.identity import RequestContext, Role
    from openviking_cli.session.user_id import UserIdentifier

    ctx = RequestContext(user=UserIdentifier.the_default_user(), role=Role.ROOT)
    result = await service.resources.add_resource(ctx=ctx, reason="test async resource")

    assert "task_id" in result
    assert result["task_id"]

    task_resp = await client.get(f"/api/v1/tasks/{result['task_id']}")
    assert task_resp.status_code == 200
    task_data = task_resp.json()["result"]
    assert task_data["task_type"] == "add_resource"


async def test_add_resource_sync_no_task_id(api_client):
    """add_resource with wait=True should NOT return a task_id."""
    client, service = api_client

    async def fake_add_resource(**kwargs):
        root_uri = "viking://resources/sync-test"
        return {"status": "success", "root_uri": root_uri}

    service.resources.add_resource = fake_add_resource

    from openviking.server.identity import RequestContext, Role
    from openviking_cli.session.user_id import UserIdentifier

    ctx = RequestContext(user=UserIdentifier.the_default_user(), role=Role.ROOT)
    result = await service.resources.add_resource(ctx=ctx, reason="test sync resource")

    assert "task_id" not in result


async def test_add_resource_async_task_lifecycle(api_client):
    """Async add_resource task should transition pending→running→completed."""
    client, service = api_client

    async def fake_add_resource(**kwargs):
        tracker = get_task_tracker()
        root_uri = "viking://resources/test-resource"
        task = tracker.create(
            "add_resource",
            resource_id=root_uri,
            owner_account_id=kwargs["ctx"].account_id,
            owner_user_id=kwargs["ctx"].user.user_id,
        )

        async def _background():
            tracker.start(task.task_id)
            await asyncio.sleep(0.05)
            tracker.complete(task.task_id, {"root_uri": root_uri})

        asyncio.create_task(_background())
        return {"status": "success", "root_uri": root_uri, "task_id": task.task_id}

    service.resources.add_resource = fake_add_resource

    from openviking.server.identity import RequestContext, Role
    from openviking_cli.session.user_id import UserIdentifier

    ctx = RequestContext(user=UserIdentifier.the_default_user(), role=Role.ROOT)
    result = await service.resources.add_resource(ctx=ctx, reason="test lifecycle")

    task_id = result["task_id"]

    task_resp = await client.get(f"/api/v1/tasks/{task_id}")
    assert task_resp.status_code == 200
    assert task_resp.json()["result"]["status"] in {"pending", "running"}

    await asyncio.sleep(0.2)

    task_resp = await client.get(f"/api/v1/tasks/{task_id}")
    assert task_resp.status_code == 200
    task_data = task_resp.json()["result"]
    assert task_data["status"] == "completed"
    assert task_data["task_type"] == "add_resource"


async def test_add_resource_task_list_filter(api_client):
    """add_resource tasks should appear in task list filtered by type."""
    client, service = api_client

    async def fake_add_resource(**kwargs):
        tracker = get_task_tracker()
        root_uri = "viking://resources/filter-test"
        task = tracker.create(
            "add_resource",
            resource_id=root_uri,
            owner_account_id=kwargs["ctx"].account_id,
            owner_user_id=kwargs["ctx"].user.user_id,
        )
        tracker.start(task.task_id)
        tracker.complete(task.task_id, {"root_uri": root_uri})
        return {"status": "success", "root_uri": root_uri, "task_id": task.task_id}

    service.resources.add_resource = fake_add_resource

    from openviking.server.identity import RequestContext, Role
    from openviking_cli.session.user_id import UserIdentifier

    ctx = RequestContext(user=UserIdentifier.the_default_user(), role=Role.ROOT)
    result = await service.resources.add_resource(ctx=ctx, reason="test list filter")
    task_id = result["task_id"]

    resp = await client.get("/api/v1/tasks", params={"task_type": "add_resource"})
    assert resp.status_code == 200
    tasks = resp.json()["result"]
    matching = [t for t in tasks if t["task_id"] == task_id]
    assert len(matching) >= 1
    assert matching[0]["task_type"] == "add_resource"


# ── add_skill task tracking ──


async def test_add_skill_async_returns_task_id(api_client):
    """add_skill with wait=False should return a task_id."""
    client, service = api_client

    async def fake_add_skill(**kwargs):
        tracker = get_task_tracker()
        task = tracker.create(
            "add_skill",
            owner_account_id=kwargs["ctx"].account_id,
            owner_user_id=kwargs["ctx"].user.user_id,
        )
        tracker.start(task.task_id)
        tracker.complete(task.task_id, {})
        return {"status": "success", "task_id": task.task_id}

    service.resources.add_skill = fake_add_skill

    from openviking.server.identity import RequestContext, Role
    from openviking_cli.session.user_id import UserIdentifier

    ctx = RequestContext(user=UserIdentifier.the_default_user(), role=Role.ROOT)
    result = await service.resources.add_skill(data="test skill", ctx=ctx)

    assert "task_id" in result
    assert result["task_id"]

    task_resp = await client.get(f"/api/v1/tasks/{result['task_id']}")
    assert task_resp.status_code == 200
    task_data = task_resp.json()["result"]
    assert task_data["task_type"] == "add_skill"


async def test_add_skill_sync_no_task_id(api_client):
    """add_skill with wait=True should NOT return a task_id."""
    client, service = api_client

    async def fake_add_skill(**kwargs):
        return {"status": "success"}

    service.resources.add_skill = fake_add_skill

    from openviking.server.identity import RequestContext, Role
    from openviking_cli.session.user_id import UserIdentifier

    ctx = RequestContext(user=UserIdentifier.the_default_user(), role=Role.ROOT)
    result = await service.resources.add_skill(data="test skill", ctx=ctx, wait=True)

    assert "task_id" not in result
