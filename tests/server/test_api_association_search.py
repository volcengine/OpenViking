# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0
"""Independent associations API; original semantic Find remains unchanged."""

import asyncio
from unittest.mock import AsyncMock

import pytest

from openviking.retrieve.memory_association import store as store_module
from openviking.retrieve.memory_association.store import FileAssociationStore
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
        {"query": "Alice", "user_id": "other"},
        {"query": "Alice", "session_id": "s"},
        {"query": "Alice", "score_threshold": 0.5},
    ],
)
async def test_invalid_payload_does_not_reach_service(client, service, monkeypatch, payload):
    call = AsyncMock()
    monkeypatch.setattr(service.search, "search_associations", call)
    response = await client.post("/api/v1/search/associations", json=payload)
    assert response.status_code == 400
    call.assert_not_awaited()


async def test_disabled_is_an_explicit_error(client):
    response = await client.post("/api/v1/search/associations", json={"query": "Alice"})
    assert response.status_code == 400
    assert "enabled is false" in response.text


async def test_round_trip_does_not_add_vectors_or_change_find(client, service, app, monkeypatch):
    ctx = RequestContext(user=UserIdentifier.the_default_user("test_user"), role=Role.USER)
    app.dependency_overrides[get_request_context] = lambda: ctx
    fs = service.viking_fs
    # Finish/cancel fixture preset-directory workers before comparing the
    # collection size; those initial vectors are unrelated to associations.
    await asyncio.to_thread(service._queue_manager.stop)
    store = FileAssociationStore(fs, MemoryAssociationConfig(enabled=True))
    monkeypatch.setattr(fs, "memory_association", store, raising=False)
    monkeypatch.setattr(
        store_module, "extract_cues_batch", lambda texts, **kwargs: [[("PROPER", "Alice")]]
    )
    uri = "viking://user/test_user/memories/events/music.md"
    primary = fs._get_vector_store()
    record = {
        "id": "association-api-parent",
        "uri": uri,
        "account_id": ctx.account_id,
        "owner_user_id": ctx.user.user_id,
        "context_type": "memory",
        "level": 2,
        "abstract": "Alice plays violin",
        "md5": "unchanged",
        "vector": [0.1] * primary.vector_dim,
    }
    await fs.write_file(uri, "Alice plays violin", ctx=ctx)
    await primary.upsert(record, ctx=ctx)
    find_payload = {"query": "Alice", "limit": 100, "level": 2}
    before = await client.post("/api/v1/search/find", json=find_payload)
    search_before = await client.post("/api/v1/search/search", json=find_payload)
    before_count = await primary.count(ctx=ctx)
    await store.refresh(uri, ctx)
    response = await client.post(
        "/api/v1/search/associations", json={"query": "What does Alice play?"}
    )
    assert response.status_code == 200, response.text
    assert response.json()["result"]["associations"] == [
        {"cue": "Alice", "cue_type": "PROPER", "score": 1.0, "memory_uri": uri}
    ]
    after = await client.post("/api/v1/search/find", json=find_payload)
    assert before.status_code == after.status_code == 200
    assert before.json()["result"] == after.json()["result"]
    search_after = await client.post("/api/v1/search/search", json=find_payload)
    assert search_before.status_code == search_after.status_code == 200
    assert search_before.json()["result"] == search_after.json()["result"]
    assert await primary.count(ctx=ctx) == before_count
    assert (await primary.get([record["id"]], ctx=ctx))[0]["md5"] == "unchanged"
