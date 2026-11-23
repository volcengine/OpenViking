# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0

"""Tests for observer endpoints (/api/v1/observer/*)."""

import asyncio
from unittest.mock import AsyncMock

import httpx

from openviking.service.debug_service import ComponentStatus


async def test_observer_queue(client: httpx.AsyncClient):
    """GET /api/v1/observer/queue should return queue status."""
    resp = await client.get("/api/v1/observer/queue")
    assert resp.status_code == 200
    body = resp.json()
    assert body["status"] == "ok"
    result = body["result"]
    assert "name" in result
    assert "is_healthy" in result
    assert "has_errors" in result
    assert "status" in result


async def test_observer_queue_structured(client: httpx.AsyncClient):
    """GET /api/v1/observer/queue?format=json should return structured status."""
    resp = await client.get("/api/v1/observer/queue", params={"format": "json"})
    assert resp.status_code == 200
    body = resp.json()
    assert body["status"] == "ok"
    result = body["result"]
    assert "status" in result
    assert isinstance(result["status"], dict)
    assert "queues" in result["status"]
    assert isinstance(result["status"]["queues"], list)
    assert "summary" in result["status"]
    assert isinstance(result["status"]["summary"], dict)


async def test_observer_vikingdb(client: httpx.AsyncClient, service):
    """VikingDB status should preserve table output and expose loaded index semantics."""
    table_resp = await client.get("/api/v1/observer/vikingdb")
    assert table_resp.status_code == 200
    assert isinstance(table_resp.json()["result"]["status"], str)

    manager = service.vikingdb_manager
    assert manager is not None
    backend = await manager.get_account_backend("default")
    backend._distance_metric = "l2"

    resp = await client.get("/api/v1/observer/vikingdb", params={"format": "json"})
    assert resp.status_code == 200
    body = resp.json()
    assert body["status"] == "ok"
    result = body["result"]
    assert result["name"] == "vikingdb"
    assert result["is_healthy"] is True
    assert result["status"]["backend"] == "local"
    assert result["status"]["distance_metric"] == "cosine"
    assert result["status"]["pure_dense_score_scale"] == "cosine_affine_0_1"


async def test_observer_models(client: httpx.AsyncClient):
    resp = await client.get("/api/v1/observer/models", params={"format": "json"})
    assert resp.status_code == 200
    body = resp.json()
    assert body["status"] == "ok"
    result = body["result"]
    assert result["name"] == "models"
    assert isinstance(result["status"], dict)
    assert {"account_id", "embedding_dimension", "vlm", "embedding", "rerank"} <= result[
        "status"
    ].keys()


async def test_observer_system(client: httpx.AsyncClient):
    """GET /api/v1/observer/system should return full system status."""
    resp = await client.get("/api/v1/observer/system")
    assert resp.status_code == 200
    body = resp.json()
    assert body["status"] == "ok"
    result = body["result"]
    assert "is_healthy" in result
    assert "errors" in result
    assert "components" in result
    assert isinstance(result["components"], dict)


async def test_observer_system_structured(client: httpx.AsyncClient, service, monkeypatch):
    monkeypatch.setattr(
        service.debug.observer,
        "get_filesystem_status_async",
        AsyncMock(return_value=ComponentStatus("filesystem", False, True, {})),
    )
    async with asyncio.timeout(10):
        runtime, account, models = await asyncio.gather(
            client.get("/api/v1/observer/system", params={"format": "json"}),
            client.get("/api/v1/observer/system", params={"format": "json", "scope": "account"}),
            client.get("/api/v1/observer/models", params={"format": "json"}),
        )
    assert runtime.status_code == account.status_code == models.status_code == 200
    runtime, account = runtime.json()["result"], account.json()["result"]
    components = runtime["components"]
    assert isinstance(components["queue"]["status"], dict)
    assert components["models"]["status"] == models.json()["result"]["status"]
    assert isinstance(components["models"]["status"]["vlm"], list)
    assert runtime["is_healthy"] is False
    assert "filesystem has errors" in runtime["errors"]
    assert account["is_healthy"] is True
    assert account["errors"] == []
    assert account["components"]["filesystem"] == components["filesystem"]
