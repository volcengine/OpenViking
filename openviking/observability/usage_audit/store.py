# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0
"""Store interfaces for product usage and audit projections."""

from __future__ import annotations

from typing import Any, Protocol, Sequence

from openviking.observability.events import ObservabilityEvent


class UsageAuditStore(Protocol):
    """Persistence contract for product usage/audit data."""

    async def initialize(self) -> None:
        """Initialize the store."""

    async def close(self) -> None:
        """Close the store."""

    async def record_batch(self, events: Sequence[ObservabilityEvent]) -> None:
        """Persist a batch of observability events."""

    async def get_today_tokens(self, *, account_id: str, date: str) -> dict[str, int]:
        """Return token totals for one account and date."""

    async def get_today_retrievals(self, *, account_id: str, date: str) -> dict[str, int]:
        """Return successful find/search counts for one account and date."""

    async def get_agent_overview(
        self, *, account_id: str, date: str, limit: int = 5
    ) -> dict[str, Any]:
        """Return distinct agent count and recent agents for one account/date."""

    async def get_token_series(
        self, *, account_id: str, start_date: str, end_date: str, bucket: str
    ) -> list[dict[str, Any]]:
        """Return token series rows for a date range."""

    async def get_context_commit_heatmap(
        self, *, account_id: str, start_date: str, end_date: str, bucket: str
    ) -> list[dict[str, Any]]:
        """Return context write bucket rows for a date range."""

    async def query_audit_logs(
        self,
        *,
        account_id: str,
        request_id: str | None = None,
        statuses: list[str] | None = None,
        api_types: list[str] | None = None,
        page: int = 1,
        page_size: int = 10,
    ) -> dict[str, Any]:
        """Query request audit rows with summary stats."""
