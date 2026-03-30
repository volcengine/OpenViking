# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0

"""Tests for filesystem endpoints: ls, tree, stat, mkdir, rm, mv."""

import httpx


async def test_ls_root(client: httpx.AsyncClient):
    resp = await client.get("/api/v1/fs/ls", params={"uri": "viking://"})
    assert resp.status_code == 200
    body = resp.json()
    assert body["status"] == "ok"
    assert isinstance(body["result"], list)


async def test_ls_simple(client: httpx.AsyncClient):
    resp = await client.get(
        "/api/v1/fs/ls",
        params={"uri": "viking://", "simple": True},
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["status"] == "ok"
    assert isinstance(body["result"], list)
    # Each item must be a non-empty URI string (fixes #218)
    for item in body["result"]:
        assert isinstance(item, str)
        assert item.startswith("viking://")


async def test_ls_simple_agent_output(client: httpx.AsyncClient):
    """Ensure --simple with output=agent returns URI strings, not empty."""
    resp = await client.get(
        "/api/v1/fs/ls",
        params={"uri": "viking://", "simple": True, "output": "agent"},
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["status"] == "ok"
    assert isinstance(body["result"], list)
    for item in body["result"]:
        assert isinstance(item, str)
        assert item.startswith("viking://")


async def test_mkdir_and_ls(client: httpx.AsyncClient):
    resp = await client.post(
        "/api/v1/fs/mkdir",
        json={"uri": "viking://resources/test_dir/"},
    )
    assert resp.status_code == 200
    assert resp.json()["status"] == "ok"

    resp = await client.get(
        "/api/v1/fs/ls",
        params={"uri": "viking://resources/"},
    )
    assert resp.status_code == 200


async def test_tree(client: httpx.AsyncClient):
    resp = await client.get("/api/v1/fs/tree", params={"uri": "viking://"})
    assert resp.status_code == 200
    body = resp.json()
    assert body["status"] == "ok"


async def test_stat_not_found(client: httpx.AsyncClient):
    resp = await client.get(
        "/api/v1/fs/stat",
        params={"uri": "viking://nonexistent/xyz"},
    )
    assert resp.status_code in (404, 500)
    body = resp.json()
    assert body["status"] == "error"


async def test_resource_ops(client_with_resource):
    """Test stat, ls_recursive, mv, rm on a single shared resource."""
    import uuid

    client, uri = client_with_resource

    # stat
    resp = await client.get("/api/v1/fs/stat", params={"uri": uri})
    assert resp.status_code == 200
    assert resp.json()["status"] == "ok"

    # ls recursive
    resp = await client.get(
        "/api/v1/fs/ls",
        params={"uri": "viking://", "recursive": True},
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["status"] == "ok"
    assert isinstance(body["result"], list)

    # mv
    unique = uuid.uuid4().hex[:8]
    new_uri = uri.rstrip("/") + f"_mv_{unique}/"
    resp = await client.post(
        "/api/v1/fs/mv",
        json={"from_uri": uri, "to_uri": new_uri},
    )
    assert resp.status_code == 200
    assert resp.json()["status"] == "ok"

    # rm (on the moved uri)
    resp = await client.request("DELETE", "/api/v1/fs", params={"uri": new_uri, "recursive": True})
    assert resp.status_code == 200
    assert resp.json()["status"] == "ok"
