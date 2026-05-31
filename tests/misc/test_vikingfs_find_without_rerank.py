# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0
"""Regression test for VikingFS.find without rerank configuration."""

import contextvars
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import httpx
import pytest
from fastapi import FastAPI

from openviking.retrieve.hierarchical_retriever import RetrieverMode
from openviking.server.auth import get_request_context
from openviking.server.identity import RequestContext, Role
from openviking.server.routers import search as search_router
from openviking.service.search_service import SearchService
from openviking.storage.viking_fs import VikingFS
from openviking_cli.retrieve.types import ContextType, MatchedContext, QueryResult
from openviking_cli.session.user_id import UserIdentifier


def _ctx() -> RequestContext:
    return RequestContext(
        user=UserIdentifier("acc1", "user1", "agent1"), role=Role.USER
    )


def _make_viking_fs() -> VikingFS:
    fs = VikingFS.__new__(VikingFS)
    fs.agfs = MagicMock()
    fs.query_embedder = MagicMock(name="embedder")
    fs.rerank_config = None
    fs.vector_store = MagicMock(name="vector_store")
    fs._bound_ctx = contextvars.ContextVar("vikingfs_bound_ctx_test", default=None)
    fs._ensure_access = MagicMock()
    fs._get_vector_store = MagicMock(return_value=fs.vector_store)
    fs._get_embedder = MagicMock(return_value=fs.query_embedder)
    fs._infer_context_type = MagicMock(return_value=ContextType.RESOURCE)
    fs._ctx_or_default = MagicMock(return_value=_ctx())
    return fs


def _make_search_app(monkeypatch, captured: dict | None = None) -> FastAPI:
    app = FastAPI()
    app.include_router(search_router.router)
    app.dependency_overrides[get_request_context] = _ctx

    if captured is not None:

        async def fake_find(**kwargs):
            captured.update(kwargs)
            return {"items": []}

        monkeypatch.setattr(
            search_router,
            "get_service",
            lambda: SimpleNamespace(search=SimpleNamespace(find=fake_find)),
        )

    return app


@pytest.mark.asyncio
async def test_find_works_without_rerank_config(monkeypatch) -> None:
    fs = _make_viking_fs()
    request_ctx = _ctx()
    captured = {}

    class FakeRetriever:
        def __init__(self, storage, embedder, rerank_config):
            captured["storage"] = storage
            captured["embedder"] = embedder
            captured["rerank_config"] = rerank_config

        async def retrieve(self, typed_query, ctx, limit, score_threshold, scope_dsl):
            captured["typed_query"] = typed_query
            captured["ctx"] = ctx
            captured["limit"] = limit
            captured["score_threshold"] = score_threshold
            captured["scope_dsl"] = scope_dsl
            return QueryResult(
                query=typed_query,
                matched_contexts=[
                    MatchedContext(
                        uri="viking://resources/docs/guide.md",
                        context_type=ContextType.RESOURCE,
                        score=0.9,
                    )
                ],
                searched_directories=["viking://resources/docs"],
            )

    monkeypatch.setattr(
        "openviking.retrieve.hierarchical_retriever.HierarchicalRetriever",
        FakeRetriever,
    )

    result = await fs.find(
        "guide",
        target_uri="viking://resources/docs",
        limit=3,
        score_threshold=0.2,
        filter={"category": "doc"},
        ctx=request_ctx,
    )

    assert result.total == 1
    assert [ctx.uri for ctx in result.resources] == ["viking://resources/docs/guide.md"]
    assert captured["storage"] is fs.vector_store
    assert captured["embedder"] is fs.query_embedder
    assert captured["rerank_config"] is None
    assert captured["typed_query"].query == "guide"
    assert captured["typed_query"].context_type == ContextType.RESOURCE
    assert captured["typed_query"].target_directories == ["viking://resources/docs"]
    assert captured["ctx"] == fs._ctx_or_default.return_value
    assert captured["limit"] == 3
    assert captured["score_threshold"] == 0.2
    assert captured["scope_dsl"] == {"category": "doc"}
    fs._ensure_access.assert_called_once_with("viking://resources/docs", request_ctx)


@pytest.mark.parametrize(
    ("request_mode", "expected_mode"),
    [
        ("fast", RetrieverMode.QUICK),
        ("deep", RetrieverMode.THINKING),
    ],
)
@pytest.mark.asyncio
async def test_find_endpoint_passes_mode_to_search_service(
    monkeypatch, request_mode: str, expected_mode: str
) -> None:
    captured = {}
    app = _make_search_app(monkeypatch, captured)

    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app),
        base_url="http://testserver",
    ) as client:
        resp = await client.post(
            "/api/v1/search/find",
            json={"query": "sample", "mode": request_mode},
        )

    assert resp.status_code == 200
    assert captured["mode"] == expected_mode


@pytest.mark.parametrize("payload", [{}, {"mode": "auto"}])
@pytest.mark.asyncio
async def test_find_endpoint_auto_mode_omits_retriever_mode(
    monkeypatch, payload
) -> None:
    captured = {}
    app = _make_search_app(monkeypatch, captured)

    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app),
        base_url="http://testserver",
    ) as client:
        resp = await client.post(
            "/api/v1/search/find",
            json={"query": "sample", **payload},
        )

    assert resp.status_code == 200
    assert "mode" not in captured


@pytest.mark.asyncio
async def test_find_endpoint_rejects_invalid_mode(monkeypatch) -> None:
    app = _make_search_app(monkeypatch)

    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app),
        base_url="http://testserver",
    ) as client:
        resp = await client.post(
            "/api/v1/search/find",
            json={"query": "sample", "mode": "turbo"},
        )

    assert resp.status_code == 422
    assert resp.json()["detail"]


@pytest.mark.asyncio
async def test_search_service_passes_find_mode_to_vikingfs() -> None:
    viking_fs = MagicMock()
    viking_fs.find = AsyncMock(return_value={"items": []})
    service = SearchService(viking_fs)

    await service.find("guide", ctx=_ctx(), mode=RetrieverMode.QUICK)

    assert viking_fs.find.await_args.kwargs["mode"] == RetrieverMode.QUICK


@pytest.mark.asyncio
async def test_search_service_omits_find_mode_by_default() -> None:
    viking_fs = MagicMock()
    viking_fs.find = AsyncMock(return_value={"items": []})
    service = SearchService(viking_fs)

    await service.find("guide", ctx=_ctx())

    assert "mode" not in viking_fs.find.await_args.kwargs


@pytest.mark.parametrize("mode", [RetrieverMode.QUICK, RetrieverMode.THINKING])
@pytest.mark.asyncio
async def test_find_passes_mode_to_retriever(monkeypatch, mode: str) -> None:
    fs = _make_viking_fs()
    captured = {}

    class FakeRetriever:
        def __init__(self, storage, embedder, rerank_config):
            pass

        async def retrieve(self, typed_query, **kwargs):
            captured["mode"] = kwargs["mode"]
            return QueryResult(
                query=typed_query,
                matched_contexts=[],
                searched_directories=[],
            )

    monkeypatch.setattr(
        "openviking.retrieve.hierarchical_retriever.HierarchicalRetriever",
        FakeRetriever,
    )

    await fs.find("guide", ctx=_ctx(), mode=mode)

    assert captured["mode"] == mode
