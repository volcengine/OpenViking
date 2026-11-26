# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0

"""HTTP contracts shared by background task producers."""

from typing import Any

import httpx
import pytest_asyncio

from openviking.server.identity import ResolvedIdentity, Role
from openviking.service.task_tracker import get_task_tracker, set_task_tracker
from tests.server.test_auth import _build_task_http_test_app, _set_fake_task_tracker

OWNER: dict[str, Any] = {"account_id": "acme", "user_id": "alice"}


@pytest_asyncio.fixture
async def api_client():
    _set_fake_task_tracker()
    app = _build_task_http_test_app(ResolvedIdentity(role=Role(Role.USER), **OWNER))
    transport = httpx.ASGITransport(app=app)
    try:
        async with httpx.AsyncClient(transport=transport, base_url="http://testserver") as client:
            yield client
    finally:
        set_task_tracker(None)


async def test_list_tasks_hides_internal_tasks_by_default(api_client):
    tracker = get_task_tracker()
    visible = await tracker.create("add_resource", **OWNER)
    internal = await tracker.create("add_resource", meta={"internal": True}, **OWNER)
    await tracker.create("add_skill", **OWNER)

    params = {"task_type": "add_resource", "limit": 1}
    response = await api_client.get("/api/v1/tasks", params=params)
    assert response.status_code == 200, response.text
    assert [task["task_id"] for task in response.json()["result"]] == [visible.task_id]

    response = await api_client.get("/api/v1/tasks", params={**params, "include_internal": True})
    assert response.status_code == 200, response.text
    assert [task["task_id"] for task in response.json()["result"]] == [internal.task_id]

    response = await api_client.get(f"/api/v1/tasks/{internal.task_id}")
    assert response.status_code == 200, response.text
    assert response.json()["result"]["task_id"] == internal.task_id


async def test_list_tasks_filter_status(api_client):
    tracker = get_task_tracker()
    first = await tracker.create("add_resource", **OWNER)
    second = await tracker.create("add_resource", **OWNER)
    await tracker.create("add_resource", **OWNER)
    for task in (first, second):
        await tracker.complete(task.task_id, {}, **OWNER)

    params = {"pagination": "cursor", "status": "completed", "limit": 1}
    response = await api_client.get("/api/v1/tasks", params=params)
    assert response.status_code == 200, response.text
    first_page = response.json()["result"]
    assert [task["task_id"] for task in first_page["items"]] == [second.task_id]
    assert first_page["has_more"] is True

    cursor = first_page["next_cursor"]
    response = await api_client.get("/api/v1/tasks", params={**params, "cursor": cursor})
    assert response.status_code == 200, response.text
    last_page = response.json()["result"]
    assert [task["task_id"] for task in last_page["items"]] == [first.task_id]
    assert last_page["has_more"] is False
    assert last_page["next_cursor"] is None


async def test_cancel_task_respects_owner_and_terminal_state(api_client):
    tracker = get_task_tracker()
    foreign = await tracker.create("add_resource", account_id="acme", user_id="bob")
    done = await tracker.create("add_resource", **OWNER)
    await tracker.complete(done.task_id, {}, **OWNER)
    pending = await tracker.create("add_resource", **OWNER)

    for task, status, code in (
        (foreign, 404, "NOT_FOUND"),
        (done, 412, "FAILED_PRECONDITION"),
    ):
        response = await api_client.post(f"/api/v1/tasks/{task.task_id}/cancel")
        assert response.status_code == status, response.text
        assert response.json()["error"]["code"] == code

    for _ in range(2):
        response = await api_client.post(f"/api/v1/tasks/{pending.task_id}/cancel")
        assert response.status_code == 200, response.text
        assert response.json()["result"]["status"] == "cancelled"
    response = await api_client.get(f"/api/v1/tasks/{pending.task_id}")
    assert response.status_code == 200, response.text
    assert response.json()["result"]["status"] == "cancelled"
