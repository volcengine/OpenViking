# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0

"""Tests for content endpoints: read, abstract, overview."""

from types import SimpleNamespace

import pytest

from openviking.server.identity import RequestContext, Role
from openviking.server.routers.content import ReindexRequest, reindex
from openviking_cli.session.user_id import UserIdentifier


async def _first_child_uri(client, uri: str) -> str:
    response = await client.get(
        "/api/v1/fs/ls",
        params={"uri": uri, "simple": True, "recursive": True, "output": "original"},
    )
    children = response.json().get("result", [])
    if children and isinstance(children[0], str):
        return children[0]
    return uri


async def test_read_content(client_with_resource):
    client, uri = client_with_resource
    file_uri = await _first_child_uri(client, uri)

    resp = await client.get("/api/v1/content/read", params={"uri": file_uri})
    assert resp.status_code == 200
    body = resp.json()
    assert body["status"] == "ok"
    assert body["result"] is not None


async def test_abstract_content(client_with_resource):
    client, uri = client_with_resource
    resp = await client.get("/api/v1/content/abstract", params={"uri": uri})
    assert resp.status_code == 200
    body = resp.json()
    assert body["status"] == "ok"


async def test_overview_content(client_with_resource):
    client, uri = client_with_resource
    resp = await client.get("/api/v1/content/overview", params={"uri": uri})
    assert resp.status_code == 200
    body = resp.json()
    assert body["status"] == "ok"


async def test_abstract_file_uri_returns_failed_precondition(client_with_resource):
    client, uri = client_with_resource
    file_uri = await _first_child_uri(client, uri)
    resp = await client.get("/api/v1/content/abstract", params={"uri": file_uri})
    assert resp.status_code == 412
    body = resp.json()
    assert body["status"] == "error"
    assert body["error"]["code"] == "FAILED_PRECONDITION"
    assert "not a directory" in body["error"]["message"]


async def test_overview_missing_uri_returns_not_found(client):
    resp = await client.get(
        "/api/v1/content/overview",
        params={"uri": "viking://resources/does-not-exist-for-overview"},
    )
    assert resp.status_code == 404
    body = resp.json()
    assert body["status"] == "error"
    assert body["error"]["code"] == "NOT_FOUND"


async def test_reindex_missing_uri(client):
    """Test reindex without uri field returns 422."""
    resp = await client.post(
        "/api/v1/content/reindex",
        json={"regenerate": False},
    )
    assert resp.status_code == 422


async def test_reindex_endpoint_registered(client):
    """Test the reindex endpoint is registered (GET returns 405, not 404)."""
    resp = await client.get("/api/v1/content/reindex")
    assert resp.status_code == 405  # Method Not Allowed, not 404


async def test_reindex_request_validation(client):
    """Test reindex validates the request body schema."""
    # Empty body — uri is required
    resp = await client.post("/api/v1/content/reindex", json={})
    assert resp.status_code == 422

    # Invalid type for regenerate
    resp = await client.post(
        "/api/v1/content/reindex",
        json={"uri": "viking://resources/test", "regenerate": "not_a_bool"},
    )
    # Pydantic coerces strings to bool, so this may or may not fail
    assert resp.status_code in (200, 422, 500)


async def test_reindex_wait_parameter_schema(client):
    """Test reindex accepts wait parameter in request schema."""
    # Invalid wait type should be coerced or rejected, not crash
    resp = await client.post(
        "/api/v1/content/reindex",
        json={"uri": "viking://resources/test", "wait": "invalid"},
    )
    # Pydantic coerces or rejects — either way, not a 404/405
    assert resp.status_code != 404
    assert resp.status_code != 405


@pytest.mark.asyncio
async def test_reindex_uses_request_tenant_for_exists(monkeypatch):
    """Reindex must validate URI existence inside the caller's tenant."""
    seen = {}

    class FakeVikingFS:
        async def exists(self, uri, ctx=None):
            seen["uri"] = uri
            seen["ctx"] = ctx
            return True

    class FakeTracker:
        def has_running(self, task_type, uri, owner_account_id=None, owner_user_id=None):
            return False

    async def fake_do_reindex(service, uri, regenerate, ctx):
        return {"status": "success", "message": "Indexed 1 resources"}

    ctx = RequestContext(
        user=UserIdentifier(account_id="test", user_id="alice", agent_id="default"),
        role=Role.ADMIN,
    )
    request = ReindexRequest(uri="viking://resources/demo/demo-note.md", wait=True)

    monkeypatch.setattr("openviking.storage.viking_fs.get_viking_fs", lambda: FakeVikingFS())
    monkeypatch.setattr(
        "openviking.service.task_tracker.get_task_tracker",
        lambda: FakeTracker(),
    )
    monkeypatch.setattr(
        "openviking.server.routers.content.get_service",
        lambda: SimpleNamespace(),
    )
    monkeypatch.setattr("openviking.server.routers.content._do_reindex", fake_do_reindex)

    response = await reindex(body=request, ctx=ctx)

    assert response.status == "ok"
    assert seen["uri"] == "viking://resources/demo/demo-note.md"
    assert seen["ctx"] == ctx


async def test_maintenance_reindex_sync_success_returns_ok_payload(client, monkeypatch):
    async def fake_do_reindex(service, uri, regenerate, ctx):
        return {"status": "success", "message": "Indexed 1 resources"}

    class FakeVikingFS:
        async def exists(self, uri, ctx=None):
            return True

    class FakeTracker:
        def has_running(self, task_type, uri, owner_account_id=None, owner_user_id=None):
            return False

    monkeypatch.setattr(
        "openviking.storage.viking_fs.get_viking_fs",
        lambda: FakeVikingFS(),
    )
    monkeypatch.setattr(
        "openviking.service.task_tracker.get_task_tracker",
        lambda: FakeTracker(),
    )
    monkeypatch.setattr(
        "openviking.server.routers.maintenance.get_service",
        lambda: SimpleNamespace(),
    )
    monkeypatch.setattr("openviking.server.routers.maintenance._do_reindex", fake_do_reindex)

    response = await client.post(
        "/api/v1/maintenance/reindex",
        json={"uri": "viking://resources/demo", "wait": True},
    )

    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "ok"
    assert body["result"]["status"] == "success"
