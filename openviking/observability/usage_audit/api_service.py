# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0
"""Read service for product Usage/Audit APIs.

The store always persists UTC. The viewer's timezone is resolved per request
from the `?timezone=` query parameter (IANA name, e.g. `Asia/Shanghai`) and
falls back to the server-default tz from config when absent or invalid.
"""

from __future__ import annotations

from datetime import date, datetime
from typing import Any

from openviking.server.identity import RequestContext
from openviking_cli.exceptions import InvalidArgumentError

from .inventory import ContextInventoryProvider
from .store import UsageAuditStore
from .time import resolve_usage_timezone, resolve_user_timezone


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
        self._default_tz = resolve_usage_timezone(timezone_name)

    def _resolve_tz(self, timezone_name: str | None):
        return resolve_user_timezone(timezone_name, fallback=self._default_tz)

    def today(self, timezone_name: str | None = None) -> str:
        """Return today's date in the viewer's timezone (or the default)."""
        tz = self._resolve_tz(timezone_name)
        return datetime.now(tz).date().isoformat()

    async def dashboard_summary(
        self, ctx: RequestContext, *, timezone_name: str | None = None
    ) -> dict[str, Any]:
        """Return all Dashboard top-card data for the viewer's tz."""
        tz = self._resolve_tz(timezone_name)
        today = datetime.now(tz).date().isoformat()
        context_counts = await self._inventory.get_counts(ctx)
        today_tokens = await self._store.get_today_tokens(
            account_id=ctx.account_id, user_date=today, tz=tz
        )
        today_retrievals = await self._store.get_today_retrievals(
            account_id=ctx.account_id, user_date=today, tz=tz
        )
        return {
            "context_counts": context_counts,
            "today_tokens": today_tokens,
            "today_retrievals": today_retrievals,
        }

    async def token_series(
        self,
        *,
        ctx: RequestContext,
        start_date: str,
        end_date: str,
        bucket: str,
        timezone_name: str | None = None,
    ) -> dict[str, Any]:
        """Return token usage series in the viewer's tz."""
        tz = self._resolve_tz(timezone_name)
        self._validate_date_range(start_date, end_date)
        items = await self._store.get_token_series(
            account_id=ctx.account_id,
            start_user_date=start_date,
            end_user_date=end_date,
            bucket=bucket,
            tz=tz,
        )
        return {"start_date": start_date, "end_date": end_date, "bucket": bucket, "items": items}

    async def context_commits(
        self,
        *,
        ctx: RequestContext,
        start_date: str,
        end_date: str,
        bucket: str,
        timezone_name: str | None = None,
    ) -> dict[str, Any]:
        """Return context write heatmap rows in the viewer's tz."""
        tz = self._resolve_tz(timezone_name)
        self._validate_date_range(start_date, end_date)
        items = await self._store.get_context_commit_heatmap(
            account_id=ctx.account_id,
            start_user_date=start_date,
            end_user_date=end_date,
            bucket=bucket,
            tz=tz,
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
        """Return filtered request audit rows.

        `created_at` is returned as a UTC ISO string; the client formats it in
        the viewer's locale on render.
        """
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
