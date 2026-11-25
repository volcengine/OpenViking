# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0

from datetime import datetime, timezone

import pytest

from openviking.observability.events import ObservabilityEvent
from openviking.observability.usage_audit.api_service import UsageAuditQueryService
from openviking.observability.usage_audit.inventory import ContextInventoryProvider
from openviking.observability.usage_audit.sqlite_store import SQLiteUsageAuditStore
from openviking.server.identity import RequestContext, Role
from openviking_cli.session.user_id import UserIdentifier


@pytest.mark.asyncio
async def test_usage_and_audit_queries_scope_results_to_the_viewer(tmp_path):
    store = SQLiteUsageAuditStore(tmp_path / "usage.sqlite3")
    await store.initialize()
    service = UsageAuditQueryService(
        store=store, inventory=ContextInventoryProvider(None), timezone_name="UTC"
    )
    day = "2026-05-01"
    timestamp = datetime(2026, 5, 1, 12, tzinfo=timezone.utc)

    try:
        for account, user, tokens in (
            ("acct-1", "alice", 10),
            ("acct-1", "bob", 20),
            ("acct-2", "alice", 100),
        ):
            await store.record_batch(
                [
                    ObservabilityEvent(
                        "vlm.call",
                        {"provider": "p", "model_name": "m", "prompt_tokens": tokens},
                        timestamp=timestamp,
                        account_id=account,
                        user_id=user,
                    ),
                    ObservabilityEvent(
                        "http.request",
                        {
                            "method": "POST",
                            "route": "/api/v1/search/find",
                            "status": "200",
                            "duration_seconds": 0.1,
                        },
                        timestamp=timestamp,
                        request_id=f"{account}-{user}",
                        account_id=account,
                        user_id=user,
                    ),
                ]
            )

        for user, role, expected_tokens, expected_requests in (
            ("admin-1", Role.ADMIN, 30, {"acct-1-alice", "acct-1-bob"}),
            ("alice", Role.USER, 10, {"acct-1-alice"}),
        ):
            ctx = RequestContext(user=UserIdentifier("acct-1", user), role=Role(role))
            usage = await service.token_series(ctx=ctx, start_date=day, end_date=day, bucket="day")
            audit = await service.audit_logs(
                ctx=ctx, request_id=None, statuses=[], api_types=[], page=1, page_size=10
            )
            assert [item["vlm_input"] for item in usage["items"]] == [expected_tokens]
            assert audit["total"] == len(expected_requests)
            assert {item["request_id"] for item in audit["items"]} == expected_requests
    finally:
        await store.close()
