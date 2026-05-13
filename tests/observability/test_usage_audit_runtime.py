# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0

from __future__ import annotations

from types import SimpleNamespace

import pytest

from openviking.observability.events import reset_event_bus_for_tests, try_publish_event
from openviking.observability.usage_audit import (
    init_usage_audit_from_server_config,
    shutdown_usage_audit,
)
from openviking.server.config import ObservabilityConfig, ServerConfig, UsageAuditConfig


@pytest.mark.asyncio
async def test_usage_audit_runtime_subscribes_to_shared_event_bus(tmp_path):
    reset_event_bus_for_tests()
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
                "agent_id": "agent-runtime",
                "method": "POST",
                "route": "/api/v1/search/find",
                "status": "200",
                "duration_seconds": 0.01,
            },
        )
        await runtime.worker.close(timeout_seconds=1.0)

        retrievals = await runtime.store.get_today_retrievals(
            account_id="acct-runtime",
            date=runtime.api_service.today(),
        )
        audit = await runtime.store.query_audit_logs(account_id="acct-runtime")
    finally:
        await shutdown_usage_audit(app=app)
        reset_event_bus_for_tests()

    assert retrievals["find"] == 1
    assert audit["total"] == 1
    assert audit["items"][0]["request_id"] == "req-runtime"
