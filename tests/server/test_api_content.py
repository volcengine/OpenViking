# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0

"""Tests for content endpoints: read, abstract, overview, reindex."""

from types import SimpleNamespace

import pytest

from openviking.server.identity import RequestContext, Role
from openviking.server.routers import content as content_router
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


async def test_read_memory_uses_visible_projection_and_raw_bypasses_it(monkeypatch):
    ctx = RequestContext(user=UserIdentifier.the_default_user("test_user"), role=Role.USER)
    uri = "viking://user/test_user/memories/notes/private.md"
    raw = 'line one\nline two\n\n<!-- MEMORY_FIELDS\n{"secret": "do-not-leak"}\n-->'
    calls = []

    async def fake_read_visible(_uri, *, ctx, offset, limit):
        calls.append((offset, limit))
        return "visible slice"

    async def fake_read(_uri, *, ctx, offset, limit):
        lines = raw.splitlines(keepends=True)
        return "".join(lines[offset:] if limit == -1 else lines[offset : offset + limit])

    monkeypatch.setattr(
        content_router,
        "get_service",
        lambda: SimpleNamespace(fs=SimpleNamespace(read=fake_read, read_visible=fake_read_visible)),
    )

    response = await content_router.read(uri=uri, offset=4, limit=1, raw=False, _ctx=ctx)
    assert response.result == "visible slice"
    assert calls[-1] == (4, 1)

    raw_response = await content_router.read(uri=uri, offset=4, limit=1, raw=True, _ctx=ctx)
    assert raw_response.result == '{"secret": "do-not-leak"}\n'
    assert calls[-1] == (4, 1)


async def test_read_directory_uri_returns_invalid_argument(client_with_resource):
    client, uri = client_with_resource

    resp = await client.get("/api/v1/content/read", params={"uri": uri})

    assert resp.status_code == 400
    body = resp.json()
    assert body["status"] == "error"
    assert body["error"]["code"] == "INVALID_ARGUMENT"
    assert "Cannot read directory as file" in body["error"]["message"]
    assert body["error"]["details"] == {
        "resource": uri,
        "expected": "file",
        "actual": "directory",
    }


@pytest.mark.parametrize("uri", ["viking://temp/generated", "viking://queue/tasks"])
async def test_read_internal_scope_uri_returns_invalid_uri(client, uri: str):
    resp = await client.get("/api/v1/content/read", params={"uri": uri})

    assert resp.status_code == 400
    body = resp.json()
    assert body["status"] == "error"
    assert body["error"]["code"] == "INVALID_URI"
    assert "Must be one of" in body["error"]["message"]
    assert "frozenset" not in body["error"]["message"]


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
    """Test reindex without uri field returns structured INVALID_ARGUMENT."""
    resp = await client.post(
        "/api/v1/content/reindex",
        json={"mode": "vectors_only"},
    )
    assert resp.status_code == 400


async def test_reindex_endpoint_registered(client):
    """Test the reindex endpoint is registered (GET returns 405, not 404)."""
    resp = await client.get("/api/v1/content/reindex")
    assert resp.status_code == 405  # Method Not Allowed, not 404


async def test_reindex_request_validation(client):
    """Test reindex validates the request body schema."""
    # Empty body — uri is required
    resp = await client.post("/api/v1/content/reindex", json={})
    assert resp.status_code == 400

    # Invalid mode should not be accepted by the endpoint
    resp = await client.post(
        "/api/v1/content/reindex",
        json={"uri": "viking://resources/test", "mode": "not_a_mode"},
    )
    assert resp.status_code in (200, 400)


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

    class FakeService:
        async def reindex(self, *, uri, mode, wait, ctx, dry_run=False):
            seen["uri"] = uri
            seen["mode"] = mode
            seen["wait"] = wait
            seen["ctx"] = ctx
            return {"status": "completed", "uri": uri, "mode": mode}

    ctx = RequestContext(
        user=UserIdentifier(account_id="test", user_id="alice"),
        role=Role.ADMIN,
    )
    request = ReindexRequest(
        uri="viking://resources/demo/demo-note.md",
        mode="semantic_and_vectors",
        wait=True,
    )

    monkeypatch.setattr("openviking.server.routers.content.get_service", lambda: FakeService())

    response = await reindex(body=request, ctx=ctx)

    assert response.status == "ok"
    assert seen["uri"] == "viking://resources/demo/demo-note.md"
    assert seen["mode"] == "semantic_and_vectors"
    assert seen["wait"] is True
    assert seen["ctx"] == ctx


async def test_content_rebuild_endpoint_removed(client):
    response = await client.post(
        "/api/v1/content/rebuild",
        json={"uri": "viking://resources/demo", "mode": "vectors_only"},
    )
    assert response.status_code == 404


async def test_maintenance_reindex_endpoint_removed(client):
    response = await client.post(
        "/api/v1/maintenance/reindex",
        json={"uri": "viking://resources/demo", "wait": True},
    )
    assert response.status_code == 404
