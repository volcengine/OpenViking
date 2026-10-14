# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0
"""Separate cue API validation and real local-index round trips."""

from unittest.mock import AsyncMock

import pytest

from openviking.retrieve.memory_association import index as index_module
from openviking.server.auth import get_request_context
from openviking.server.identity import RequestContext, Role
from openviking_cli.session.user_id import UserIdentifier
from openviking_cli.utils.config.retrieval_config import MemoryAssociationConfig


@pytest.mark.parametrize(
    "payload",
    [
        {"query": ""},
        {"query": "Alice", "limit": 0},
        {"query": "Alice", "limit": 1001},
        {"query": "Alice", "score_threshold": 1.1},
        {"query": "Alice", "user_id": "another-user"},
        {"query": "Alice", "session_id": "session"},
    ],
)
async def test_invalid_association_payload_does_not_reach_service(
    client, service, monkeypatch, payload
):
    call = AsyncMock()
    monkeypatch.setattr(service.search, "search_associations", call)
    response = await client.post("/api/v1/search/associations", json=payload)
    assert response.status_code == 400
    call.assert_not_awaited()


async def test_disabled_and_blank_association_queries_are_explicit_errors(client, monkeypatch):
    monkeypatch.setattr(index_module, "get_association_config", lambda: MemoryAssociationConfig())
    response = await client.post("/api/v1/search/associations", json={"query": "Alice"})
    assert response.status_code == 400
    assert "Enable retrieval.memory_association" in response.text
    response = await client.post("/api/v1/search/associations", json={"query": "  "})
    assert response.status_code == 400


async def test_association_api_round_trip_and_ordinary_find(client, service, app, monkeypatch):
    config = MemoryAssociationConfig(enabled=True)
    monkeypatch.setattr(index_module, "get_association_config", lambda: config)
    monkeypatch.setattr(
        index_module, "extract_cues_batch", lambda texts: [[("PROPER", "Alice")] for _ in texts]
    )
    ctx = RequestContext(user=UserIdentifier.the_default_user("test_user"), role=Role.USER)
    app.dependency_overrides[get_request_context] = lambda: ctx
    store = service.viking_fs._get_vector_store()
    dimension = store.vector_dim
    record = {
        "id": "cue-api-memory",
        "uri": "viking://user/test_user/memories/events/music.md",
        "account_id": ctx.account_id,
        "owner_user_id": ctx.user.user_id,
        "context_type": "memory",
        "level": 2,
        "abstract": "Alice plays violin",
        "md5": "v1",
        "vector": [0.1] * dimension,
    }
    await store.upsert(record, ctx=ctx)
    await store.memory_association_index.replace(
        record, "Alice plays violin", service.viking_fs._get_embedder(ctx), ctx, config
    )
    response = await client.post(
        "/api/v1/search/associations",
        json={
            "query": "What does Alice play?",
            "target_uri": "viking://~/memories",
            "limit": 2,
        },
    )
    assert response.status_code == 200, response.text
    result = response.json()["result"]
    assert result["query_cues"] == ["Alice"]
    assert result["total"] == 1
    assert result["associations"][0]["memory_uri"] == record["uri"]
    assert result["associations"][0]["cue"] == "Alice"
    response = await client.post(
        "/api/v1/search/find", json={"query": "Alice", "limit": 10, "level": 2}
    )
    assert response.status_code == 200, response.text
    assert len(response.json()["result"]["memories"]) == 1
    assert response.json()["result"]["memories"][0]["abstract"] == record["abstract"]
