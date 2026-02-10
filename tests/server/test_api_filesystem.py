# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: Apache-2.0

"""Tests for filesystem endpoints: ls, tree, stat, mkdir, rm, mv."""

import httpx


async def test_ls_root(client: httpx.AsyncClient):
    resp = await client.get(
        "/api/v1/fs/ls", params={"uri": "viking://"}
    )
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
    resp = await client.get(
        "/api/v1/fs/tree", params={"uri": "viking://"}
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["status"] == "ok"


async def test_stat_after_add_resource(client_with_resource):
    client, uri = client_with_resource
    resp = await client.get(
        "/api/v1/fs/stat", params={"uri": uri}
    )
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


async def test_rm_resource(client_with_resource):
    client, uri = client_with_resource
    resp = await client.request(
        "DELETE", "/api/v1/fs", params={"uri": uri, "recursive": True}
    )
    assert resp.status_code == 200
    assert resp.json()["status"] == "ok"


async def test_mv_resource(client_with_resource):
    import uuid

    client, uri = client_with_resource
    # Use a unique name to avoid conflicts with leftover data
    unique = uuid.uuid4().hex[:8]
    new_uri = uri.rstrip("/") + f"_mv_{unique}/"
    resp = await client.post(
        "/api/v1/fs/mv",
        json={"from_uri": uri, "to_uri": new_uri},
    )
    assert resp.status_code == 200
    assert resp.json()["status"] == "ok"


async def test_ls_recursive(client_with_resource):
    client, _ = client_with_resource
    resp = await client.get(
        "/api/v1/fs/ls",
        params={"uri": "viking://", "recursive": True},
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["status"] == "ok"
    assert isinstance(body["result"], list)
