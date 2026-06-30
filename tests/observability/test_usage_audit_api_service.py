# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0

from __future__ import annotations

from typing import Any

import pytest

from openviking.observability.usage_audit.api_service import UsageAuditQueryService
from openviking.server.identity import RequestContext, Role
from openviking_cli.session.user_id import UserIdentifier


class FakeStore:
    def __init__(self) -> None:
        self.audit_call: dict[str, Any] | None = None
        self.token_series_call: dict[str, Any] | None = None

    async def get_token_series(self, **kwargs):
        self.token_series_call = kwargs
        return []

    async def query_audit_logs(self, **kwargs):
        self.audit_call = kwargs
        return {"total": 0, "success_rate": 0.0, "page": 1, "page_size": 10, "items": []}


class FakeInventory:
    async def get_counts(self, _ctx):
        return {}


@pytest.mark.asyncio
async def test_admin_metrics_and_audit_logs_are_account_wide():
    store = FakeStore()
    service = UsageAuditQueryService(store=store, inventory=FakeInventory(), timezone_name="UTC")
    ctx = RequestContext(
        user=UserIdentifier(account_id="acct-1", user_id="admin-1"),
        role=Role.ADMIN,
    )

    await service.token_series(
        ctx=ctx,
        start_date="2026-05-01",
        end_date="2026-05-01",
        bucket="day",
    )
    await service.audit_logs(
        ctx=ctx,
        request_id=None,
        statuses=[],
        api_types=[],
        page=1,
        page_size=10,
    )

    assert store.token_series_call is not None
    assert store.audit_call is not None
    assert store.token_series_call["user_id"] is None
    assert store.audit_call["user_id"] is None


@pytest.mark.asyncio
async def test_regular_user_metrics_and_audit_logs_use_current_identity():
    store = FakeStore()
    service = UsageAuditQueryService(store=store, inventory=FakeInventory(), timezone_name="UTC")
    ctx = RequestContext(
        user=UserIdentifier(account_id="acct-1", user_id="alice"),
        role=Role.USER,
    )

    await service.token_series(
        ctx=ctx,
        start_date="2026-05-01",
        end_date="2026-05-01",
        bucket="day",
    )
    await service.audit_logs(
        ctx=ctx,
        request_id=None,
        statuses=[],
        api_types=[],
        page=1,
        page_size=10,
    )

    assert store.token_series_call is not None
    assert store.audit_call is not None
    assert store.token_series_call["user_id"] == "alice"
    assert store.audit_call["user_id"] == "alice"
