# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: Apache-2.0

"""Tests for search endpoints: find, search, grep, glob."""

import httpx


async def test_find_basic(client_with_resource):
    client, uri = client_with_resource
    resp = await client.post(
        "/api/v1/search/find",
        json={"query": "sample document", "limit": 5},
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["status"] == "ok"
    assert body["result"] is not None


async def test_find_with_target_uri(client_with_resource):
    client, uri = client_with_resource
    resp = await client.post(
        "/api/v1/search/find",
        json={"query": "sample", "target_uri": uri, "limit": 5},
    )
    assert resp.status_code == 200
    assert resp.json()["status"] == "ok"


async def test_find_with_score_threshold(client_with_resource):
    client, uri = client_with_resource
    resp = await client.post(
        "/api/v1/search/find",
        json={
            "query": "sample document",
            "score_threshold": 0.01,
            "limit": 10,
        },
    )
    assert resp.status_code == 200
    assert resp.json()["status"] == "ok"


async def test_find_no_results(client: httpx.AsyncClient):
    resp = await client.post(
        "/api/v1/search/find",
        json={"query": "completely_random_nonexistent_xyz123"},
    )
    assert resp.status_code == 200
    assert resp.json()["status"] == "ok"


async def test_search_basic(client_with_resource):
    client, uri = client_with_resource
    resp = await client.post(
        "/api/v1/search/search",
        json={"query": "sample document", "limit": 5},
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["status"] == "ok"
    assert body["result"] is not None


async def test_search_with_session(client_with_resource):
    client, uri = client_with_resource
    # Create a session first
    sess_resp = await client.post(
        "/api/v1/sessions", json={"user": "test"}
    )
    session_id = sess_resp.json()["result"]["session_id"]

    resp = await client.post(
        "/api/v1/search/search",
        json={
            "query": "sample",
            "session_id": session_id,
            "limit": 5,
        },
    )
    assert resp.status_code == 200
    assert resp.json()["status"] == "ok"


async def test_grep(client_with_resource):
    client, uri = client_with_resource
    parent_uri = "/".join(uri.split("/")[:-1]) + "/"
    resp = await client.post(
        "/api/v1/search/grep",
        json={"uri": parent_uri, "pattern": "Sample"},
    )
    assert resp.status_code == 200
    assert resp.json()["status"] == "ok"


async def test_grep_case_insensitive(client_with_resource):
    client, uri = client_with_resource
    parent_uri = "/".join(uri.split("/")[:-1]) + "/"
    resp = await client.post(
        "/api/v1/search/grep",
        json={
            "uri": parent_uri,
            "pattern": "sample",
            "case_insensitive": True,
        },
    )
    assert resp.status_code == 200
    assert resp.json()["status"] == "ok"


async def test_glob(client_with_resource):
    client, _ = client_with_resource
    resp = await client.post(
        "/api/v1/search/glob",
        json={"pattern": "*.md"},
    )
    assert resp.status_code == 200
    assert resp.json()["status"] == "ok"
