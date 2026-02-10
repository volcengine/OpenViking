# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: Apache-2.0

"""Tests for server infrastructure: health, system status, middleware, error handling."""

import httpx


async def test_health_endpoint(client: httpx.AsyncClient):
    resp = await client.get("/health")
    assert resp.status_code == 200
    body = resp.json()
    assert body["status"] == "ok"


async def test_system_status(client: httpx.AsyncClient):
    resp = await client.get("/api/v1/system/status")
    assert resp.status_code == 200
    body = resp.json()
    assert body["status"] == "ok"
    assert body["result"]["initialized"] is True


async def test_process_time_header(client: httpx.AsyncClient):
    resp = await client.get("/health")
    assert "x-process-time" in resp.headers
    value = float(resp.headers["x-process-time"])
    assert value >= 0


async def test_openviking_error_handler(client: httpx.AsyncClient):
    """Requesting a non-existent resource should return structured error."""
    resp = await client.get(
        "/api/v1/fs/stat", params={"uri": "viking://nonexistent/path"}
    )
    assert resp.status_code in (404, 500)
    body = resp.json()
    assert body["status"] == "error"
    assert body["error"]["code"] is not None


async def test_404_for_unknown_route(client: httpx.AsyncClient):
    resp = await client.get("/this/route/does/not/exist")
    assert resp.status_code == 404
