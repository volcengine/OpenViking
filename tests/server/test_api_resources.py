# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: Apache-2.0

"""Tests for resource management endpoints."""

import httpx


async def test_add_resource_success(client: httpx.AsyncClient, sample_markdown_file):
    resp = await client.post(
        "/api/v1/resources",
        json={
            "path": str(sample_markdown_file),
            "reason": "test resource",
            "wait": False,
        },
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["status"] == "ok"
    assert "time" not in body
    assert "usage" not in body
    assert "telemetry" not in body
    assert "root_uri" in body["result"]
    assert body["result"]["root_uri"].startswith("viking://")


async def test_add_resource_with_wait(client: httpx.AsyncClient, sample_markdown_file):
    resp = await client.post(
        "/api/v1/resources",
        json={
            "path": str(sample_markdown_file),
            "reason": "test resource",
            "wait": True,
        },
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["status"] == "ok"
    assert "root_uri" in body["result"]


async def test_add_resource_with_telemetry_wait(client: httpx.AsyncClient, sample_markdown_file):
    resp = await client.post(
        "/api/v1/resources",
        json={
            "path": str(sample_markdown_file),
            "reason": "telemetry resource",
            "wait": True,
            "telemetry": True,
        },
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["status"] == "ok"
    telemetry_summary = body["telemetry"]["summary"]
    assert telemetry_summary["operation"] == "resources.add_resource"
    assert "usage" not in body
    semantic = telemetry_summary["semantic_nodes"]
    assert semantic["total"] is None or semantic["done"] == semantic["total"]
    assert semantic["pending"] in (None, 0)
    assert semantic["running"] in (None, 0)
    assert "memory" not in telemetry_summary


async def test_add_resource_with_summary_only_telemetry(
    client: httpx.AsyncClient, sample_markdown_file
):
    resp = await client.post(
        "/api/v1/resources",
        json={
            "path": str(sample_markdown_file),
            "reason": "summary only telemetry resource",
            "wait": True,
            "telemetry": {"summary": True},
        },
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["status"] == "ok"
    assert "summary" in body["telemetry"]
    assert "usage" not in body
    assert "events" not in body["telemetry"]
    assert "truncated" not in body["telemetry"]
    assert "dropped" not in body["telemetry"]


async def test_add_resource_rejects_events_only_telemetry(
    client: httpx.AsyncClient, sample_markdown_file
):
    resp = await client.post(
        "/api/v1/resources",
        json={
            "path": str(sample_markdown_file),
            "reason": "events only telemetry",
            "wait": False,
            "telemetry": {"summary": False, "events": True},
        },
    )
    assert resp.status_code == 400
    body = resp.json()
    assert body["status"] == "error"
    assert body["error"]["code"] == "INVALID_ARGUMENT"
    assert "events" in body["error"]["message"]


async def test_add_resource_file_not_found(client: httpx.AsyncClient):
    resp = await client.post(
        "/api/v1/resources",
        json={"path": "/nonexistent/file.txt", "reason": "test"},
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["status"] == "ok"
    assert "errors" in body["result"] and len(body["result"]["errors"]) > 0


async def test_add_resource_with_to(client: httpx.AsyncClient, sample_markdown_file):
    resp = await client.post(
        "/api/v1/resources",
        json={
            "path": str(sample_markdown_file),
            "to": "viking://resources/custom/sample",
            "reason": "test resource",
        },
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["status"] == "ok"
    assert "custom" in body["result"]["root_uri"]


async def test_wait_processed_empty_queue(client: httpx.AsyncClient):
    resp = await client.post(
        "/api/v1/system/wait",
        json={"timeout": 30.0},
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["status"] == "ok"


async def test_wait_processed_after_add(client: httpx.AsyncClient, sample_markdown_file):
    await client.post(
        "/api/v1/resources",
        json={"path": str(sample_markdown_file), "reason": "test"},
    )
    resp = await client.post(
        "/api/v1/system/wait",
        json={"timeout": 60.0},
    )
    assert resp.status_code == 200
    assert resp.json()["status"] == "ok"


async def test_add_resource_with_watch_interval(client: httpx.AsyncClient, sample_markdown_file):
    resp = await client.post(
        "/api/v1/resources",
        json={
            "path": str(sample_markdown_file),
            "reason": "test resource with watch interval",
            "watch_interval": 5.0,
        },
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["status"] == "ok"
    assert "root_uri" in body["result"]


async def test_add_resource_with_default_watch_interval(
    client: httpx.AsyncClient, sample_markdown_file
):
    resp = await client.post(
        "/api/v1/resources",
        json={
            "path": str(sample_markdown_file),
            "reason": "test resource with default watch interval",
        },
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["status"] == "ok"
    assert "root_uri" in body["result"]
