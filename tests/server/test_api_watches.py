# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0

"""Tests for watch management endpoints (RFC #2104)."""

import asyncio

import httpx
import pytest


@pytest.fixture
def watch_manager(service):
    wm = service.watch_scheduler.watch_manager
    assert wm is not None, "WatchScheduler must be running for these tests"
    return wm


async def _seed(
    wm,
    *,
    to_uri="viking://resources/test/foo",
    account="default",
    user="default",
    agent="default",
    role="user",
    interval=60.0,
    path="https://example.com/foo",
):
    return await wm.create_task(
        path=path,
        account_id=account,
        user_id=user,
        agent_id=agent,
        original_role=role,
        to_uri=to_uri,
        watch_interval=interval,
    )


async def test_list_empty(client: httpx.AsyncClient):
    resp = await client.get("/api/v1/watches")
    assert resp.status_code == 200
    body = resp.json()
    assert body["status"] == "ok"
    assert body["result"] == {"tasks": [], "total": 0}


async def test_full_lifecycle(client: httpx.AsyncClient, watch_manager, monkeypatch):
    task = await _seed(watch_manager)

    # List
    resp = await client.get("/api/v1/watches")
    body = resp.json()
    assert body["status"] == "ok"
    assert body["result"]["total"] == 1
    assert body["result"]["tasks"][0]["task_id"] == task.task_id
    assert body["result"]["tasks"][0]["to_uri"] == task.to_uri

    # Get by ID
    resp = await client.get(f"/api/v1/watches/{task.task_id}")
    assert resp.status_code == 200
    assert resp.json()["result"]["task_id"] == task.task_id

    # Get by URI (via list endpoint with ?to_uri=)
    resp = await client.get("/api/v1/watches", params={"to_uri": task.to_uri})
    assert resp.status_code == 200
    body = resp.json()
    assert body["result"]["task_id"] == task.task_id

    # PATCH watch_interval
    resp = await client.patch(
        f"/api/v1/watches/{task.task_id}",
        json={"watch_interval": 4320},
    )
    assert resp.status_code == 200
    assert resp.json()["result"]["watch_interval"] == 4320

    # PATCH is_active=false (pause)
    resp = await client.patch(
        f"/api/v1/watches/{task.task_id}",
        json={"is_active": False},
    )
    assert resp.status_code == 200
    assert resp.json()["result"]["is_active"] is False
    assert resp.json()["result"]["next_execution_time"] is None

    # POST trigger — monkeypatch scheduler so we don't actually run a fetch.
    # Class-level setattr means the function is bound as a method, so the
    # fake must accept `self` (the WatchScheduler instance) as first arg.
    triggered = []
    schedule_started = asyncio.Event()

    async def fake_schedule(_self, task_id):
        triggered.append(task_id)
        schedule_started.set()
        return True

    monkeypatch.setattr(
        "openviking.resource.watch_scheduler.WatchScheduler.schedule_task",
        fake_schedule,
    )
    resp = await client.post(f"/api/v1/watches/{task.task_id}/trigger")
    assert resp.status_code == 200
    body = resp.json()
    assert body["result"]["scheduled"] is True
    # Trigger is fire-and-forget — wait briefly for the background task to run.
    await asyncio.wait_for(schedule_started.wait(), timeout=2.0)
    assert triggered == [task.task_id]

    # DELETE
    resp = await client.delete(f"/api/v1/watches/{task.task_id}")
    assert resp.status_code == 200
    assert resp.json()["result"]["deleted"] is True

    # Subsequent GET → 404
    resp = await client.get(f"/api/v1/watches/{task.task_id}")
    assert resp.status_code == 404


async def test_get_by_uri_returns_single_object(client: httpx.AsyncClient, watch_manager):
    task = await _seed(watch_manager, to_uri="viking://resources/test/uri-keyed")
    resp = await client.get("/api/v1/watches", params={"to_uri": task.to_uri})
    assert resp.status_code == 200
    body = resp.json()
    # When to_uri is given, result is single object (not the {tasks, total} envelope)
    assert "task_id" in body["result"]
    assert "tasks" not in body["result"]


async def test_active_only_filter(client: httpx.AsyncClient, watch_manager):
    active = await _seed(watch_manager, to_uri="viking://resources/test/active")
    paused = await _seed(watch_manager, to_uri="viking://resources/test/paused")
    await watch_manager.update_task(paused.task_id, "default", "default", "root", is_active=False)

    resp = await client.get("/api/v1/watches", params={"active_only": "true"})
    ids = {t["task_id"] for t in resp.json()["result"]["tasks"]}
    assert active.task_id in ids
    assert paused.task_id not in ids

    resp = await client.get("/api/v1/watches", params={"active_only": "false"})
    ids = {t["task_id"] for t in resp.json()["result"]["tasks"]}
    assert active.task_id in ids
    assert paused.task_id in ids


async def test_dual_key_matching_accepted(client: httpx.AsyncClient, watch_manager):
    """When both {task_id} and ?to_uri= are supplied and resolve to the SAME task,
    the request succeeds (useful as a cross-key sanity check from clients that
    have both pieces of information)."""
    task = await _seed(watch_manager, to_uri="viking://resources/test/dual-ok")
    resp = await client.get(f"/api/v1/watches/{task.task_id}", params={"to_uri": task.to_uri})
    assert resp.status_code == 200
    assert resp.json()["result"]["task_id"] == task.task_id


async def test_dual_key_mismatch_returns_400(client: httpx.AsyncClient, watch_manager):
    """When {task_id} and ?to_uri= refer to DIFFERENT tasks, return 400."""
    a = await _seed(watch_manager, to_uri="viking://resources/test/dual-a")
    b = await _seed(watch_manager, to_uri="viking://resources/test/dual-b")
    # Use a's task_id but b's to_uri — they disagree.
    resp = await client.get(f"/api/v1/watches/{a.task_id}", params={"to_uri": b.to_uri})
    assert resp.status_code == 400


async def test_delete_missing_key_400(client: httpx.AsyncClient):
    """DELETE /watches without {task_id} path and without ?to_uri= must 400."""
    resp = await client.delete("/api/v1/watches")
    # The by-uri route requires to_uri as a Query(...) so FastAPI will 422 it.
    assert resp.status_code == 422


async def test_not_found_404(client: httpx.AsyncClient):
    resp = await client.delete("/api/v1/watches/no-such-task-id")
    assert resp.status_code == 404
    resp = await client.get("/api/v1/watches/no-such-task-id")
    assert resp.status_code == 404
    resp = await client.patch("/api/v1/watches/no-such-task-id", json={"is_active": False})
    assert resp.status_code == 404


async def test_patch_rejects_non_positive_watch_interval(client: httpx.AsyncClient, watch_manager):
    """REST PATCH must reject `watch_interval <= 0` at the request boundary.

    Without the field_validator, a negative or zero value would be forwarded
    to WatchManager.update_task, which deactivates the task and stores the
    bad cadence. A later resume (`is_active=true`) then fails inside
    update_task with ValueError → 404, misleading callers about the root
    cause. Reject upfront with 422 instead, matching CLI/MCP semantics.
    """
    task = await _seed(watch_manager, to_uri="viking://resources/test/nonpos")
    for bad in [-1, 0, -42.5]:
        resp = await client.patch(
            f"/api/v1/watches/{task.task_id}",
            json={"watch_interval": bad},
        )
        assert resp.status_code == 422, (
            f"watch_interval={bad} should be 422, got {resp.status_code}: {resp.text[:200]}"
        )


async def test_patch_rejects_unknown_field(client: httpx.AsyncClient, watch_manager):
    """UpdateWatchRequest has extra='forbid'; passing a field outside the allowed
    set (watch_interval / is_active / reason / instruction) returns 422.

    Note: `to_uri` is intentionally NOT exposed via PATCH. WatchManager's
    update_task supports rebinding to_uri (which is the only mutation that can
    raise ConflictError on the watch side), but we want to-uri assignment to
    be a delete-and-recreate operation for clarity, not an in-place mutation.
    """
    task = await _seed(watch_manager, to_uri="viking://resources/test/forbid")
    resp = await client.patch(
        f"/api/v1/watches/{task.task_id}",
        json={"to_uri": "viking://resources/test/other"},
    )
    assert resp.status_code == 422


async def test_trigger_by_uri(client: httpx.AsyncClient, watch_manager, monkeypatch):
    task = await _seed(watch_manager, to_uri="viking://resources/test/trig")

    triggered = []
    schedule_started = asyncio.Event()

    async def fake_schedule(_self, task_id):
        triggered.append(task_id)
        schedule_started.set()
        return True

    monkeypatch.setattr(
        "openviking.resource.watch_scheduler.WatchScheduler.schedule_task",
        fake_schedule,
    )
    resp = await client.post("/api/v1/watches/trigger", params={"to_uri": task.to_uri})
    assert resp.status_code == 200
    await asyncio.wait_for(schedule_started.wait(), timeout=2.0)
    assert triggered == [task.task_id]


async def test_patch_partial_preserves_unset_fields(client: httpx.AsyncClient, watch_manager):
    task = await _seed(watch_manager, to_uri="viking://resources/test/partial")
    original_interval = task.watch_interval

    # PATCH only is_active — interval should not change
    resp = await client.patch(f"/api/v1/watches/{task.task_id}", json={"is_active": False})
    assert resp.status_code == 200
    body = resp.json()
    assert body["result"]["watch_interval"] == original_interval
    assert body["result"]["is_active"] is False
