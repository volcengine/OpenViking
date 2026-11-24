# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0

"""Tests for observer endpoints (/api/v1/observer/*)."""

import asyncio
from unittest.mock import AsyncMock

import httpx

from openviking.service.debug_service import ComponentStatus
from openviking.storage.queuefs import get_queue_manager
from openviking.storage.queuefs.named_queue import QueueStatus


async def test_observer_queue_exposes_table_and_counters(client: httpx.AsyncClient, monkeypatch):
    semantic = QueueStatus(pending=2, processed=5, error_count=1)
    embedding = QueueStatus(in_progress=1, processed=8, requeue_count=3)

    async def statuses():
        return {"Semantic": semantic, "Embedding": embedding}

    monkeypatch.setattr(get_queue_manager(), "check_status", statuses)
    table = await client.get("/api/v1/observer/queue")
    structured = await client.get("/api/v1/observer/queue", params={"format": "json"})
    assert table.status_code == structured.status_code == 200
    table_status = table.json()["result"]["status"]
    assert "Semantic" in table_status and "Embedding" in table_status
    result = structured.json()["result"]
    assert result["name"] == "queue"
    assert result["is_healthy"] is False and result["has_errors"] is True
    queues = [queue for queue in result["status"]["queues"] if queue["queue"] != "Semantic-Nodes"]
    assert {"Semantic", "Embedding"} <= {queue["queue"] for queue in queues}
    summary = result["status"]["summary"]
    assert summary == {key: sum(queue[key] for queue in queues) for key in summary}
    assert summary == {
        "pending": 2,
        "in_progress": 1,
        "processed": 13,
        "requeued": 3,
        "errors": 1,
        "total": 16,
    }
    semantic.pending = 0
    embedding.in_progress = 0
    idle = await client.get("/api/v1/observer/queue", params={"format": "json"})
    assert idle.status_code == 200, idle.text
    idle_result = idle.json()["result"]
    assert idle_result["is_healthy"] is True and idle_result["has_errors"] is False
    assert idle_result["status"]["summary"]["errors"] == 1


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
