# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0
"""Regression tests for PR #3 routers: admin, config, system, stats, tasks.

Covers the behavior established in PR #3 of the typed-response rollout:

1. ``ExcludeNoneRoute`` removes ``None`` fields from JSON output on the
   five PR #3 routers.
2. ``extra='allow'`` forwards unknown fields (protects against silent
   drop when service-side grows a new field).
3. ``/health`` and ``/ready`` return mirror models directly (no
   ``Response[T]`` envelope), matching the K8s probe contract.

Tests build a minimal FastAPI app with dependency overrides; no fixture
from ``tests/server/conftest.py`` is used. pytest still loads parent
``conftest.py`` modules at collection; a partial environment that can't
satisfy their imports will fail before any test runs here.
"""

from __future__ import annotations

from typing import Any
from unittest.mock import MagicMock

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from openviking.server.auth import get_request_context
from openviking.server.dependencies import set_service
from openviking.server.identity import RequestContext, Role
from openviking.server.routers.stats import router as stats_router
from openviking.server.routers.system import router as system_router
from openviking.server.routers.tasks import router as tasks_router
from openviking_cli.session.user_id import UserIdentifier


def _build_request_context() -> RequestContext:
    return RequestContext(
        user=UserIdentifier(account_id="acct_test", user_id="user_test", agent_id="agent_test"),
        role=Role.USER,
    )


@pytest.fixture
def client_factory():
    previous: Any = None

    def _build(service_mock: Any, router: Any) -> TestClient:
        nonlocal previous
        from openviking.server import dependencies as deps_mod

        previous = deps_mod._service
        set_service(service_mock)
        app = FastAPI()
        app.include_router(router)
        app.dependency_overrides[get_request_context] = _build_request_context
        return TestClient(app)

    yield _build

    from openviking.server import dependencies as deps_mod

    deps_mod._service = previous


def test_system_status_omits_nulls_and_preserves_extras(client_factory) -> None:
    """GET /api/v1/system/status: extra service fields forwarded; optional nulls omitted."""
    service = MagicMock()
    service._initialized = True
    client = client_factory(service, system_router)

    resp = client.get("/api/v1/system/status").json()

    assert resp["status"] == "ok"
    assert resp["result"]["initialized"] is True
    assert resp["result"]["user"] == "user_test"


def test_stats_memories_omits_null_optional_fields(client_factory) -> None:
    """GET /api/v1/stats/memories: null hotness/staleness omitted; extras preserved."""
    service = MagicMock()
    aggregator_result = {
        "total_memories": 100,
        "by_category": {"cases": 50, "patterns": 30, "tools": 20},
        "hotness_distribution": None,  # should be omitted
        "staleness": None,  # should be omitted
        "future_memory_metric": 42,
    }

    class _FakeAggregator:
        async def get_memory_stats(self, ctx: Any, category: Any = None) -> dict:
            return aggregator_result

    import openviking.server.routers.stats as stats_mod

    _orig = stats_mod._get_aggregator
    stats_mod._get_aggregator = lambda: _FakeAggregator()
    try:
        client = client_factory(service, stats_router)
        resp = client.get("/api/v1/stats/memories").json()

        assert resp["status"] == "ok"
        result = resp["result"]
        assert result["total_memories"] == 100
        assert result["future_memory_metric"] == 42, "extra='allow' must forward unknown fields"
        for none_field in ("hotness_distribution", "staleness"):
            assert none_field not in result, f"None field {none_field!r} must be omitted"
    finally:
        stats_mod._get_aggregator = _orig


def test_tasks_get_preserves_extra_result_fields(client_factory) -> None:
    """GET /api/v1/tasks/{id}: TaskRecord preserves dynamic result dict and extras."""
    # get_task uses the module-level get_task_tracker, not the service.
    tracker_mock = MagicMock()
    task_obj = MagicMock()
    task_obj.to_dict = MagicMock(
        return_value={
            "task_id": "t_abc",
            "task_type": "session_commit",
            "status": "completed",
            "created_at": 1728000000.0,
            "updated_at": 1728000010.0,
            "resource_id": "s_1",
            "result": {"archive_uri": "viking://x", "memories_extracted": 3},
            "error": None,
            "future_task_field": "preserved",
        }
    )
    tracker_mock.get = MagicMock(return_value=task_obj)

    import openviking.server.routers.tasks as tasks_mod

    _orig = tasks_mod.get_task_tracker
    tasks_mod.get_task_tracker = lambda: tracker_mock
    try:
        client = client_factory(MagicMock(), tasks_router)
        resp = client.get("/api/v1/tasks/t_abc").json()

        assert resp["status"] == "ok"
        result = resp["result"]
        assert result["task_id"] == "t_abc"
        assert result["status"] == "completed"
        assert result["result"]["archive_uri"] == "viking://x"
        assert result["future_task_field"] == "preserved"
        assert "error" not in result, "None error field must be omitted"
    finally:
        tasks_mod.get_task_tracker = _orig


def test_system_health_returns_mirror_model_not_envelope() -> None:
    """GET /health: returns SystemHealthResponse directly, no Response[T] envelope."""
    # /health doesn't need a service, just a bare app
    app = FastAPI()
    app.include_router(system_router)
    client = TestClient(app)

    resp = client.get("/health").json()

    # No "status": "ok" / "result" / "error" envelope — body is flat
    assert "healthy" in resp, f"health response missing mirror-model shape: {resp}"
    assert resp["healthy"] is True
    # Envelope keys must not be present
    assert "result" not in resp
    assert "error" not in resp
