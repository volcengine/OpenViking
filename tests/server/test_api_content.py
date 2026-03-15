# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: Apache-2.0

"""Tests for content endpoints: read, abstract, overview."""


async def test_read_content(client_with_resource):
    client, uri = client_with_resource
    # The resource URI may be a directory; list children to find the file
    ls_resp = await client.get(
        "/api/v1/fs/ls",
        params={"uri": uri, "simple": True, "recursive": True, "output": "original"},
    )
    children = ls_resp.json().get("result", [])
    # Find a file (non-directory) to read
    file_uri = None
    if children:
        # ls(simple=True) returns full URIs, use directly
        file_uri = children[0] if isinstance(children[0], str) else None
    if file_uri is None:
        file_uri = uri

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


async def test_reindex_existing_resource(client_with_resource):
    """Test reindex on an already-added resource (re-embed only)."""
    client, uri = client_with_resource
    resp = await client.post(
        "/api/v1/content/reindex",
        json={"uri": uri, "regenerate": False},
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["status"] == "ok"
    assert body["result"]["status"] == "success"


async def test_reindex_not_found(client):
    """Test reindex on a non-existent URI returns NOT_FOUND."""
    resp = await client.post(
        "/api/v1/content/reindex",
        json={"uri": "viking://resources/nonexistent", "regenerate": False},
    )
    assert resp.status_code == 200
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


async def test_reindex_default_regenerate_false(client_with_resource):
    """Test reindex defaults to regenerate=False."""
    client, uri = client_with_resource
    resp = await client.post(
        "/api/v1/content/reindex",
        json={"uri": uri},
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["status"] == "ok"
    assert body["result"]["status"] == "success"
