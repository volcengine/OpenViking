# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0

"""Tests for filesystem endpoints: ls, tree, stat, mkdir, rm, mv."""

import httpx

from openviking.pyagfs.exceptions import AGFSHTTPError
from openviking.storage.errors import ResourceBusyError


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


async def test_mkdir_with_description_initializes_abstract_and_enqueues_l0(
    client: httpx.AsyncClient,
    monkeypatch,
):
    seen = {}

    async def _fake_vectorize_directory_meta(**kwargs):
        seen.update(kwargs)

    monkeypatch.setattr(
        "openviking.service.fs_service.vectorize_directory_meta",
        _fake_vectorize_directory_meta,
    )

    uri = "viking://resources/described_dir/"
    description = "Directory for API docs"
    resp = await client.post(
        "/api/v1/fs/mkdir",
        json={"uri": uri, "description": description},
    )
    assert resp.status_code == 200
    assert resp.json()["status"] == "ok"

    abstract_resp = await client.get(
        "/api/v1/content/abstract",
        params={"uri": uri},
    )
    assert abstract_resp.status_code == 200
    assert abstract_resp.json()["result"] == description
    assert seen["uri"] == "viking://resources/described_dir"
    assert seen["abstract"] == description
    assert seen["overview"] == ""
    assert seen["context_type"] == "resource"
    assert seen["include_overview"] is False
    assert seen["ctx"] is not None


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
    assert resp.status_code == 404
    body = resp.json()
    assert body["status"] == "error"


async def test_rm_directory_without_recursive_returns_failed_precondition(client: httpx.AsyncClient):
    await client.post(
        "/api/v1/fs/mkdir",
        json={"uri": "viking://resources/rm_dir_without_recursive/"},
    )
    resp = await client.request(
        "DELETE",
        "/api/v1/fs",
        params={"uri": "viking://resources/rm_dir_without_recursive"},
    )
    assert resp.status_code == 412
    body = resp.json()
    assert body["status"] == "error"
    assert body["error"]["code"] == "FAILED_PRECONDITION"


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


async def test_rm_maps_resource_busy_to_conflict(client, service, monkeypatch):
    async def fake_rm(uri, ctx=None, recursive=False):
        raise ResourceBusyError(f"Resource is being processed: {uri}")

    monkeypatch.setattr(service.fs, "rm", fake_rm)
    resp = await client.request(
        "DELETE",
        "/api/v1/fs",
        params={"uri": "viking://resources/locked", "recursive": True},
    )
    assert resp.status_code == 409
    body = resp.json()
    assert body["status"] == "error"
    assert body["error"]["code"] == "CONFLICT"


async def test_rm_agfs_internal_error_does_not_look_successful(client, service, monkeypatch):
    class FakeAGFS:
        def __init__(self, wrapped):
            self._wrapped = wrapped

        def stat(self, path):
            raise AGFSHTTPError("Internal server error", 500)

        def __getattr__(self, name):
            return getattr(self._wrapped, name)

    monkeypatch.setattr(service.fs._viking_fs, "agfs", FakeAGFS(service.fs._viking_fs.agfs))
    resp = await client.request(
        "DELETE",
        "/api/v1/fs",
        params={"uri": "viking://resources/delete-failure", "recursive": True},
    )
    assert resp.status_code == 503
    body = resp.json()
    assert body["status"] == "error"
    assert body["error"]["code"] == "UNAVAILABLE"
