# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: Apache-2.0

"""Tests for content endpoints: read, abstract, overview."""


async def test_read_content(client_with_resource):
    client, uri = client_with_resource
    # The resource URI may be a directory; list children to find the file
    ls_resp = await client.get(
        "/api/v1/fs/ls", params={"uri": uri, "simple": True, "recursive": True}
    )
    children = ls_resp.json().get("result", [])
    # Find a file (non-directory) to read
    file_uri = None
    if children:
        file_uri = uri.rstrip("/") + "/" + children[0] if isinstance(children[0], str) else None
    if file_uri is None:
        file_uri = uri

    resp = await client.get(
        "/api/v1/content/read", params={"uri": file_uri}
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["status"] == "ok"
    assert body["result"] is not None


async def test_abstract_content(client_with_resource):
    client, uri = client_with_resource
    resp = await client.get(
        "/api/v1/content/abstract", params={"uri": uri}
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["status"] == "ok"


async def test_overview_content(client_with_resource):
    client, uri = client_with_resource
    resp = await client.get(
        "/api/v1/content/overview", params={"uri": uri}
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["status"] == "ok"
