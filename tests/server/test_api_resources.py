# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: Apache-2.0

"""Tests for resource management endpoints."""

from types import SimpleNamespace

import httpx

from openviking.telemetry import get_current_telemetry


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
    semantic = telemetry_summary.get("semantic_nodes")
    if semantic is not None:
        assert semantic["total"] is None or semantic["done"] == semantic["total"]
        assert semantic["pending"] in (None, 0)
        assert semantic["running"] in (None, 0)
    assert "resource" in telemetry_summary
    assert "memory" not in telemetry_summary


async def test_add_resource_with_telemetry_includes_resource_breakdown(
    client: httpx.AsyncClient, service, monkeypatch
):
    async def fake_add_resource(**kwargs):
        telemetry = get_current_telemetry()
        telemetry.set("resource.request.duration_ms", 152.3)
        telemetry.set("resource.process.duration_ms", 101.7)
        telemetry.set("resource.parse.duration_ms", 38.1)
        telemetry.set("resource.parse.warnings_count", 1)
        telemetry.set("resource.finalize.duration_ms", 22.4)
        telemetry.set("resource.summarize.duration_ms", 31.8)
        telemetry.set("resource.wait.duration_ms", 46.9)
        telemetry.set("resource.watch.duration_ms", 0.8)
        telemetry.set("resource.flags.wait", True)
        telemetry.set("resource.flags.build_index", True)
        telemetry.set("resource.flags.summarize", False)
        telemetry.set("resource.flags.watch_enabled", False)
        return {
            "status": "success",
            "root_uri": "viking://resources/demo",
        }

    monkeypatch.setattr(service.resources, "add_resource", fake_add_resource)

    resp = await client.post(
        "/api/v1/resources",
        json={
            "path": "/tmp/demo.md",
            "reason": "telemetry resource",
            "wait": True,
            "telemetry": True,
        },
    )
    assert resp.status_code == 200
    body = resp.json()
    resource = body["telemetry"]["summary"]["resource"]
    assert resource["request"]["duration_ms"] == 152.3
    assert resource["process"]["parse"] == {"duration_ms": 38.1, "warnings_count": 1}
    assert resource["wait"]["duration_ms"] == 46.9
    assert resource["flags"] == {
        "wait": True,
        "build_index": True,
        "summarize": False,
        "watch_enabled": False,
    }


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


async def test_add_resource_with_watch_interval_requires_to(
    client: httpx.AsyncClient, sample_markdown_file
):
    resp = await client.post(
        "/api/v1/resources",
        json={
            "path": str(sample_markdown_file),
            "reason": "test resource with watch interval",
            "watch_interval": 5.0,
        },
    )
    assert resp.status_code == 400
    body = resp.json()
    assert body["status"] == "error"
    assert "watch_interval > 0 requires 'to' to be specified" in body["error"]["message"]


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


async def test_temp_upload_success(client: httpx.AsyncClient, temp_dir, monkeypatch):
    monkeypatch.setattr(
        "openviking.server.routers.resources.get_openviking_config",
        lambda: SimpleNamespace(
            storage=SimpleNamespace(get_upload_temp_dir=lambda: temp_dir),
        ),
    )

    resp = await client.post(
        "/api/v1/resources/temp_upload",
        files={"file": ("sample.md", b"# upload\n", "text/markdown")},
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["status"] == "ok"
    assert "telemetry" not in body
    assert body["result"]["temp_path"].endswith(".md")


async def test_temp_upload_with_telemetry_returns_summary(
    client: httpx.AsyncClient, temp_dir, monkeypatch
):
    monkeypatch.setattr(
        "openviking.server.routers.resources.get_openviking_config",
        lambda: SimpleNamespace(
            storage=SimpleNamespace(get_upload_temp_dir=lambda: temp_dir),
        ),
    )

    resp = await client.post(
        "/api/v1/resources/temp_upload",
        files={"file": ("sample.md", b"# upload\n", "text/markdown")},
        data={"telemetry": "true"},
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["status"] == "ok"
    assert body["result"]["temp_path"].endswith(".md")
    assert body["telemetry"]["summary"]["operation"] == "resources.temp_upload"
