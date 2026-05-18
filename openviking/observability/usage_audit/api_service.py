# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0
"""Read service for product Usage/Audit APIs."""

from __future__ import annotations

from datetime import date, datetime
from typing import Any

from openviking.server.identity import RequestContext
from openviking_cli.exceptions import InvalidArgumentError

from .inventory import ContextInventoryProvider
from .store import UsageAuditStore
from .time import resolve_usage_timezone


class UsageAuditQueryService:
    """High-level query facade used by HTTP readers."""

    def __init__(
        self,
        *,
        store: UsageAuditStore,
        inventory: ContextInventoryProvider,
        timezone_name: str = "local",
    ) -> None:
        self._store = store
        self._inventory = inventory
        self._tz = resolve_usage_timezone(timezone_name)

    def today(self) -> str:
        """Return today's date in the configured usage/audit timezone."""
        return datetime.now(self._tz).date().isoformat()

    async def dashboard_summary(self, ctx: RequestContext) -> dict[str, Any]:
        """Return all Dashboard top-card data."""
        today = self.today()
        context_counts = await self._inventory.get_counts(ctx)
        today_tokens = await self._store.get_today_tokens(account_id=ctx.account_id, date=today)
        today_retrievals = await self._store.get_today_retrievals(
            account_id=ctx.account_id,
            date=today,
        )
        agent_overview = await self._store.get_agent_overview(
            account_id=ctx.account_id,
            date=today,
            limit=5,
        )
        return {
            "context_counts": context_counts,
            "today_tokens": today_tokens,
            "today_retrievals": today_retrievals,
            "agent_overview": agent_overview,
        }

    async def token_series(
        self,
        *,
        ctx: RequestContext,
        start_date: str,
        end_date: str,
        bucket: str,
    ) -> dict[str, Any]:
        """Return token usage series for a date range."""
        self._validate_date_range(start_date, end_date)
        items = await self._store.get_token_series(
            account_id=ctx.account_id,
            start_date=start_date,
            end_date=end_date,
            bucket=bucket,
        )
        return {"start_date": start_date, "end_date": end_date, "bucket": bucket, "items": items}

    async def context_commits(
        self,
        *,
        ctx: RequestContext,
        start_date: str,
        end_date: str,
        bucket: str,
    ) -> dict[str, Any]:
        """Return context write heatmap rows for a date range."""
        self._validate_date_range(start_date, end_date)
        items = await self._store.get_context_commit_heatmap(
            account_id=ctx.account_id,
            start_date=start_date,
            end_date=end_date,
            bucket=bucket,
        )
        return {"start_date": start_date, "end_date": end_date, "bucket": bucket, "items": items}

    async def audit_logs(
        self,
        *,
        ctx: RequestContext,
        request_id: str | None,
        statuses: list[str],
        api_types: list[str],
        page: int,
        page_size: int,
    ) -> dict[str, Any]:
        """Return filtered request audit rows."""
        return await self._store.query_audit_logs(
            account_id=ctx.account_id,
            request_id=request_id,
            statuses=statuses,
            api_types=api_types,
            page=max(int(page), 1),
            page_size=min(max(int(page_size), 1), 100),
        )

    @staticmethod
    def _validate_date_range(start_date: str, end_date: str) -> None:
        try:
            start = date.fromisoformat(start_date)
            end = date.fromisoformat(end_date)
        except ValueError as exc:
            raise InvalidArgumentError("date must be in YYYY-MM-DD format") from exc
        if end < start:
            raise InvalidArgumentError("end_date must be greater than or equal to start_date")
