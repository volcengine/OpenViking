# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0

"""Tests for search endpoints: find, search, grep, glob."""

import httpx
import pytest

from openviking.models.embedder.base import EmbedResult


@pytest.fixture(autouse=True)
def fake_query_embedder(service):
    class FakeEmbedder:
        def embed(self, text: str, is_query: bool = False) -> EmbedResult:
            return EmbedResult(dense_vector=[0.1, 0.2, 0.3])

    service.viking_fs.query_embedder = FakeEmbedder()


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
    assert "usage" not in body
    assert "telemetry" not in body


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


async def test_find_telemetry_metrics(client_with_resource):
    client, _ = client_with_resource
    resp = await client.post(
        "/api/v1/search/find",
        json={"query": "sample document", "limit": 5, "telemetry": True},
    )
    assert resp.status_code == 200
    body = resp.json()
    summary = body["telemetry"]["summary"]
    assert summary["operation"] == "search.find"
    assert "duration_ms" in summary
    assert {"total", "llm", "embedding"}.issubset(summary["tokens"].keys())
    assert "vector" in summary
    assert summary["vector"]["searches"] >= 0
    assert "queue" not in summary
    assert "semantic_nodes" not in summary
    assert "memory" not in summary
    assert "usage" not in body
    assert body["telemetry"]["id"]
    assert body["telemetry"]["id"].startswith("tm_")


async def test_search_telemetry_metrics(client_with_resource):
    client, _ = client_with_resource
    resp = await client.post(
        "/api/v1/search/search",
        json={"query": "sample document", "limit": 5, "telemetry": True},
    )
    assert resp.status_code == 200
    body = resp.json()
    summary = body["telemetry"]["summary"]
    assert summary["operation"] == "search.search"
    assert summary["vector"]["returned"] >= 0
    assert "queue" not in summary
    assert "semantic_nodes" not in summary
    assert "memory" not in summary


async def test_find_summary_only_telemetry(client_with_resource):
    client, _ = client_with_resource
    resp = await client.post(
        "/api/v1/search/find",
        json={
            "query": "sample document",
            "limit": 5,
            "telemetry": {"summary": True},
        },
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["telemetry"]["summary"]["operation"] == "search.find"
    assert "usage" not in body
    assert "events" not in body["telemetry"]
    assert "truncated" not in body["telemetry"]
    assert "dropped" not in body["telemetry"]


async def test_find_rejects_events_telemetry_request(client_with_resource):
    client, _ = client_with_resource
    resp = await client.post(
        "/api/v1/search/find",
        json={
            "query": "sample document",
            "limit": 5,
            "telemetry": {"summary": False, "events": True},
        },
    )
    assert resp.status_code == 400
    body = resp.json()
    assert body["status"] == "error"
    assert body["error"]["code"] == "INVALID_ARGUMENT"
    assert "events" in body["error"]["message"]


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




async def test_grep_exclude_uri_excludes_specific_uri_range(
    client: httpx.AsyncClient,
    upload_temp_dir,
):
    include_file = upload_temp_dir / "include.md"
    include_file.write_text("# Include\n\nOpenViking should match here.\n")
    exclude_file = upload_temp_dir / "exclude.md"
    exclude_file.write_text("# Exclude\n\nOpenViking should be excluded here.\n")

    await client.post(
        "/api/v1/resources",
        json={"temp_file_id": include_file.name, "reason": "include"},
    )
    await client.post(
        "/api/v1/resources",
        json={"temp_file_id": exclude_file.name, "reason": "exclude"},
    )

    root_uri = "viking://resources"
    exclude_uri = "viking://resources/exclude.md"
    resp = await client.post(
        "/api/v1/search/grep",
        json={
            "uri": root_uri,
            "pattern": "OpenViking",
            "exclude_uri": exclude_uri,
        },
    )

    assert resp.status_code == 200
    body = resp.json()
    assert body["status"] == "ok"
    matches = body["result"]["matches"]
    assert matches
    assert all(not m["uri"].startswith(exclude_uri.rstrip('/')) for m in matches)


async def test_grep_exclude_uri_does_not_exclude_same_named_sibling_dirs(
    client: httpx.AsyncClient,
    upload_temp_dir,
):
    group_a_file = upload_temp_dir / "group_a_cache_a.md"
    group_a_file.write_text("# Group A\n\nOpenViking match in group A cache.\n")
    group_b_file = upload_temp_dir / "group_b_cache_b.md"
    group_b_file.write_text("# Group B\n\nOpenViking match in group B cache.\n")

    await client.post(
        "/api/v1/resources",
        json={
            "temp_file_id": group_a_file.name,
            "to": "viking://resources/group_a/cache/a.md",
            "reason": "test",
        },
    )
    await client.post(
        "/api/v1/resources",
        json={
            "temp_file_id": group_b_file.name,
            "to": "viking://resources/group_b/cache/b.md",
            "reason": "test",
        },
    )

    resp = await client.post(
        "/api/v1/search/grep",
        json={
            "uri": "viking://resources",
            "pattern": "OpenViking",
            "exclude_uri": "viking://resources/group_a/cache",
        },
    )

    assert resp.status_code == 200
    matches = resp.json()["result"]["matches"]
    uris = {m["uri"] for m in matches}
    assert any(uri.startswith("viking://resources/group_b/cache/") for uri in uris)
    assert all(not uri.startswith("viking://resources/group_a/cache/") for uri in uris)

async def test_glob(client_with_resource):
    client, _ = client_with_resource
    resp = await client.post(
        "/api/v1/search/glob",
        json={"pattern": "*.md"},
    )
    assert resp.status_code == 200
    assert resp.json()["status"] == "ok"
