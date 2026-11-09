"""An empty visible memory must not abort Search or misalign remaining bodies."""

import asyncio
import json
from unittest.mock import AsyncMock

import httpx
import pytest

from benchmark.aml import server


class IdentityRanker:
    async def rank(self, query, candidates):
        return candidates


def memory(name):
    return {"uri": f"viking://user/alice/memories/{name}", "abstract": "stale abstract"}


def make_app(monkeypatch, hits, bodies):
    client = AsyncMock()
    closed = False

    async def read(uri):
        value = bodies[uri]
        if isinstance(value, Exception):
            raise value
        if isinstance(value, str) and value.strip():
            # Empty reads finish first; healthy siblings must still finish before close.
            await asyncio.sleep(0.01)
            assert not closed
        return value

    async def close():
        nonlocal closed
        closed = True

    client.read.side_effect = read
    client.close.side_effect = close
    settings = server.AMLSettings(aml_api_key="test-key", retry_attempts=1)
    backend = server.OpenVikingBackend(settings, client_factory=lambda user: client)
    monkeypatch.setattr(backend, "find", AsyncMock(return_value=hits))
    return server.create_app(settings=settings, backend=backend, reranker=IdentityRanker()), client


async def search(app):
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://adapter.test"
    ) as client:
        return await client.post(
            "/search",
            headers={"X-API-Key": "test-key"},
            json={"user_id": "alice", "query": "question", "top_k": 20},
        )


@pytest.mark.parametrize("empty", ["", " \n\t"])
async def test_empty_entity_is_skipped_without_reusing_abstract_or_shifting_ids(
    monkeypatch, caplog, empty
):
    hits = [memory("events/a.md"), memory("entities/group/empty.md"), memory("events/b.md")]
    bodies = dict(zip([hit["uri"] for hit in hits], ["body A", empty, "body B"], strict=True))
    app, client = make_app(monkeypatch, hits, bodies)
    response = await search(app)
    assert response.status_code == 200
    assert response.json() == {
        "data": [
            {"id": hits[0]["uri"], "content": "body A"},
            {"id": hits[2]["uri"], "content": "body B"},
        ]
    }
    assert client.read.await_count == 3
    client.close.assert_awaited_once()
    records = [
        json.loads(row.message) for row in caplog.records if row.name == "benchmark.aml.audit"
    ]
    skipped = [row for row in records if row["event"] == "search_memory_skipped"]
    assert len(skipped) == 1
    assert skipped[0]["memory_uri"] == hits[1]["uri"]
    assert skipped[0]["reason"] == "empty_memory_body"
    assert not any(row["event"] == "backend_attempt_failed" for row in records)


async def test_all_empty_memories_return_an_empty_data_list(monkeypatch):
    hits = [memory("entities/group/a.md"), memory("entities/group/b.md")]
    app, client = make_app(monkeypatch, hits, {hit["uri"]: "" for hit in hits})
    response = await search(app)
    assert response.status_code == 200
    assert response.json() == {"data": []}
    assert client.read.await_count == 2


@pytest.mark.parametrize(
    "failure",
    [
        httpx.ReadError("connection reset"),
        server.OpenVikingError("read denied", code="PERMISSION_DENIED"),
        None,
        {},
    ],
)
async def test_real_read_errors_and_invalid_payloads_are_not_silently_skipped(
    monkeypatch, caplog, failure
):
    hit = memory("entities/group/a.md")
    app, client = make_app(monkeypatch, [hit], {hit["uri"]: failure})
    response = await search(app)
    assert response.status_code == 503
    client.read.assert_awaited_once_with(hit["uri"])
    records = [
        json.loads(row.message) for row in caplog.records if row.name == "benchmark.aml.audit"
    ]
    assert not any(row["event"] == "search_memory_skipped" for row in records)
