# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0

from __future__ import annotations

from datetime import datetime, timezone

import pytest

from openviking.observability.events import ObservabilityEvent
from openviking.observability.usage_audit.sqlite_store import SQLiteUsageAuditStore


def _event(event_name: str, payload: dict, *, agent_id: str | None = None) -> ObservabilityEvent:
    return ObservabilityEvent(
        event_name=event_name,
        payload=payload,
        timestamp=datetime(2026, 5, 12, 1, 2, 3, tzinfo=timezone.utc),
        request_id=payload.get("request_id"),
        account_id="acct-1",
        user_id="user-1",
        agent_id=agent_id,
    )


@pytest.mark.asyncio
async def test_sqlite_usage_audit_store_aggregates_dashboard_data(tmp_path):
    store = SQLiteUsageAuditStore(tmp_path / "usage.sqlite3", timezone_name="UTC")
    await store.initialize()
    try:
        await store.record_batch(
            [
                _event(
                    "vlm.call",
                    {
                        "provider": "p",
                        "model_name": "m",
                        "prompt_tokens": 3,
                        "completion_tokens": 2,
                    },
                ),
                _event(
                    "embedding.call",
                    {
                        "provider": "p",
                        "model_name": "e",
                        "prompt_tokens": 5,
                        "completion_tokens": 0,
                    },
                ),
                _event(
                    "http.request",
                    {
                        "request_id": "req-find",
                        "method": "POST",
                        "route": "/api/v1/search/find",
                        "status": "200",
                        "duration_seconds": 0.1,
                    },
                ),
                _event(
                    "http.request",
                    {
                        "request_id": "req-message",
                        "method": "POST",
                        "route": "/api/v1/sessions/{session_id}/messages",
                        "status": "200",
                        "duration_seconds": 0.1,
                    },
                    agent_id="agent-1",
                ),
                _event(
                    "http.request",
                    {
                        "request_id": "req-console",
                        "method": "GET",
                        "route": "/api/v1/console/dashboard/summary",
                        "status": "200",
                        "duration_seconds": 0.01,
                    },
                    agent_id="agent-1",
                ),
                _event(
                    "http.request",
                    {
                        "request_id": "req-docs",
                        "method": "GET",
                        "route": "/docs",
                        "status": "200",
                        "duration_seconds": 0.01,
                    },
                    agent_id="agent-1",
                ),
                _event(
                    "http.request",
                    {
                        "request_id": "req-favicon",
                        "method": "GET",
                        "route": "/favicon.ico",
                        "status": "200",
                        "duration_seconds": 0.01,
                    },
                    agent_id="agent-1",
                ),
            ]
        )

        assert await store.get_today_tokens(account_id="acct-1", date="2026-05-12") == {
            "vlm_input": 3,
            "vlm_output": 2,
            "embedding_input": 5,
            "total": 10,
        }
        assert await store.get_today_retrievals(account_id="acct-1", date="2026-05-12") == {
            "find": 1,
            "search": 0,
            "total": 1,
        }
        commits = await store.get_context_commit_heatmap(
            account_id="acct-1",
            start_date="2026-05-12",
            end_date="2026-05-12",
            bucket="hour",
        )
        assert any(row["hour"] == 1 and row["session_add_message"] == 1 for row in commits)
        agents = await store.get_agent_overview(
            account_id="acct-1",
            date="2026-05-12",
        )
        assert agents["total"] == 1
        assert agents["items"][0]["agent_id"] == "agent-1"
        audit = await store.query_audit_logs(account_id="acct-1")
        assert audit["total"] == 2
        assert audit["success_rate"] == 1.0
        assert {item["api_type"] for item in audit["items"]} == {"search.find", "sessions"}
    finally:
        await store.close()


@pytest.mark.asyncio
async def test_sqlite_usage_audit_store_trims_audit_per_account(tmp_path):
    store = SQLiteUsageAuditStore(
        tmp_path / "usage.sqlite3",
        audit_retention_per_account=2,
        timezone_name="UTC",
    )
    await store.initialize()
    try:
        await store.record_batch(
            [
                _event(
                    "http.request",
                    {
                        "request_id": f"req-{idx}",
                        "method": "GET",
                        "route": "/api/v1/system/status",
                        "status": "200",
                        "duration_seconds": 0.01,
                    },
                )
                for idx in range(3)
            ]
        )
        audit = await store.query_audit_logs(account_id="acct-1", page_size=10)
        assert audit["total"] == 2
        assert [item["request_id"] for item in audit["items"]] == ["req-2", "req-1"]
    finally:
        await store.close()


@pytest.mark.asyncio
async def test_sqlite_usage_audit_store_success_filter_includes_3xx(tmp_path):
    store = SQLiteUsageAuditStore(tmp_path / "usage.sqlite3", timezone_name="UTC")
    await store.initialize()
    try:
        await store.record_batch(
            [
                _event(
                    "http.request",
                    {
                        "request_id": "req-200",
                        "method": "GET",
                        "route": "/api/v1/system/status",
                        "status": "200",
                        "duration_seconds": 0.01,
                    },
                ),
                _event(
                    "http.request",
                    {
                        "request_id": "req-302",
                        "method": "GET",
                        "route": "/api/v1/system/status",
                        "status": "302",
                        "duration_seconds": 0.01,
                    },
                ),
                _event(
                    "http.request",
                    {
                        "request_id": "req-500",
                        "method": "GET",
                        "route": "/api/v1/system/status",
                        "status": "500",
                        "duration_seconds": 0.01,
                    },
                ),
            ]
        )

        success = await store.query_audit_logs(
            account_id="acct-1",
            statuses=["success"],
            page_size=10,
        )
        explicit_2xx = await store.query_audit_logs(
            account_id="acct-1",
            statuses=["2xx"],
            page_size=10,
        )

        assert success["total"] == 2
        assert {item["request_id"] for item in success["items"]} == {"req-200", "req-302"}
        assert explicit_2xx["total"] == 1
        assert explicit_2xx["items"][0]["request_id"] == "req-200"
    finally:
        await store.close()
