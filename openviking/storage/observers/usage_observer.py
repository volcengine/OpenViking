# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0
"""
UsageObserver: Context usage metrics observer.

Provides vector count, memory count, resource count, and session count
breakdowns for the observer API.
"""

from typing import Any, Dict, Optional

from openviking.server.identity import RequestContext
from openviking.storage.observers.base_observer import BaseObserver
from openviking.storage.vikingdb_manager import VikingDBManager
from openviking_cli.utils import run_async
from openviking_cli.utils.logger import get_logger

logger = get_logger(__name__)


class UsageObserver(BaseObserver):
    """Observes context usage metrics: vectors, memories, resources, sessions."""

    def __init__(self, vikingdb_manager: Optional[VikingDBManager] = None):
        self._vikingdb = vikingdb_manager
        self._last_usage: Dict[str, Any] = {}

    async def get_usage_async(self, ctx: Optional[RequestContext] = None) -> Dict[str, Any]:
        """Collect usage metrics.

        Returns:
            Dict with total_vectors and breakdown by context type.
        """
        result: Dict[str, Any] = {
            "total_vectors": 0,
        }

        # Vector count from VikingDB
        if self._vikingdb is not None:
            try:
                if await self._vikingdb.collection_exists():
                    result["total_vectors"] = await self._vikingdb.count()
            except Exception as e:
                logger.warning(f"Failed to get vector count: {e}")
                result["total_vectors"] = -1

        self._last_usage = result
        return result

    def get_usage(self, ctx: Optional[RequestContext] = None) -> Dict[str, Any]:
        """Synchronous wrapper for get_usage_async."""
        return run_async(self.get_usage_async(ctx=ctx))

    async def get_status_table_async(self, ctx: Optional[RequestContext] = None) -> str:
        """Format usage metrics as a table (async variant for async call sites)."""
        from tabulate import tabulate

        usage = await self.get_usage_async(ctx=ctx)

        data = [
            {"Metric": "Total Vectors", "Value": usage.get("total_vectors", 0)},
        ]

        return tabulate(data, headers="keys", tablefmt="pretty")

    def get_status_table(self, ctx: Optional[RequestContext] = None) -> str:
        """Synchronous wrapper around get_status_table_async for BaseObserver compatibility."""
        return run_async(self.get_status_table_async(ctx=ctx))

    def is_healthy(self) -> bool:
        """Usage observer is healthy if VikingDB is available."""
        return self._vikingdb is not None

    def has_errors(self) -> bool:
        """Check if the last usage query had errors."""
        return self._last_usage.get("total_vectors", 0) == -1
