# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0

"""Tests for search endpoints: find, search, grep, glob."""

import json
from datetime import datetime, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock

import httpx
import pytest

from openviking.models.embedder.base import EmbedResult
from openviking.server.auth import get_request_context
from openviking.server.identity import RequestContext, Role
from openviking.server.routers import search as search_router
from openviking.storage.vectordb.collection.vikingdb_clients import VikingDBClient
from openviking.storage.viking_fs import VikingFS
from openviking.storage.viking_vector_index_backend import _SingleAccountBackend
from openviking.utils.time_utils import parse_iso_datetime
from openviking_cli.exceptions import InvalidArgumentError
from openviking_cli.retrieve import ContextType, FindResult, MatchedContext
from openviking_cli.session.user_id import UserIdentifier
from openviking_cli.utils.config.vectordb_config import VectorDBBackendConfig, VikingDBConfig


async def test_keyword_search_sends_args_to_vikingdb(
    client: httpx.AsyncClient, service, monkeypatch
):
    captured = {}
    uri = "viking://resources/oauth.md"

    def fake_request(_client, method, path, *, req_body):
        if path == "/api/vikingdb/GetCollection":
            return httpx.Response(
                200,
                json={
                    "Result": {
                        "CollectionName": "context",
                        "Fields": [{"FieldName": "content", "FieldType": "text"}],
                        "FullText": [{"Field": "content"}],
                    }
                },
            )
        assert (method, path) == ("POST", "/api/vikingdb/data/search/keywords")
        captured.update(req_body)
        return httpx.Response(
            200,
            json={
                "result": {
                    "data": [
                        {
                            "id": "oauth",
                            "score": 2.5,
                            "fields": {"uri": uri, "context_type": "resource", "level": 2},
                        }
                    ]
                }
            },
        )

    backend = _SingleAccountBackend(
        VectorDBBackendConfig(
            backend="vikingdb", vikingdb=VikingDBConfig(host="http://vikingdb.invalid")
        ),
        bound_account_id="default",
    )
    monkeypatch.setattr(VikingDBClient, "do_req", fake_request)
    monkeypatch.setattr(service.viking_fs, "rerank_config", None)
    monkeypatch.setattr(
        service.viking_fs.vector_store, "get_account_backend", AsyncMock(return_value=backend)
    )
    payload = {
        "query": "OAuth token",
        "search_type": "keywords",
        "limit": 2,
        "node_limit": 5,
        "args": {"bm25_k1": 1.2, "bm25_b": 0.2, "post_process_input_limit": 4},
    }
    try:
        rejected = await client.post("/api/v1/search/search", json=payload)
        assert rejected.status_code == 400
        assert rejected.json()["error"]["code"] == "INVALID_ARGUMENT"
        assert not captured

        payload["args"]["post_process_input_limit"] = 5
        response = await client.post("/api/v1/search/search", json=payload)
    finally:
        await backend.close()

    assert response.status_code == 200
    assert [hit["uri"] for hit in response.json()["result"]["resources"]] == [uri]
    assert captured["query"] == "OAuth token"
    assert captured["mode"] == "bm25"
    assert captured["limit"] == 5
    assert captured["bm25_k1"] == 1.2
    assert captured["bm25_b"] == 0.2
    assert captured["advance"] == {"post_process_input_limit": 5}


@pytest.mark.parametrize(
    "payload",
    [
        {"query": "", "search_type": "keywords"},
        {
            "query": "OAuth token",
            "image_url": "https://example.com/image.png",
            "search_type": "keywords",
        },
    ],
)
async def test_search_rejects_invalid_keywords_inputs(client: httpx.AsyncClient, payload):
    response = await client.post("/api/v1/search/search", json=payload)

    assert response.status_code == 400
    assert response.json()["error"]["code"] == "INVALID_ARGUMENT"


@pytest.mark.parametrize("endpoint", ["find", "search"])
async def test_search_endpoints_inline_visible_content_when_requested(
    client_with_resource, sample_markdown_file, endpoint
):
    client, root = client_with_resource
    payload = {"query": "sample", "target_uri": root, "level": 2}

    default = await client.post(f"/api/v1/search/{endpoint}", json=payload)
    assert default.status_code == 200, default.text
    body = default.json()
    assert "usage" not in body
    assert "telemetry" not in body
    [hit] = body["result"]["resources"]
    assert hit["uri"] == f"{root}/sample.md"
    assert "content" not in hit

    expanded = await client.post(
        f"/api/v1/search/{endpoint}", json={**payload, "read_content": True}
    )
    assert expanded.status_code == 200, expanded.text
    assert expanded.json()["result"]["resources"] == [
        {**hit, "content": sample_markdown_file.read_text()}
    ]


async def test_find_keeps_hit_without_content_when_read_fails(
    client_with_resource, service, monkeypatch
):
    client, root = client_with_resource
    uri = f"{root}/sample.md"
    read_visible = AsyncMock(side_effect=OSError("unavailable"))
    monkeypatch.setattr(service.fs, "read_visible", read_visible)

    response = await client.post(
        "/api/v1/search/find",
        json={"query": "sample", "target_uri": root, "level": 2, "read_content": True},
    )

    assert response.status_code == 200, response.text
    result = response.json()["result"]
    [hit] = result["resources"]
    assert result["total"] == 1
    assert hit["uri"] == uri
    assert "content" not in hit
    [read_call] = read_visible.await_args_list
    assert read_call.args == (uri,)


async def test_search_context_rejects_read_content(client: httpx.AsyncClient):
    response = await client.post(
        "/api/v1/search/search",
        json={"query": "visible", "mode": "context", "read_content": True},
    )

    assert response.status_code == 400
    assert response.json()["error"]["code"] == "INVALID_ARGUMENT"


@pytest.mark.parametrize(
    ("endpoint", "service_method"),
    [
        ("/api/v1/search/find", "find"),
        ("/api/v1/search/search", "search"),
    ],
)
async def test_search_endpoints_filter_invalid_result_tags(
    client: httpx.AsyncClient, service, monkeypatch, endpoint: str, service_method: str
):
    async def fake_search(**kwargs):
        del kwargs
        return FindResult(
            memories=[],
            resources=[
                MatchedContext(
                    uri="viking://resources/legacy-tags.md",
                    context_type=ContextType.RESOURCE,
                    search_tags=["default", "team=infra", "bad=", "project=viking"],
                )
            ],
            skills=[],
        )

    monkeypatch.setattr(service.search, service_method, fake_search)

    response = await client.post(endpoint, json={"query": "legacy tags"})

    assert response.status_code == 200
    assert response.json()["result"]["resources"][0]["tags"] == [
        "team=infra",
        "project=viking",
    ]


@pytest.mark.parametrize("endpoint", ["/api/v1/search/find", "/api/v1/search/search"])
async def test_search_endpoints_reject_unknown_request_fields(
    client: httpx.AsyncClient,
    endpoint: str,
):
    resp = await client.post(
        endpoint,
        json={"query": "sample document", "unexpected": "value"},
    )

    assert resp.status_code == 400


async def test_find_with_target_uri_and_tags_passes_target_and_filter(
    client: httpx.AsyncClient, service, monkeypatch
):
    captured = {}

    async def fake_find(*, target_uri=None, filter=None, **kwargs):
        captured["target_uri"] = target_uri
        captured["filter"] = filter
        return {"items": []}

    monkeypatch.setattr(service.search, "find", fake_find)

    resp = await client.post(
        "/api/v1/search/find",
        json={
            "query": "sample",
            "target_uri": "viking://resources/sample",
            "tags": ["team=search", "env=prod"],
            "limit": 10,
        },
    )

    assert resp.status_code == 200
    assert resp.json()["status"] == "ok"
    assert captured["target_uri"] == "viking://resources/sample"
    assert captured["filter"] == {
        "op": "and",
        "conds": [
            {"op": "must", "field": "search_tags", "conds": ["team=search"]},
            {"op": "must", "field": "search_tags", "conds": ["env=prod"]},
        ],
    }


@pytest.mark.parametrize("endpoint", ["find", "search"])
async def test_search_level_selects_indexed_resources(client_with_resource, endpoint):
    client, root = client_with_resource
    abstract = (f"{root}/.abstract.md", 0)
    overview = (f"{root}/.overview.md", 1)
    leaf = (f"{root}/sample.md", 2)
    payload = {"query": "sample document", "target_uri": root}

    for level, expected in (
        (2, {leaf}),
        ([0], {abstract}),
        ([1], {overview}),
        ("0,2", {abstract, leaf}),
        ([1, 2], {overview, leaf}),
        ([5], set()),
    ):
        response = await client.post(f"/api/v1/search/{endpoint}", json={**payload, "level": level})
        assert response.status_code == 200, response.text
        result = response.json()["result"]
        assert {(hit["uri"], hit["level"]) for hit in result["resources"]} == expected
        assert result["total"] == len(expected)

    summaries = await client.post(f"/api/v1/search/{endpoint}", json={**payload, "level": [0, 1]})
    assert summaries.status_code == 200, summaries.text
    hits = {(hit["uri"], hit["level"]) for hit in summaries.json()["result"]["resources"]}
    assert hits and hits <= {abstract, overview}

    response = await client.post(f"/api/v1/search/{endpoint}", json=payload)
    assert response.status_code == 200, response.text
    hits = {(hit["uri"], hit["level"]) for hit in response.json()["result"]["resources"]}
    assert leaf in hits
    assert hits & {abstract, overview}


async def test_find_with_inaccessible_target_uri_returns_permission_denied(
    client: httpx.AsyncClient, app
):
    app.dependency_overrides[get_request_context] = lambda: RequestContext(
        user=UserIdentifier.the_default_user(),
        role=Role.USER,
    )
    try:
        resp = await client.post(
            "/api/v1/search/find",
            json={"query": "sample", "target_uri": "viking://user/foreign/memories", "limit": 5},
        )
    finally:
        app.dependency_overrides.pop(get_request_context, None)

    assert resp.status_code == 403
    body = resp.json()
    assert body["status"] == "error"
    assert body["error"]["code"] == "PERMISSION_DENIED"
    assert "Access denied" in body["error"]["message"]


async def test_find_with_score_threshold(client_with_resource):
    client, root = client_with_resource
    payload = {"query": "sample document", "target_uri": root, "level": 2}

    baseline = await client.post("/api/v1/search/find", json=payload)
    assert baseline.status_code == 200, baseline.text
    [hit] = baseline.json()["result"]["resources"]
    assert hit["uri"] == f"{root}/sample.md"

    response = await client.post(
        "/api/v1/search/find", json={**payload, "score_threshold": hit["score"]}
    )
    assert response.status_code == 200, response.text
    result = response.json()["result"]
    assert result["total"] == 0
    assert result["resources"] == result["memories"] == result["skills"] == []


async def test_find_no_results(client: httpx.AsyncClient):
    response = await client.post(
        "/api/v1/search/find",
        json={"query": "sample document", "target_uri": "viking://resources", "level": 2},
    )

    assert response.status_code == 200, response.text
    assert response.json()["result"] == {"memories": [], "resources": [], "skills": [], "total": 0}


@pytest.mark.parametrize("endpoint", ["find", "search"])
@pytest.mark.parametrize(
    ("query", "message"),
    [
        ("", "must not be empty"),
        ("   \t\n", "must not be empty"),
        ("broken\ud8b7", "valid Unicode"),
    ],
)
async def test_search_rejects_invalid_query(
    client: httpx.AsyncClient, service, monkeypatch, endpoint, query, message
):
    provider = service.viking_fs._embedding_provider
    embed = AsyncMock(wraps=provider.embed)
    monkeypatch.setattr(provider, "embed", embed)

    resp = await client.post(
        f"/api/v1/search/{endpoint}",
        content=json.dumps({"query": query}),
        headers={"Content-Type": "application/json"},
    )

    assert resp.status_code == 400
    body = resp.json()
    assert body["status"] == "error"
    assert body["error"]["code"] == "INVALID_ARGUMENT"
    assert message in body["error"]["message"]
    assert not any(call.kwargs.get("is_query", False) for call in embed.await_args_list)


@pytest.mark.parametrize("method_name", ["find", "search"])
async def test_vikingfs_rejects_empty_query_before_initialization(method_name: str):
    viking_fs = VikingFS.__new__(VikingFS)
    method = getattr(viking_fs, method_name)

    with pytest.raises(InvalidArgumentError, match="must not be empty"):
        await method(query=" ")


async def test_ranked_search_requires_query_with_context_type_filter(client: httpx.AsyncClient):
    resp = await client.post("/api/v1/search/search", json={"context_type": ["resource"]})

    assert resp.status_code == 400
    error = resp.json()["error"]
    assert error["code"] == "INVALID_ARGUMENT"
    assert "must not be empty" in error["message"]


async def test_find_with_since_compiles_time_range(client: httpx.AsyncClient, service, monkeypatch):
    captured = {}

    async def fake_find(*, filter=None, **kwargs):
        captured["filter"] = filter
        captured["kwargs"] = kwargs
        return {"items": []}

    monkeypatch.setattr(service.search, "find", fake_find)

    resp = await client.post(
        "/api/v1/search/find",
        json={"query": "sample", "since": "2h"},
    )

    assert resp.status_code == 200
    assert resp.json()["status"] == "ok"
    assert captured["filter"]["op"] == "time_range"
    assert captured["filter"]["field"] == "updated_at"
    gte = parse_iso_datetime(captured["filter"]["gte"])
    delta = datetime.now(timezone.utc) - gte
    assert 7_100 <= delta.total_seconds() <= 7_300


async def test_find_combines_existing_filter_with_time_range(
    client: httpx.AsyncClient, service, monkeypatch
):
    captured = {}

    async def fake_find(*, filter=None, **kwargs):
        captured["filter"] = filter
        return {"items": []}

    monkeypatch.setattr(service.search, "find", fake_find)

    resp = await client.post(
        "/api/v1/search/find",
        json={
            "query": "sample",
            "filter": {"op": "must", "field": "kind", "conds": ["email"]},
            "since": "2026-03-10",
            "time_field": "created_at",
        },
    )

    assert resp.status_code == 200
    assert resp.json()["status"] == "ok"
    assert captured["filter"] == {
        "op": "and",
        "conds": [
            {"op": "must", "field": "kind", "conds": ["email"]},
            {
                "op": "time_range",
                "field": "created_at",
                "gte": "2026-03-10T00:00:00.000Z",
            },
        ],
    }


async def test_find_with_context_type_compiles_filter(
    client: httpx.AsyncClient, service, monkeypatch
):
    captured = {}

    async def fake_find(*, filter=None, **kwargs):
        captured["filter"] = filter
        return {"items": []}

    monkeypatch.setattr(service.search, "find", fake_find)

    resp = await client.post(
        "/api/v1/search/find",
        json={"query": "sample", "context_type": "memory"},
    )

    assert resp.status_code == 200
    assert resp.json()["status"] == "ok"
    assert captured["filter"] == {"op": "must", "field": "context_type", "conds": ["memory"]}


async def test_find_combines_tags_with_existing_filter(
    client: httpx.AsyncClient, service, monkeypatch
):
    captured = {}

    async def fake_find(*, filter=None, **kwargs):
        captured["filter"] = filter
        return {"items": []}

    monkeypatch.setattr(service.search, "find", fake_find)

    resp = await client.post(
        "/api/v1/search/find",
        json={
            "query": "sample",
            "filter": {"op": "must", "field": "kind", "conds": ["email"]},
            "tags": ["Env=Prod", " env=prod ", "team=Search"],
        },
    )

    assert resp.status_code == 200
    assert resp.json()["status"] == "ok"
    assert captured["filter"] == {
        "op": "and",
        "conds": [
            {"op": "must", "field": "kind", "conds": ["email"]},
            {"op": "must", "field": "search_tags", "conds": ["env=prod"]},
            {"op": "must", "field": "search_tags", "conds": ["team=search"]},
        ],
    }


async def test_search_combines_context_type_list_with_existing_filter(
    client: httpx.AsyncClient, service, monkeypatch
):
    captured = {}

    async def fake_search(*, filter=None, **kwargs):
        captured["filter"] = filter
        return {"items": []}

    monkeypatch.setattr(service.search, "search", fake_search)

    resp = await client.post(
        "/api/v1/search/search",
        json={
            "query": "sample",
            "context_type": ["memory", "resource"],
            "filter": {"op": "must", "field": "kind", "conds": ["email"]},
        },
    )

    assert resp.status_code == 200
    assert resp.json()["status"] == "ok"
    assert captured["filter"] == {
        "op": "and",
        "conds": [
            {"op": "must", "field": "kind", "conds": ["email"]},
            {"op": "must", "field": "context_type", "conds": ["memory", "resource"]},
        ],
    }


async def test_search_compiles_tags_only_filter(client: httpx.AsyncClient, service, monkeypatch):
    captured = {}

    async def fake_search(*, filter=None, **kwargs):
        captured["filter"] = filter
        return {"items": []}

    monkeypatch.setattr(service.search, "search", fake_search)

    resp = await client.post(
        "/api/v1/search/search",
        json={"query": "sample", "tags": ["Team=Search", "env=prod"]},
    )

    assert resp.status_code == 200
    assert resp.json()["status"] == "ok"
    assert captured["filter"] == {
        "op": "and",
        "conds": [
            {"op": "must", "field": "search_tags", "conds": ["team=search"]},
            {"op": "must", "field": "search_tags", "conds": ["env=prod"]},
        ],
    }


async def test_find_with_invalid_context_type_returns_invalid_argument(client: httpx.AsyncClient):
    resp = await client.post(
        "/api/v1/search/find",
        json={"query": "sample", "context_type": "archive"},
    )

    assert resp.status_code == 400
    body = resp.json()
    assert body["status"] == "error"
    assert body["error"]["code"] == "INVALID_ARGUMENT"
    assert "context_type" in body["error"]["message"]


async def test_search_rejects_invalid_kv_tags(client: httpx.AsyncClient):
    resp = await client.post(
        "/api/v1/search/search",
        json={"query": "sample", "tags": ["team-search"]},
    )

    assert resp.status_code == 400
    body = resp.json()
    assert body["status"] == "error"
    assert body["error"]["code"] == "INVALID_ARGUMENT"


async def test_find_with_invalid_time_returns_invalid_argument(client: httpx.AsyncClient):
    resp = await client.post(
        "/api/v1/search/find",
        json={"query": "sample", "since": "not-a-time"},
    )

    assert resp.status_code == 400
    body = resp.json()
    assert body["status"] == "error"
    assert body["error"]["code"] == "INVALID_ARGUMENT"
    assert body["error"]["message"]


@pytest.mark.parametrize("endpoint", ["/api/v1/search/find", "/api/v1/search/search"])
@pytest.mark.parametrize(
    "fields",
    [
        {"time_field": "published_at", "since": "2h"},
        {"limit": 0},
        {"limit": -1},
        {"node_limit": 0},
        {"node_limit": -1},
        {"limit": 0, "node_limit": 5},
    ],
)
async def test_search_rejects_invalid_request_fields(client: httpx.AsyncClient, endpoint, fields):
    resp = await client.post(
        endpoint,
        json={"query": "sample", **fields},
    )

    assert resp.status_code == 400
    body = resp.json()
    assert body["status"] == "error"
    assert body["error"]["code"] == "INVALID_ARGUMENT"
    assert body["error"]["message"]
    assert body["error"]["details"]["validation_errors"]


async def test_find_with_inverted_mixed_time_range_returns_invalid_argument(
    client: httpx.AsyncClient,
):
    resp = await client.post(
        "/api/v1/search/find",
        json={"query": "sample", "since": "2099-01-01", "until": "2h"},
    )

    assert resp.status_code == 400
    body = resp.json()
    assert body["status"] == "error"
    assert body["error"]["code"] == "INVALID_ARGUMENT"
    assert "earlier than or equal to" in body["error"]["message"]


async def test_search_with_empty_session_uses_the_original_query(
    client_with_resource, service, monkeypatch
):
    client, root = client_with_resource
    created = await client.post("/api/v1/sessions", json={})
    assert created.status_code == 200, created.text
    session_id = created.json()["result"]["session_id"]
    planner = AsyncMock()
    monkeypatch.setattr(service.viking_fs._vlm_resolver, "get_query_planner", planner)

    response = await client.post(
        "/api/v1/search/search",
        json={"query": "sample", "session_id": session_id, "target_uri": root, "level": 2},
    )

    assert response.status_code == 200, response.text
    result = response.json()["result"]
    assert [hit["uri"] for hit in result["resources"]] == [f"{root}/sample.md"]
    assert "query_plan" not in result
    planner.assert_not_awaited()


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
    assert "tokens" not in summary
    assert "vector" in summary
    assert summary["vector"]["searches"] >= 0
    assert "search" in summary
    assert "vector_retrieval" in summary["search"]
    assert "result_convert" not in summary["search"]
    assert "request" not in summary["search"]
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
    assert "search" in summary
    assert "vector_retrieval" in summary["search"]
    assert "result_convert" not in summary["search"]
    assert "request" not in summary["search"]
    if body["result"]["total"] > 0:
        assert summary["vector"]["returned"] == body["result"]["total"]
    else:
        assert "returned" not in summary["vector"]
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


async def test_search_with_until_compiles_time_range(
    client: httpx.AsyncClient, service, monkeypatch
):
    captured = {}

    async def fake_search(*, filter=None, **kwargs):
        captured["filter"] = filter
        return {"items": []}

    monkeypatch.setattr(service.search, "search", fake_search)

    resp = await client.post(
        "/api/v1/search/search",
        json={"query": "sample", "until": "2026-03-11", "time_field": "created_at"},
    )

    assert resp.status_code == 200
    assert resp.json()["status"] == "ok"
    assert captured["filter"] == {
        "op": "time_range",
        "field": "created_at",
        "lte": "2026-03-11T23:59:59.999Z",
    }


async def test_grep(client_with_resource):
    client, uri = client_with_resource
    parent_uri = "/".join(uri.split("/")[:-1]) + "/"
    resp = await client.post(
        "/api/v1/search/grep",
        json={
            "uri": parent_uri,
            "pattern": "Introduction",
            "before_context": 1,
            "after_context": 1,
        },
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["status"] == "ok"
    match = body["result"]["matches"][0]
    assert match["before_context"] == [{"line": 2, "content": ""}]
    assert match["after_context"] == [
        {"line": 4, "content": "This is a sample markdown document for server testing."}
    ]


@pytest.mark.asyncio
async def test_grep_forwards_tags_to_filesystem_service(monkeypatch):
    seen = {}

    async def fake_grep(uri, pattern, **kwargs):
        seen.update(uri=uri, pattern=pattern, **kwargs)
        return {"matches": [], "count": 0, "match_count": 0, "files_scanned": 0}

    monkeypatch.setattr(
        search_router,
        "get_service",
        lambda: SimpleNamespace(fs=SimpleNamespace(grep=fake_grep)),
    )

    await search_router.grep(
        search_router.GrepRequest(
            uri="viking://resources", pattern="OpenViking", tags=["team=search", "env=prod"]
        ),
        _ctx=RequestContext(user=UserIdentifier("acct", "alice"), role=Role.USER),
    )

    assert seen["tags"] == ["team=search", "env=prod"]


@pytest.mark.asyncio
async def test_grep_forwards_include_tags_to_filesystem_service(monkeypatch):
    seen = {}

    async def fake_grep(uri, pattern, **kwargs):
        seen.update(uri=uri, pattern=pattern, **kwargs)
        return {"matches": [], "count": 0, "match_count": 0, "files_scanned": 0}

    monkeypatch.setattr(
        search_router,
        "get_service",
        lambda: SimpleNamespace(fs=SimpleNamespace(grep=fake_grep)),
    )

    await search_router.grep(
        search_router.GrepRequest(
            uri="viking://resources", pattern="OpenViking", include_tags=True
        ),
        _ctx=RequestContext(user=UserIdentifier("acct", "alice"), role=Role.USER),
    )

    assert seen["include_tags"] is True


@pytest.mark.asyncio
async def test_grep_forwards_context_to_filesystem_service(monkeypatch):
    seen = {}

    async def fake_grep(uri, pattern, **kwargs):
        seen.update(uri=uri, pattern=pattern, **kwargs)
        return {"matches": [], "count": 0, "match_count": 0, "files_scanned": 0}

    monkeypatch.setattr(
        search_router,
        "get_service",
        lambda: SimpleNamespace(fs=SimpleNamespace(grep=fake_grep)),
    )

    await search_router.grep(
        search_router.GrepRequest(
            uri="viking://resources",
            pattern="OpenViking",
            before_context=2,
            after_context=3,
        ),
        _ctx=RequestContext(user=UserIdentifier("acct", "alice"), role=Role.USER),
    )

    assert seen["before_context"] == 2
    assert seen["after_context"] == 3


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


async def test_grep_missing_uri_returns_not_found(client: httpx.AsyncClient):
    resp = await client.post(
        "/api/v1/search/grep",
        json={
            "uri": "viking://resources/nonexistent_grep_test_xyz",
            "pattern": "test",
        },
    )

    assert resp.status_code == 404
    assert resp.json()["status"] == "error"


async def test_grep_level_limit_filters_by_relative_match_path(
    client: httpx.AsyncClient,
    upload_temp_dir,
):
    root_file = upload_temp_dir / "root_level.md"
    root_file.write_text("OpenViking on root level\n")
    deep_file = upload_temp_dir / "deep_level.md"
    deep_file.write_text("OpenViking in deep level\n")

    await client.post(
        "/api/v1/resources",
        json={
            "temp_file_id": root_file.name,
            "to": "viking://resources/level-limit/root_level.md",
            "reason": "test",
            "wait": True,
        },
    )
    await client.post(
        "/api/v1/resources",
        json={
            "temp_file_id": deep_file.name,
            "to": "viking://resources/level-limit/nested/deeper/deep_level.md",
            "reason": "test",
            "wait": True,
        },
    )

    resp = await client.post(
        "/api/v1/search/grep",
        json={
            "uri": "viking://resources/level-limit",
            "pattern": "OpenViking",
            "level_limit": 2,
        },
    )

    assert resp.status_code == 200
    body = resp.json()
    assert body["status"] == "ok"
    uris = {m["uri"] for m in body["result"]["matches"]}
    assert "viking://resources/level-limit/root_level.md/root_level.md" in uris
    assert "viking://resources/level-limit/nested/deeper/deep_level.md/deep_level.md" not in uris


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
        json={"temp_file_id": include_file.name, "reason": "include", "wait": True},
    )
    await client.post(
        "/api/v1/resources",
        json={"temp_file_id": exclude_file.name, "reason": "exclude", "wait": True},
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
    assert all(not m["uri"].startswith(exclude_uri.rstrip("/")) for m in matches)


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
            "wait": True,
        },
    )
    await client.post(
        "/api/v1/resources",
        json={
            "temp_file_id": group_b_file.name,
            "to": "viking://resources/group_b/cache/b.md",
            "reason": "test",
            "wait": True,
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
        json={"pattern": "**/*.md"},
    )
    assert resp.status_code == 200
    assert resp.json()["status"] == "ok"
    assert all(isinstance(match, str) for match in resp.json()["result"]["matches"])


async def test_glob_explicit_empty_extra_fields_returns_entry_objects(client_with_resource):
    client, _ = client_with_resource
    resp = await client.post(
        "/api/v1/search/glob",
        json={"pattern": "**/*.md", "extra_fields": []},
    )

    assert resp.status_code == 200
    matches = resp.json()["result"]["matches"]
    assert matches
    assert all(isinstance(match, dict) for match in matches)
    assert all("uri" in match and "isDir" in match for match in matches)


@pytest.mark.asyncio
async def test_glob_forwards_tags_and_tag_projection_to_filesystem_service(monkeypatch):
    seen = {}

    async def fake_glob(pattern, **kwargs):
        seen.update(pattern=pattern, **kwargs)
        return {"matches": [], "count": 0}

    monkeypatch.setattr(
        search_router,
        "get_service",
        lambda: SimpleNamespace(fs=SimpleNamespace(glob=fake_glob)),
    )

    await search_router.glob(
        search_router.GlobRequest(
            pattern="**/*.md", tags=["env=prod"], include_tags=True
        ),
        _ctx=RequestContext(user=UserIdentifier("acct", "alice"), role=Role.USER),
    )

    assert seen["tags"] == ["env=prod"]
    assert seen["include_tags"] is True


@pytest.mark.parametrize("multimodal", [True, False])
async def test_find_image_query_uses_account_embedder_capability(
    client: httpx.AsyncClient, service, monkeypatch, multimodal: bool
):
    from openviking.models.embedder.base import DenseEmbedderBase
    from openviking_cli.utils.config.embedding_config import EmbeddingConfig

    seen = []

    class ImageEmbedder(DenseEmbedderBase):
        def __init__(self, dimension: int):
            super().__init__(model_name="test-image-embedder")
            self._dimension = dimension

        @property
        def supports_multimodal(self) -> bool:
            return multimodal

        def embed(self, content, is_query: bool = False) -> EmbedResult:
            if is_query:
                seen.append(content)
            return EmbedResult(dense_vector=[0.1] * self._dimension)

        def get_dimension(self) -> int:
            return self._dimension

    monkeypatch.setattr(EmbeddingConfig, "get_embedder", lambda self: ImageEmbedder(self.dimension))
    await service.embedding_provider.invalidate(UserIdentifier.the_default_user().account_id)
    image_url = "data:image/png;base64,iVBORw0KGgo="

    response = await client.post("/api/v1/search/find", json={"query": "", "image_url": image_url})

    if multimodal:
        assert response.status_code == 200, response.text
        assert seen and seen[-1][-1] == {"type": "image_url", "image_url": {"url": image_url}}
    else:
        assert response.status_code == 400
        assert response.json()["error"]["code"] == "INVALID_ARGUMENT"
        assert not seen
