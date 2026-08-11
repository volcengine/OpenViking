# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0

from types import SimpleNamespace

import httpx
import pytest
from fastapi import FastAPI
from pydantic import ValidationError

from openviking.server.identity import RequestContext, Role
from openviking.server.routers import search as search_router
from openviking.server.routers.search import FindRequest, SearchRequest
from openviking_cli.retrieve.types import FindResult
from openviking_cli.session.user_id import UserIdentifier


def test_find_request_parses_diversity_lambda_alias():
    request = FindRequest.model_validate(
        {"query": "architecture", "diversity": {"strategy": "mmr", "lambda": 0.6}}
    )
    assert request.diversity is not None
    assert request.diversity.relevance_weight == 0.6


def test_context_search_rejects_diversity():
    with pytest.raises(ValidationError, match="diversity requires mode='list'"):
        SearchRequest.model_validate(
            {"query": "architecture", "mode": "context", "diversity": {"strategy": "mmr"}}
        )


@pytest.mark.asyncio
async def test_find_route_passes_validated_diversity(monkeypatch):
    captured = {}

    async def fake_find(**kwargs):
        captured.update(kwargs)
        return FindResult(memories=[], resources=[], skills=[])

    app = FastAPI()
    app.include_router(search_router.router)
    app.dependency_overrides[search_router.get_request_context] = lambda: RequestContext(
        user=UserIdentifier("test", "user"), role=Role.USER
    )
    monkeypatch.setattr(
        search_router,
        "get_service",
        lambda: SimpleNamespace(search=SimpleNamespace(find=fake_find)),
    )
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        response = await client.post(
            "/api/v1/search/find",
            json={"query": "architecture", "diversity": {"strategy": "mmr", "lambda": 0.6}},
        )
    assert response.status_code == 200
    assert captured["diversity"].relevance_weight == 0.6


@pytest.mark.asyncio
async def test_context_route_returns_422_for_diversity():
    app = FastAPI()
    app.include_router(search_router.router)
    app.dependency_overrides[search_router.get_request_context] = lambda: RequestContext(
        user=UserIdentifier("test", "user"), role=Role.USER
    )
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        response = await client.post(
            "/api/v1/search/search",
            json={"query": "architecture", "mode": "context", "diversity": {"strategy": "mmr"}},
        )
    assert response.status_code == 422
    assert "diversity requires mode='list'" in response.text
