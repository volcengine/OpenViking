# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: Apache-2.0

"""Tests for content write endpoint."""

import pytest


async def _first_file_uri(client, root_uri: str) -> str:
    resp = await client.get(
        "/api/v1/fs/ls",
        params={"uri": root_uri, "simple": True, "recursive": True, "output": "original"},
    )
    assert resp.status_code == 200
    children = resp.json().get("result", [])
    assert children
    return children[0]


async def test_write_endpoint_registered(client):
    resp = await client.get("/api/v1/content/write")
    assert resp.status_code == 405


async def test_write_rejects_directory_uri(client_with_resource):
    client, uri = client_with_resource
    resp = await client.post(
        "/api/v1/content/write",
        json={"uri": uri, "content": "new content"},
    )
    assert resp.status_code == 400
    body = resp.json()
    assert body["status"] == "error"
    assert body["error"]["code"] == "INVALID_ARGUMENT"


async def test_write_rejects_derived_file_uri(client_with_resource):
    client, uri = client_with_resource
    resp = await client.post(
        "/api/v1/content/write",
        json={"uri": f"{uri}/.overview.md", "content": "new content"},
    )
    assert resp.status_code == 400
    body = resp.json()
    assert body["status"] == "error"
    assert body["error"]["code"] == "INVALID_ARGUMENT"


async def test_write_replaces_existing_resource_file(client_with_resource):
    client, uri = client_with_resource
    file_uri = await _first_file_uri(client, uri)

    write_resp = await client.post(
        "/api/v1/content/write",
        json={
            "uri": file_uri,
            "content": "# Updated\n\nFresh content.",
            "mode": "replace",
            "wait": True,
        },
    )
    assert write_resp.status_code == 200
    body = write_resp.json()
    assert body["status"] == "ok"
    assert body["result"]["uri"] == file_uri
    assert body["result"]["mode"] == "replace"

    read_resp = await client.get("/api/v1/content/read", params={"uri": file_uri})
    assert read_resp.status_code == 200
    assert read_resp.json()["result"] == "# Updated\n\nFresh content."


async def test_write_appends_existing_resource_file(client_with_resource):
    client, uri = client_with_resource
    file_uri = await _first_file_uri(client, uri)
    original = (await client.get("/api/v1/content/read", params={"uri": file_uri})).json()["result"]

    write_resp = await client.post(
        "/api/v1/content/write",
        json={
            "uri": file_uri,
            "content": "\n\nAppended section.",
            "mode": "append",
            "wait": True,
        },
    )
    assert write_resp.status_code == 200

    read_resp = await client.get("/api/v1/content/read", params={"uri": file_uri})
    assert read_resp.status_code == 200
    assert read_resp.json()["result"] == original + "\n\nAppended section."


@pytest.mark.asyncio
async def test_write_missing_uri_validation(client):
    resp = await client.post("/api/v1/content/write", json={"content": "missing uri"})
    assert resp.status_code == 422


@pytest.mark.asyncio
async def test_write_rejects_removed_semantic_flags(client_with_resource):
    client, uri = client_with_resource
    file_uri = await _first_file_uri(client, uri)

    resp = await client.post(
        "/api/v1/content/write",
        json={
            "uri": file_uri,
            "content": "updated",
            "regenerate_semantics": False,
            "revectorize": False,
        },
    )

    assert resp.status_code == 422
