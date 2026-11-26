# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0

"""Tests for search endpoints: find, search, grep, glob."""

import json
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import httpx
import pytest

from openviking.models.embedder.base import EmbedResult
from openviking.server.auth import get_request_context
from openviking.server.identity import RequestContext, Role
from openviking.storage.vectordb.collection.vikingdb_clients import VikingDBClient
from openviking.storage.viking_fs import VikingFS
from openviking.storage.viking_vector_index_backend import _SingleAccountBackend
from openviking_cli.exceptions import InvalidArgumentError
from openviking_cli.retrieve import ContextType, FindResult, MatchedContext
from openviking_cli.session.user_id import UserIdentifier
from openviking_cli.utils.config.vectordb_config import VectorDBBackendConfig, VikingDBConfig


@pytest.mark.parametrize(("query", "with_session"), [("", False), ("  \n\t", True)])
async def test_ordinary_filter_only_search_matches_find_without_models_or_session(
    client_with_resource, service, monkeypatch, query, with_session
):
    client, root = client_with_resource
    payload = {
        "query": query,
        "target_uri": root,
        "filter": {"op": "must", "field": "context_type", "conds": ["resource"]},
        "level": 2,
        "score_threshold": 100,
    }
    find_response = await client.post("/api/v1/search/find", json=payload)
    assert find_response.status_code == 200, find_response.text
    assert find_response.json()["result"]["total"] > 0
    embedding = AsyncMock(side_effect=AssertionError("Pure filters must not embed"))
    monkeypatch.setattr("openviking.models.embedder.base.embed_compat", embedding)
    monkeypatch.setattr(service.search, "is_intent_enabled", lambda: True)
    session_factory = Mock(side_effect=AssertionError("Pure filters must not load a session"))
    monkeypatch.setattr(service.sessions, "session", session_factory)
    if with_session:
        payload["session_id"] = "unused-session"
    search_response = await client.post("/api/v1/search/search", json=payload)
    assert search_response.status_code == 200, search_response.text
    assert search_response.json()["result"] == find_response.json()["result"]
    for hit in search_response.json()["result"]["resources"]:
        assert hit["score"] == 0
        assert not any(k in hit for k in ("item_uuid", "item_attrs", "item_extra_info"))
    embedding.assert_not_awaited()
    session_factory.assert_not_called()


async def test_filter_only_search_service_does_not_read_session_context(service, monkeypatch):
    monkeypatch.setattr(service.search, "is_intent_enabled", lambda: True)
    session = SimpleNamespace(get_context_for_search=AsyncMock())
    search = AsyncMock(return_value=FindResult(memories=[], resources=[], skills=[]))
    monkeypatch.setattr(service.viking_fs, "search", search)
    ctx = RequestContext(user=UserIdentifier.the_default_user(), role=Role.ROOT)
    await service.search.search(
        "",
        ctx=ctx,
        session=session,
        filter={"op": "must", "field": "search_tags", "conds": ["case=fresh"]},
    )
    session.get_context_for_search.assert_not_awaited()
    assert search.await_args.kwargs["session_info"] is None


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


async def test_search_accepts_filter_only_context_type_query(client_with_resource):
    client, root = client_with_resource
    payload = {"target_uri": root, "context_type": ["resource"]}
    found = await client.post("/api/v1/search/find", json=payload)
    searched = await client.post("/api/v1/search/search", json=payload)
    assert found.status_code == searched.status_code == 200
    assert searched.json()["result"] == found.json()["result"]
    assert searched.json()["result"]["total"] > 0
    assert all(hit["score"] == 0 for hit in searched.json()["result"]["resources"])


async def test_find_combines_existing_filter_with_time_range(client, service):
    ctx = RequestContext(user=service.user, role=Role(Role.ROOT))
    resources = "viking://resources/filter-contract"
    memories = f"viking://user/{ctx.user.user_id}/memories/preferences"
    terms = ["kind=email", "team=search", "env=prod"]
    vector = service.viking_fs.query_embedder.embed("sample").dense_vector
    rows = (
        (resources, "accepted", "resource", "2026-03-11T23:59:59Z", terms),
        (resources, "too-old", "resource", "2026-03-09T23:59:59Z", terms),
        (resources, "too-new", "resource", "2026-03-12T00:00:00Z", terms),
        (resources, "without-env", "resource", "2026-03-10T10:00:00Z", terms[:2]),
        (resources, "wrong-kind", "resource", "2026-03-10T10:00:00Z", terms[1:]),
        (memories, "accepted-memory", "memory", "2026-03-10T00:00:00Z", terms),
    )
    for root, name, kind, created, tags in rows:
        await service.vikingdb_manager.upsert(
            {
                "uri": f"{root}/{name}.md",
                "parent_uri": root,
                "context_type": kind,
                "is_leaf": True,
                "abstract": "sample",
                "level": 2,
                "vector": vector,
                "created_at": created,
                "updated_at": "2026-03-15T10:00:00Z",
                "search_tags": tags,
            },
            ctx=ctx,
        )

    payload = {
        "query": "sample",
        "level": 2,
        "since": "2026-03-10",
        "until": "2026-03-11",
        "time_field": "created_at",
        "filter": {"op": "must", "field": "search_tags", "conds": ["kind=email"]},
        "tags": ["Env=Prod", " env=prod ", "Team=Search"],
    }
    response = await client.post("/api/v1/search/find", json={**payload, "target_uri": resources})
    assert response.status_code == 200, response.text
    found = response.json()["result"]
    assert found["total"] == 1
    assert [item["uri"] for item in found["resources"]] == [f"{resources}/accepted.md"]

    for scope, has_resource in (("memory", False), (["memory", "resource"], True)):
        response = await client.post(
            "/api/v1/search/search",
            json={**payload, "context_type": scope, "target_uri": [resources, memories]},
        )
        assert response.status_code == 200, response.text
        found = response.json()["result"]
        assert found["total"] == 1 + has_resource
        assert [item["uri"] for item in found["memories"]] == [f"{memories}/accepted-memory.md"]
        assert [item["uri"] for item in found["resources"]] == (
            [f"{resources}/accepted.md"] if has_resource else []
        )

    response = await client.post(
        "/api/v1/search/search",
        json={
            "query": "sample",
            "level": 2,
            "tags": ["team=search", "env=prod"],
            "target_uri": resources,
        },
    )
    assert response.status_code == 200, response.text
    assert {item["uri"] for item in response.json()["result"]["resources"]} == {
        f"{resources}/{name}.md" for name in ("accepted", "too-old", "too-new", "wrong-kind")
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
        {"node_limit": 0},
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
    field = "time_field" if "time_field" in fields else next(iter(fields))
    [error] = body["error"]["details"]["validation_errors"]
    assert error["loc"] == ["body", field]


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
    client, root = client_with_resource
    for endpoint, telemetry in (("find", True), ("search", {"summary": True})):
        response = await client.post(
            f"/api/v1/search/{endpoint}",
            json={
                "query": "sample document",
                "target_uri": root,
                "level": 2,
                "telemetry": telemetry,
            },
        )
        assert response.status_code == 200, response.text
        body = response.json()
        assert body["result"]["total"] == 1
        assert "usage" not in body
        data = body["telemetry"]
        assert data["id"].startswith("tm_")
        assert {"events", "truncated", "dropped"}.isdisjoint(data)
        summary = data["summary"]
        assert summary["operation"] == f"search.{endpoint}"
        assert "duration_ms" in summary
        assert {"tokens", "queue", "semantic_nodes", "memory"}.isdisjoint(summary)
        assert {"request", "result_convert"}.isdisjoint(summary["search"])
        assert "vector_retrieval" in summary["search"]
        assert summary["vector"]["returned"] == body["result"]["total"]


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


async def test_grep_filters_and_returns_resource_tags(client_with_resource):
    client, root = client_with_resource
    uri = f"{root}/sample.md"
    tags = ["team=search", "env=prod"]
    updated = await client.post("/api/v1/fs/attrs/set_tags", json={"uri": uri, "tags": tags})
    assert updated.status_code == 200, updated.text
    payload = {"uri": root, "pattern": "Introduction"}

    unfiltered = await client.post("/api/v1/search/grep", json=payload)
    assert unfiltered.status_code == 200, unfiltered.text
    [match] = unfiltered.json()["result"]["matches"]
    assert match["uri"] == uri
    assert "tags" not in match

    projected = await client.post("/api/v1/search/grep", json={**payload, "include_tags": True})
    assert projected.status_code == 200, projected.text
    assert projected.json()["result"]["matches"] == [{**match, "tags": tags}]

    selected = await client.post("/api/v1/search/grep", json={**payload, "tags": tags})
    assert selected.status_code == 200, selected.text
    assert selected.json()["result"]["matches"] == [{**match, "tags": tags}]

    excluded = await client.post(
        "/api/v1/search/grep", json={**payload, "tags": ["team=search", "env=dev"]}
    )
    assert excluded.status_code == 200, excluded.text
    assert excluded.json()["result"]["matches"] == []


async def test_grep_case_insensitive(client_with_resource):
    client, root = client_with_resource
    payload = {"uri": root, "pattern": "introduction"}
    sensitive = await client.post("/api/v1/search/grep", json=payload)
    assert sensitive.status_code == 200, sensitive.text
    assert sensitive.json()["result"]["matches"] == []

    insensitive = await client.post(
        "/api/v1/search/grep", json={**payload, "case_insensitive": True}
    )
    assert insensitive.status_code == 200, insensitive.text
    [match] = insensitive.json()["result"]["matches"]
    assert match["uri"] == f"{root}/sample.md"
    assert match["content"] == "## Introduction"


async def test_grep_missing_uri_returns_not_found(client: httpx.AsyncClient):
    resp = await client.post(
        "/api/v1/search/grep",
        json={
            "uri": "viking://resources/nonexistent_grep_test_xyz",
            "pattern": "test",
        },
    )

    assert resp.status_code == 404
    error = resp.json()["error"]
    assert error["code"] == "NOT_FOUND"
    assert "viking://resources/nonexistent_grep_test_xyz" in error["message"]


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


async def test_grep_exclude_uri_does_not_exclude_same_named_sibling_dirs(
    client: httpx.AsyncClient, upload_temp_dir
):
    files = {}
    for group in ("group_a", "group_b"):
        source = upload_temp_dir / f"{group}.md"
        source.write_text(f"OpenViking match in {group} cache.\n")
        target = f"viking://resources/{group}/cache/{source.name}"
        added = await client.post(
            "/api/v1/resources",
            json={"temp_file_id": source.name, "to": target, "wait": True},
        )
        assert added.status_code == 200, added.text
        files[group] = f"{added.json()['result']['root_uri']}/{source.name}"
    payload = {"uri": "viking://resources", "pattern": "OpenViking"}
    baseline = await client.post("/api/v1/search/grep", json=payload)
    assert baseline.status_code == 200, baseline.text
    assert {m["uri"] for m in baseline.json()["result"]["matches"]} == set(files.values())

    response = await client.post(
        "/api/v1/search/grep", json={**payload, "exclude_uri": "viking://resources/group_a/cache"}
    )

    assert response.status_code == 200, response.text
    assert {m["uri"] for m in response.json()["result"]["matches"]} == {files["group_b"]}


async def test_glob(client_with_resource):
    client, root = client_with_resource
    resp = await client.post(
        "/api/v1/search/glob",
        json={"pattern": "**/*.md"},
    )
    assert resp.status_code == 200
    assert resp.json()["status"] == "ok"
    matches = resp.json()["result"]["matches"]
    assert f"{root}/sample.md" in matches
    assert all(isinstance(match, str) for match in matches)


async def test_glob_explicit_empty_extra_fields_returns_entry_objects(client_with_resource):
    client, root = client_with_resource
    resp = await client.post(
        "/api/v1/search/glob",
        json={"pattern": "**/*.md", "extra_fields": []},
    )

    assert resp.status_code == 200
    matches = resp.json()["result"]["matches"]
    assert any(m["uri"] == f"{root}/sample.md" and m["isDir"] is False for m in matches)
    assert all(isinstance(match, dict) for match in matches)
    assert all("uri" in match and "isDir" in match for match in matches)


async def test_glob_filters_and_projects_resource_tags(client_with_resource):
    client, root = client_with_resource
    uri = f"{root}/sample.md"
    tags = ["env=prod"]
    updated = await client.post("/api/v1/fs/attrs/set_tags", json={"uri": uri, "tags": tags})
    assert updated.status_code == 200, updated.text
    payload = {"uri": root, "pattern": "*.md", "include_tags": True}

    response = await client.post("/api/v1/search/glob", json={**payload, "tags": tags})
    assert response.status_code == 200, response.text
    [match] = response.json()["result"]["matches"]
    assert match["uri"] == uri
    assert match["isDir"] is False
    assert match["tags"] == tags

    excluded = await client.post("/api/v1/search/glob", json={**payload, "tags": ["env=dev"]})
    assert excluded.status_code == 200, excluded.text
    assert excluded.json()["result"]["matches"] == []


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
