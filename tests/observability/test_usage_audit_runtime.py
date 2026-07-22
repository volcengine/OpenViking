# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0

from __future__ import annotations

from types import SimpleNamespace

import httpx
import pytest
from fastapi import FastAPI

from openviking.observability import bind_background_observability_context
from openviking.observability.events import _GLOBAL_EVENT_BUS, try_publish_event
from openviking.observability.usage_audit import (
    init_usage_audit_from_server_config,
    shutdown_usage_audit,
)
from openviking.server.auth import get_request_context
from openviking.server.config import ObservabilityConfig, ServerConfig, UsageAuditConfig
from openviking.server.identity import RequestContext, Role
from openviking.server.routers.console import router as console_router
from openviking_cli.session.user_id import UserIdentifier


@pytest.mark.asyncio
async def test_usage_audit_runtime_subscribes_to_shared_event_bus(tmp_path):
    _GLOBAL_EVENT_BUS.clear()
    app = SimpleNamespace(state=SimpleNamespace())
    config = ServerConfig(
        observability=ObservabilityConfig(
            usage_audit=UsageAuditConfig(
                sqlite_path=str(tmp_path / "usage.sqlite3"),
                flush_interval_seconds=0.1,
                timezone="UTC",
            )
        )
    )
    runtime = await init_usage_audit_from_server_config(config, app=app, service=object())
    assert runtime is not None
    try:
        try_publish_event(
            "http.request",
            {
                "request_id": "req-runtime",
                "account_id": "acct-runtime",
                "user_id": "user-runtime",
                "method": "POST",
                "route": "/api/v1/search/find",
                "status": "200",
                "duration_seconds": 0.01,
            },
        )
        await runtime.worker.close(timeout_seconds=1.0)

        from zoneinfo import ZoneInfo

        retrievals = await runtime.store.get_today_retrievals(
            account_id="acct-runtime",
            user_date=runtime.api_service.today(),
            tz=ZoneInfo("UTC"),
        )
        audit = await runtime.store.query_audit_logs(account_id="acct-runtime")
    finally:
        await shutdown_usage_audit(app=app)
        _GLOBAL_EVENT_BUS.clear()

    assert retrievals["find"] == 1
    assert audit["total"] == 1
    assert audit["items"][0]["request_id"] == "req-runtime"


@pytest.mark.asyncio
async def test_background_model_tokens_reach_user_scoped_console_api(tmp_path):
    _GLOBAL_EVENT_BUS.clear()
    app = FastAPI()
    app.include_router(console_router)
    user_ctx = RequestContext(
        user=UserIdentifier(account_id="acct-runtime", user_id="user-runtime"),
        role=Role.USER,
    )
    app.dependency_overrides[get_request_context] = lambda: user_ctx
    config = ServerConfig(
        observability=ObservabilityConfig(
            usage_audit=UsageAuditConfig(
                sqlite_path=str(tmp_path / "usage.sqlite3"),
                flush_interval_seconds=0.1,
                timezone="UTC",
            )
        )
    )
    runtime = await init_usage_audit_from_server_config(config, app=app, service=object())
    assert runtime is not None
    today = runtime.api_service.today()

    try:
        with bind_background_observability_context(
            http_method="QUEUE",
            http_route="/queuefs/test",
            request_id="background-task",
            account_id="acct-runtime",
            user_id="user-runtime",
        ):
            try_publish_event(
                "vlm.call",
                {
                    "provider": "test-provider",
                    "model_name": "test-vlm",
                    "prompt_tokens": 3,
                    "completion_tokens": 2,
                },
            )
            try_publish_event(
                "embedding.call",
                {
                    "provider": "test-provider",
                    "model_name": "test-embedding",
                    "prompt_tokens": 5,
                    "completion_tokens": 0,
                },
            )
        await runtime.worker.close(timeout_seconds=1.0)

        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(
            transport=transport, base_url="http://testserver"
        ) as client:
            response = await client.get(
                "/api/v1/console/tokens",
                params={"start_date": today, "end_date": today, "timezone": "UTC"},
            )
    finally:
        await shutdown_usage_audit(app=app)
        _GLOBAL_EVENT_BUS.clear()

    assert response.status_code == 200
    assert response.json()["result"]["items"] == [
        {
            "date": today,
            "vlm_input": 3,
            "vlm_output": 2,
            "embedding_input": 5,
        }
    ]
