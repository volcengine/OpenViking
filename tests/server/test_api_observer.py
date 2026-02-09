# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: Apache-2.0

"""Tests for observer endpoints (/api/v1/observer/*)."""

import httpx


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


async def test_observer_vikingdb(client: httpx.AsyncClient):
    """GET /api/v1/observer/vikingdb should return VikingDB status."""
    resp = await client.get("/api/v1/observer/vikingdb")
    assert resp.status_code == 200
    body = resp.json()
    assert body["status"] == "ok"
    result = body["result"]
    assert "name" in result
    assert "is_healthy" in result


async def test_observer_vlm(client: httpx.AsyncClient):
    """GET /api/v1/observer/vlm should return VLM status."""
    resp = await client.get("/api/v1/observer/vlm")
    assert resp.status_code == 200
    body = resp.json()
    assert body["status"] == "ok"
    result = body["result"]
    assert "name" in result
    assert "is_healthy" in result


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
