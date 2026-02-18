# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: Apache-2.0

"""Tests for search endpoints: find, search, grep, glob."""

import shutil

import httpx
import pytest


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
    sess_resp = await client.post("/api/v1/sessions", json={"user": "test"})
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


async def test_ast_grep(client, service):
    async def fake_ast_grep(**kwargs):
        return {
            "matches": [
                {
                    "uri": "viking://resources/sample.md",
                    "language": "markdown",
                    "start_line": 1,
                    "start_col": 1,
                    "end_line": 1,
                    "end_col": 8,
                    "content": "# Sample",
                }
            ],
            "count": 1,
            "scanned_files": 1,
            "skipped_files": 0,
            "truncated": False,
        }

    service.fs.ast_grep = fake_ast_grep

    resp = await client.post(
        "/api/v1/search/ast-grep",
        json={"uri": "viking://resources/", "pattern": "$X"},
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["status"] == "ok"
    assert body["result"]["count"] == 1


async def test_ast_grep_invalid_arguments(client):
    resp = await client.post(
        "/api/v1/search/ast-grep",
        json={"uri": "viking://resources/"},
    )
    assert resp.status_code == 400
    body = resp.json()
    assert body["status"] == "error"
    assert body["error"]["code"] == "INVALID_ARGUMENT"


async def test_ast_grep_real_engine(client, service):
    if shutil.which("sg") is None:
        pytest.skip("ast-grep binary 'sg' is not installed")

    file_uri = "viking://resources/ast_grep_real/sub/sample.py"
    await service.viking_fs.write_file(
        file_uri,
        "def greet(name):\n    return f'hello {name}'\n",
    )

    resp = await client.post(
        "/api/v1/search/ast-grep",
        json={
            "uri": "viking://resources/ast_grep_real/",
            "pattern": "def $NAME($$$ARGS): $$$BODY",
            "file_glob": "**/*.py",
            "limit": 10,
        },
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["status"] == "ok"
    assert body["result"]["count"] >= 1
    assert body["result"]["scanned_files"] >= 1
    assert any(m["uri"] == file_uri for m in body["result"]["matches"])
