# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0
"""Runtime wait status must not leak other tenants' queue error messages."""

from types import SimpleNamespace
from unittest.mock import AsyncMock

import httpx
import pytest
from fastapi import FastAPI

from openviking.server.auth import get_request_context
from openviking.server.identity import RequestContext, Role
from openviking.server.routers import system as system_router
from openviking.service import resource_service, task_tracker
from openviking_cli.session.user_id import UserIdentifier


def _queue_status() -> dict:
    return {
        "Semantic": SimpleNamespace(
            processed=2,
            requeue_count=0,
            error_count=1,
            errors=[SimpleNamespace(message="private queue error")],
        )
    }


@pytest.mark.parametrize(
    ("role", "expected_message"),
    [
        (Role.ROOT, "private queue error"),
        (Role.ADMIN, "Queue processing error"),
        (Role.USER, "Queue processing error"),
    ],
)
async def test_runtime_wait_redacts_queue_errors_for_non_root(monkeypatch, role, expected_message):
    monkeypatch.setattr(
        resource_service,
        "get_queue_manager",
        lambda: SimpleNamespace(wait_complete=AsyncMock(return_value=_queue_status())),
    )
    monkeypatch.setattr(
        task_tracker,
        "get_task_tracker",
        lambda: SimpleNamespace(has_work=AsyncMock(return_value=False)),
    )
    service = object.__new__(resource_service.ResourceService)
    fake_service = SimpleNamespace(resources=service)
    monkeypatch.setattr(system_router, "get_service", lambda: fake_service)

    app = FastAPI()
    app.include_router(system_router.router)
    app.dependency_overrides[get_request_context] = lambda: RequestContext(
        user=UserIdentifier("default", "alice"),
        role=role,
    )
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://testserver") as client:
        response = await client.post("/api/v1/system/wait", json={})

    assert response.status_code == 200, response.text
    assert response.json()["result"]["Semantic"]["errors"] == [{"message": expected_message}]
