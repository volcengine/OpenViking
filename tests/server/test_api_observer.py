# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0

"""Tests for observer endpoints (/api/v1/observer/*)."""

import asyncio
from types import SimpleNamespace

import httpx
import pytest
from fastapi import FastAPI

from openviking.server.auth import get_request_context
from openviking.server.identity import RequestContext, Role
from openviking.server.routers import observer
from openviking.service.session_service import SessionService
from openviking.storage.viking_fs import VikingFS
from openviking_cli.session.user_id import UserIdentifier
from tests.utils.mock_agfs import MockLocalAGFS


@pytest.mark.parametrize(("session_count", "archive_count"), [(1, 1001), (1001, 1)])
async def test_observer_session_archives_counts_persisted_markers(
    tmp_path, monkeypatch, session_count, archive_count
):
    """Count all visible archives without task rows, across new service instances."""
    agfs = MockLocalAGFS(root_path=tmp_path)
    session_root = "/local/acme/user/alice/sessions"
    for session_index in range(session_count):
        for index in range(archive_count):
            agfs.write(
                f"{session_root}/s{session_index}/history/archive_{index:03d}/.failed.json", "{}"
            )
    history = f"{session_root}/s0/history"
    agfs.write(f"{history}/archive_1001/.done", "{}")
    agfs.write(f"{history}/archive_1001/.failed.json", "{}")
    agfs.write(f"{history}/archive_1002/messages.jsonl", "")
    agfs.write(f"{history}/unrelated/.failed.json", "{}")
    agfs.write(f"{session_root}/without-history/messages.jsonl", "")
    agfs.write("/local/acme/user/bob/sessions/s1/history/archive_000/.done", "{}")
    agfs.write("/local/other/user/alice/sessions/s1/history/archive_000/.done", "{}")

    app = FastAPI()
    app.include_router(observer.router)
    context = RequestContext(user=UserIdentifier("acme", "alice"), role=Role.USER)
    app.dependency_overrides[get_request_context] = lambda: context
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        for _ in range(2):
            service = SessionService(viking_fs=VikingFS(agfs=agfs))
            monkeypatch.setattr(
                observer, "get_service", lambda service=service: SimpleNamespace(sessions=service)
            )
            response = await client.get("/api/v1/observer/session-archives")
            assert response.status_code == 200
            assert response.json()["result"] == {
                "total": 1003,
                "completed": 1,
                "failed": 1001,
                "pending": 1,
            }

        context = RequestContext(user=UserIdentifier("acme", "bob"), role=Role.USER)
        assert (await client.get("/api/v1/observer/session-archives")).json()["result"] == {
            "total": 1,
            "completed": 1,
            "failed": 0,
            "pending": 0,
        }
        context = RequestContext(user=UserIdentifier("other", "alice"), role=Role.ROOT)
        assert (await client.get("/api/v1/observer/session-archives")).json()["result"] == {
            "total": 1,
            "completed": 1,
            "failed": 0,
            "pending": 0,
        }
        context = RequestContext(user=UserIdentifier("empty", "nobody"), role=Role.USER)
        assert (await client.get("/api/v1/observer/session-archives")).json()["result"] == {
            "total": 0,
            "completed": 0,
            "failed": 0,
            "pending": 0,
        }


async def test_observer_session_archives_does_not_hide_storage_errors(tmp_path, monkeypatch):
    agfs = MockLocalAGFS(root_path=tmp_path)
    marker_dir = "/local/acme/user/alice/sessions/s1/history/archive_000"
    agfs.write(f"{marker_dir}/.failed.json", "{}")
    original_ls = agfs.ls

    def failing_ls(path, **kwargs):
        if path == marker_dir:
            raise PermissionError("archive unavailable")
        return original_ls(path, **kwargs)

    monkeypatch.setattr(agfs, "ls", failing_ls)
    service = SessionService(viking_fs=VikingFS(agfs=agfs))
    monkeypatch.setattr(observer, "get_service", lambda: SimpleNamespace(sessions=service))
    app = FastAPI()
    app.include_router(observer.router)
    app.dependency_overrides[get_request_context] = lambda: RequestContext(
        user=UserIdentifier("acme", "alice"),
        role=Role.USER,
    )
    transport = httpx.ASGITransport(app=app, raise_app_exceptions=False)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.get("/api/v1/observer/session-archives")
    assert response.status_code == 500


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


async def test_observer_system_structured(client: httpx.AsyncClient):
    async with asyncio.timeout(10):
        response, models = await asyncio.gather(
            client.get("/api/v1/observer/system", params={"format": "json"}),
            client.get("/api/v1/observer/models", params={"format": "json"}),
        )
    assert response.status_code == models.status_code == 200
    components = response.json()["result"]["components"]
    assert isinstance(components["queue"]["status"], dict)
    assert components["models"]["status"] == models.json()["result"]["status"]
    assert isinstance(components["models"]["status"]["vlm"], list)
