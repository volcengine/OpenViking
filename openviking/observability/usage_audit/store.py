# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0
"""Store interfaces for product usage and audit projections."""

from __future__ import annotations

from datetime import tzinfo
from typing import Any, Protocol, Sequence

from openviking.observability.events import ObservabilityEvent


class UsageAuditStore(Protocol):
    """Persistence contract for product usage/audit data.

    Time-keyed columns are stored in UTC; read methods accept a viewer-supplied
    `tz` and rebucket on the fly.
    """

    async def initialize(self) -> None:
        """Initialize the store."""

    async def close(self) -> None:
        """Close the store."""

    async def record_batch(self, events: Sequence[ObservabilityEvent]) -> None:
        """Persist a batch of observability events."""

    async def get_today_tokens(
        self, *, account_id: str, user_date: str, tz: tzinfo
    ) -> dict[str, int]:
        """Return token totals for one account and viewer-local date."""

    async def get_today_retrievals(
        self, *, account_id: str, user_date: str, tz: tzinfo
    ) -> dict[str, int]:
        """Return successful find/search counts for one account and date."""

    async def get_token_series(
        self,
        *,
        account_id: str,
        start_user_date: str,
        end_user_date: str,
        bucket: str,
        tz: tzinfo,
    ) -> list[dict[str, Any]]:
        """Return token series rows for a viewer-local date range."""

    async def get_context_commit_heatmap(
        self,
        *,
        account_id: str,
        start_user_date: str,
        end_user_date: str,
        bucket: str,
        tz: tzinfo,
    ) -> list[dict[str, Any]]:
        """Return context write bucket rows for a viewer-local date range."""

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
