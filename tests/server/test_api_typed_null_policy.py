# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0
"""Regression tests for content/search null-handling policy.

These complement ``test_api_sessions_null_policy.py`` and cover the
``ExcludeNoneRoute`` + ``extra='allow'`` behavior introduced in PR #1 for
the content and search routers.

Bot proxy endpoints are covered by upstream vikingbot tests; they are not
retested here because the server-side behavior is pure passthrough of
``response.json()`` into mirror models.

Like ``test_api_sessions_null_policy.py``, this module only overrides
FastAPI dependencies — it does not instantiate any fixture from the
heavy ``tests/server/conftest.py``. pytest still loads parent
``conftest.py`` modules at collection; a partial environment that can't
satisfy their imports will fail before any test runs here.
"""

from __future__ import annotations

from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from openviking.server.auth import get_request_context
from openviking.server.dependencies import set_service
from openviking.server.identity import RequestContext, Role
from openviking.server.routers.content import router as content_router
from openviking.server.routers.search import router as search_router
from openviking.telemetry.execution import TelemetryExecutionResult
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


def test_content_write_omits_null_and_preserves_extras(
    client_factory, monkeypatch: pytest.MonkeyPatch
) -> None:
    """POST /content/write omits None fields; extra='allow' forwards future fields."""
    service = MagicMock()
    service.fs = MagicMock()

    async def _fake_write(**kwargs: Any) -> dict:
        return {
            "uri": kwargs["uri"],
            "root_uri": None,
            "context_type": "resource",
            "mode": "replace",
            "written_bytes": 42,
            "semantic_updated": True,
            "vector_updated": None,
            "queue_status": None,
            "future_write_metric": 7,
        }

    service.fs.write = AsyncMock(side_effect=_fake_write)

    async def _fake_run_operation(
        operation: str, telemetry: Any, fn: Any
    ) -> TelemetryExecutionResult[Any]:
        from openviking.telemetry.request import TelemetrySelection

        result = await fn()
        return TelemetryExecutionResult(
            result=result,
            telemetry=None,
            selection=TelemetrySelection(include_summary=False),
        )

    monkeypatch.setattr("openviking.server.routers.content.run_operation", _fake_run_operation)

    client = client_factory(service, content_router)
    resp = client.post(
        "/api/v1/content/write",
        json={"uri": "viking://a", "content": "hi"},
    ).json()

    assert resp["status"] == "ok"
    result = resp["result"]
    assert result["uri"] == "viking://a"
    assert result["written_bytes"] == 42
    assert result["semantic_updated"] is True
    assert result["future_write_metric"] == 7, "extra='allow' must forward unknown fields"
    for none_field in ("root_uri", "vector_updated", "queue_status"):
        assert none_field not in result, f"None field {none_field!r} must be omitted"


def test_search_find_omits_null_query_plan_fields(
    client_factory, monkeypatch: pytest.MonkeyPatch
) -> None:
    """POST /search/find omits a None ``query_plan`` and preserves unknown fields."""
    service = MagicMock()
    service.search = MagicMock()

    class _FindResult:
        def to_dict(self, include_provenance: bool = False) -> dict:
            return {
                "memories": [
                    {
                        "context_type": "memory",
                        "uri": "viking://m/1",
                        "score": 0.8,
                        "abstract": "a",
                        "category": None,
                        "relations": None,
                    }
                ],
                "resources": [],
                "skills": [],
                "total": 1,
                "query_plan": None,
                "future_search_signal": "forward_me",
            }

    service.search.find = AsyncMock(return_value=_FindResult())

    async def _fake_run_operation(
        operation: str, telemetry: Any, fn: Any
    ) -> TelemetryExecutionResult[Any]:
        from openviking.telemetry.request import TelemetrySelection

        result = await fn()
        return TelemetryExecutionResult(
            result=result,
            telemetry=None,
            selection=TelemetrySelection(include_summary=False),
        )

    monkeypatch.setattr("openviking.server.routers.search.run_operation", _fake_run_operation)

    client = client_factory(service, search_router)
    resp = client.post(
        "/api/v1/search/find",
        json={"query": "anything"},
    ).json()

    assert resp["status"] == "ok"
    result = resp["result"]
    assert result["total"] == 1
    assert result["future_search_signal"] == "forward_me", (
        "extra='allow' must forward unknown fields"
    )
    assert "query_plan" not in result, "None query_plan must be omitted"
    assert "provenance" not in result, "None provenance must be omitted"
    hit = result["memories"][0]
    assert "category" not in hit, "None hit field must be omitted"
    assert "relations" not in hit, "None hit field must be omitted"
