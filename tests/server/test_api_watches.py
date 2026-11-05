# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0

"""Tests for watch management endpoints (RFC #2104)."""

from types import SimpleNamespace
from unittest.mock import AsyncMock

import httpx
import pytest
import pytest_asyncio
from pydantic import ValidationError

from openviking.resource.watch_manager import WatchManager
from openviking.resource.watch_types import WatchRunResult
from openviking.server.routers.watches import UpdateWatchRequest
from openviking.service.resource_service import ResourceService
from openviking_cli.utils.config.open_viking_config import (
    OpenVikingConfig,
    OpenVikingConfigSingleton,
)
from tests.utils.watch_fixtures import reserve_watch


@pytest.fixture(autouse=True)
def watch_test_config(tmp_path, monkeypatch):
    """Use isolated local storage without requiring a developer's ov.conf."""
    config = OpenVikingConfig.from_dict(
        {
            "storage": {
                "workspace": str(tmp_path),
                "agfs": {"backend": "local"},
                "vectordb": {"backend": "local"},
            },
            "embedding": {
                "dense": {
                    "provider": "openai",
                    "model": "test-embedder",
                    "api_key": "test-key",
                    "dimension": 2048,
                }
            },
        }
    )
    monkeypatch.setattr(OpenVikingConfigSingleton, "_instance", config)


@pytest_asyncio.fixture
async def service(watch_store, watch_vfs):
    """Exercise real watch endpoints without initializing the vector backend."""
    manager = WatchManager(
        watch_store,
        viking_fs=watch_vfs,
        resource_service=AsyncMock(spec=ResourceService),
    )
    yield SimpleNamespace(watch_manager=manager)


@pytest.fixture
def watch_manager(service):
    wm = service.watch_manager
    assert wm is not None, "WatchManager must be initialized for these tests"
    return wm


async def _seed(
    wm,
    *,
    to_uri="viking://resources/test/foo",
    account="default",
    user="default",
    role="user",
    interval=60.0,
    path="https://example.com/foo",
    source_type=None,
):
    return await wm.create_task(
        path=path,
        account_id=account,
        user_id=user,
        original_role=role,
        source_type=source_type,
        to_uri=to_uri,
        watch_interval=interval,
    )


async def test_list_empty(client: httpx.AsyncClient):
    resp = await client.get("/api/v1/watches")
    assert resp.status_code == 200
    body = resp.json()
    assert body["status"] == "ok"
    assert body["result"] == {"tasks": [], "total": 0}


async def test_list_cursor_pagination(client: httpx.AsyncClient, watch_manager):
    tasks = [
        await _seed(watch_manager, to_uri=f"viking://resources/test/page-{index}")
        for index in range(5)
    ]
    expected = [
        task.to_dict()
        for task in sorted(tasks, key=lambda task: (task.created_at, task.task_id), reverse=True)
    ]
    seen = []
    params = {"limit": 2}
    for page_number in range(3):
        response = await client.get("/api/v1/watches", params=params)
        assert response.status_code == 200
        result = response.json()["result"]
        assert result["total"] == 5
        assert result["has_more"] == (page_number < 2)
        seen.extend(result["tasks"])
        assert (result["next_cursor"] is not None) == result["has_more"]
        params["cursor"] = result["next_cursor"]
    assert seen == expected
    # Cursor-only requests use the default page size and can change the limit.
    first = (await client.get("/api/v1/watches", params={"limit": 1})).json()["result"]
    response = await client.get("/api/v1/watches", params={"cursor": first["next_cursor"]})
    assert response.json()["result"]["tasks"] == expected[1:]
    assert response.json()["result"]["has_more"] is False
    legacy = (await client.get("/api/v1/watches")).json()["result"]
    assert set(legacy) == {"tasks", "total"}


async def test_list_pagination_empty(client: httpx.AsyncClient):
    response = await client.get("/api/v1/watches", params={"limit": 10})
    assert response.status_code == 200
    assert response.json()["result"] == {
        "tasks": [],
        "total": 0,
        "has_more": False,
        "next_cursor": None,
    }


@pytest.mark.parametrize(
    "params",
    [
        {"limit": 0},
        {"limit": 201},
        {"limit": "invalid"},
        {"cursor": "invalid"},
        {"cursor": ""},
        {"cursor": "x" * 2049},
        {"to_uri": "viking://resources/test/foo", "limit": 1},
        {"to_uri": "viking://resources/test/foo", "cursor": "invalid"},
    ],
)
async def test_list_rejects_invalid_pagination(client: httpx.AsyncClient, params):
    response = await client.get("/api/v1/watches", params=params)
    assert response.status_code == 400


async def test_list_cursor_rejects_filter_or_identity_change(
    app, client: httpx.AsyncClient, watch_manager
):
    from openviking.server.auth import get_request_context
    from openviking.server.identity import RequestContext, Role
    from openviking_cli.session.user_id import UserIdentifier

    await _seed(watch_manager, to_uri="viking://resources/test/cursor-a")
    await _seed(watch_manager, to_uri="viking://resources/test/cursor-b")
    first = (await client.get("/api/v1/watches", params={"limit": 1})).json()["result"]
    response = await client.get(
        "/api/v1/watches", params={"cursor": first["next_cursor"], "active_only": True}
    )
    assert response.status_code == 400
    app.dependency_overrides[get_request_context] = lambda: RequestContext(
        user=UserIdentifier(account_id="default", user_id="other"), role=Role.USER
    )
    try:
        response = await client.get("/api/v1/watches", params={"cursor": first["next_cursor"]})
        assert response.status_code == 400
        response = await client.get("/api/v1/watches", params={"limit": 1})
        assert response.json()["result"]["total"] == 0
    finally:
        app.dependency_overrides.pop(get_request_context, None)


@pytest.mark.parametrize(
    "change",
    [
        {"version": 0},
        {"version": True},
        {"authorized": "false"},
        {"ov_account_id": ""},
        {"unexpected": "value"},
    ],
)
def test_credential_state_contract_rejects_invalid_fields(change):
    state = {
        "account_id": "account",
        "resource_id": "ov-resource",
        "user_id": "user",
        "ov_user_id": "default",
        "ov_account_id": "default",
        "type": "oauth",
        "platform": "dingtalk",
        "version": 8,
        "authorized": False,
        **change,
    }
    with pytest.raises(ValidationError):
        UpdateWatchRequest(credential_state=state)


async def test_patch_credential_state(client: httpx.AsyncClient, watch_manager):
    state = {
        "account_id": "account",
        "resource_id": "ov-resource",
        "user_id": "user",
        "ov_user_id": "default",
        "ov_account_id": "default",
        "type": "oauth",
        "platform": "dingtalk",
        "version": 8,
        "authorized": False,
    }
    reference = {key: value for key, value in state.items() if key not in {"version", "authorized"}}
    reference["binding_version"] = 3
    task = await _seed(watch_manager, source_type="dingtalk", path="dingtalk://node")
    await watch_manager.update_auth_state(
        task.task_id,
        {
            "provider": "connector_plaintext",
            "request": {
                "api_key": "test-key",
                "account_id": "default",
                "add_type": "dingtalk",
                "path": task.path,
                "connector_args": {},
                "connector_auth_config": {"openviking_credential_ref": reference},
            },
        },
    )
    response = await client.patch(
        f"/api/v1/watches/{task.task_id}", json={"credential_state": state}
    )
    assert response.status_code == 200
    assert response.json()["result"]["is_active"] is False
    state.update(version=10, authorized=True)
    response = await client.patch(
        f"/api/v1/watches/{task.task_id}", json={"credential_state": state}
    )
    assert response.status_code == 200
    assert response.json()["result"]["is_active"] is True
    response = await client.patch(
        f"/api/v1/watches/{task.task_id}",
        json={
            "credential_state": state,
            "is_active": False,
        },
    )
    assert response.status_code == 400


async def test_full_lifecycle(client: httpx.AsyncClient, watch_manager, monkeypatch):
    task = await _seed(watch_manager, source_type="url")

    # List
    resp = await client.get("/api/v1/watches")
    body = resp.json()
    assert body["status"] == "ok"
    assert body["result"]["total"] == 1
    assert body["result"]["tasks"][0]["task_id"] == task.task_id
    assert body["result"]["tasks"][0]["source_type"] == "url"
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
    assert resp.json()["result"]["watch_interval"] == 4320

    # POST trigger
    triggered = []

    async def fake_trigger(_self, task_id):
        triggered.append(task_id)
        return True

    monkeypatch.setattr(
        "openviking.resource.watch_manager.WatchManager.trigger",
        fake_trigger,
    )
    resp = await client.post(f"/api/v1/watches/{task.task_id}/trigger")
    assert resp.status_code == 200
    body = resp.json()
    assert body["result"]["scheduled"] is True
    assert triggered == [task.task_id]

    # DELETE
    resp = await client.delete(f"/api/v1/watches/{task.task_id}")
    assert resp.status_code == 200
    assert resp.json()["result"]["deleted"] is True

    # Subsequent GET → 404
    resp = await client.get(f"/api/v1/watches/{task.task_id}")
    assert resp.status_code == 404


async def test_list_exposes_last_execution_result(client: httpx.AsyncClient, watch_manager):
    task = await _seed(watch_manager, to_uri="viking://resources/test/observable")
    run = await reserve_watch(
        watch_manager.store,
        task.task_id,
        run_id="connector-task-1",
        target_uri=task.to_uri,
        manual=True,
    )
    await watch_manager.finish_run(
        task.task_id,
        run.run_id,
        WatchRunResult(status="failed", execution_task_id="connector-task-1", error="pull failed"),
    )

    resp = await client.get("/api/v1/watches")

    assert resp.status_code == 200
    result = resp.json()["result"]["tasks"][0]
    assert result["last_task_id"] == "connector-task-1"
    assert result["last_status"] == "failed"
    assert result["last_error"] == "pull failed"


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
    assert resp.status_code == 400


async def test_not_found_404(client: httpx.AsyncClient):
    resp = await client.delete("/api/v1/watches/no-such-task-id")
    assert resp.status_code == 404
    resp = await client.get("/api/v1/watches/no-such-task-id")
    assert resp.status_code == 404
    resp = await client.patch("/api/v1/watches/no-such-task-id", json={"is_active": False})
    assert resp.status_code == 404


async def test_patch_rejects_non_positive_watch_interval(client: httpx.AsyncClient, watch_manager):
    """Invalid cadence is rejected without changing the Watch."""
    task = await _seed(watch_manager, to_uri="viking://resources/test/nonpos")
    for bad in [-1, 0]:
        resp = await client.patch(
            f"/api/v1/watches/{task.task_id}",
            json={"watch_interval": bad},
        )
        assert resp.status_code == 400, (
            f"watch_interval={bad} should be 400, got {resp.status_code}: {resp.text[:200]}"
        )
    assert (await watch_manager.get_task(task.task_id)).watch_interval == task.watch_interval


async def test_patch_rejects_unknown_field(client: httpx.AsyncClient, watch_manager):
    """Changing a target requires deleting and recreating its Watch."""
    task = await _seed(watch_manager, to_uri="viking://resources/test/forbid")
    resp = await client.patch(
        f"/api/v1/watches/{task.task_id}",
        json={"to_uri": "viking://resources/test/other"},
    )
    assert resp.status_code == 400


async def test_trigger_by_uri(client: httpx.AsyncClient, watch_manager, monkeypatch):
    task = await _seed(watch_manager, to_uri="viking://resources/test/trig")

    triggered = []

    async def fake_trigger(_self, task_id):
        triggered.append(task_id)
        return True

    monkeypatch.setattr(
        "openviking.resource.watch_manager.WatchManager.trigger",
        fake_trigger,
    )
    resp = await client.post("/api/v1/watches/trigger", params={"to_uri": task.to_uri})
    assert resp.status_code == 200
    assert triggered == [task.task_id]


async def test_home_alias_to_uri_resolves_to_canonically_keyed_task(
    app, client: httpx.AsyncClient, watch_manager
):
    """`?to_uri=viking://~/...` addresses the task stored under the canonical key.

    Watch tasks are keyed on the canonical URI, and the router canonicalizes
    ``to_uri`` at the request boundary, so the alias resolves to the same task
    and the response keeps echoing the stored canonical key. The dev auth plugin
    resolves test requests to root, which never expands the alias, so this test
    overrides the request context with a user-role identity.
    """
    from openviking.server.auth import get_request_context
    from openviking.server.identity import RequestContext, Role
    from openviking_cli.session.user_id import UserIdentifier

    canonical = "viking://user/default/resources/home-alias"
    task = await _seed(watch_manager, to_uri=canonical)
    app.dependency_overrides[get_request_context] = lambda: RequestContext(
        user=UserIdentifier(account_id="default", user_id="default"),
        role=Role.USER,
    )
    try:
        resp = await client.get(
            "/api/v1/watches", params={"to_uri": "viking://~/resources/home-alias"}
        )
        assert resp.status_code == 200
        assert resp.json()["result"]["task_id"] == task.task_id
        assert resp.json()["result"]["to_uri"] == canonical

        resp = await client.delete(
            "/api/v1/watches", params={"to_uri": "viking://~/resources/home-alias"}
        )
        assert resp.status_code == 200
        assert resp.json()["result"]["deleted"] is True
        assert resp.json()["result"]["to_uri"] == canonical
    finally:
        app.dependency_overrides.pop(get_request_context, None)
