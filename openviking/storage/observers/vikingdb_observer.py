# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0
"""
VikingDBObserver: VikingDB storage observability tool.

Provides methods to observe and report VikingDB collection status.
"""

from typing import Dict, Optional

from openviking.server.identity import RequestContext
from openviking.storage.observers.base_observer import BaseObserver
from openviking.storage.vikingdb_manager import VikingDBManager
from openviking_cli.utils import run_async
from openviking_cli.utils.logger import get_logger

logger = get_logger(__name__)


class VikingDBObserver(BaseObserver):
    """
    VikingDBObserver: System observability tool for VikingDB management.

    Provides methods to query collection status and format output.
    """

    def __init__(self, vikingdb_manager: VikingDBManager):
        self._vikingdb_manager = vikingdb_manager

    async def get_status_table_async(self, ctx: Optional[RequestContext] = None) -> str:
        if not self._vikingdb_manager:
            return "VikingDB manager not initialized."

        backend = (
            await self._vikingdb_manager.get_account_backend(ctx.account_id)
            if ctx is not None else self._vikingdb_manager
        )
        if not await backend.collection_exists():
            return "No collections found."

        statuses = await self._get_collection_statuses(
            [backend.collection_name], ctx=ctx
        )
        return self._format_status_as_table(statuses)

    async def get_status_json_async(self, ctx: Optional[RequestContext] = None) -> dict:
        if not self._vikingdb_manager:
            return {
                "collections": [],
                "summary": {
                    "index_count": 0,
                    "vector_count": 0,
                    "collection_count": 0,
                },
            }

        if not await self._vikingdb_manager.collection_exists():
            return {
                "collections": [],
                "summary": {
                    "index_count": 0,
                    "vector_count": 0,
                    "collection_count": 0,
                },
            }

        statuses = await self._get_collection_statuses(
            [self._vikingdb_manager.collection_name], ctx=ctx
        )
        collections = []
        total_indexes = 0
        total_vectors = 0
        for name, status in statuses.items():
            item = {
                "collection": name,
                "index_count": status.get("index_count", 0),
                "vector_count": status.get("vector_count", 0),
                "ok": not bool(status.get("error")),
            }
            if status.get("error"):
                item["error"] = status["error"]
            collections.append(item)
            total_indexes += item["index_count"]
            total_vectors += item["vector_count"]

        return {
            "collections": collections,
            "summary": {
                "index_count": total_indexes,
                "vector_count": total_vectors,
                "collection_count": len(collections),
            },
        }

    def get_status_table(self, ctx: Optional[RequestContext] = None) -> str:
        return run_async(self.get_status_table_async(ctx=ctx))

    def get_status_json(self, ctx: Optional[RequestContext] = None) -> dict:
        return run_async(self.get_status_json_async(ctx=ctx))

    def __str__(self) -> str:
        return self.get_status_table()

    async def _get_collection_statuses(
        self, collection_names: list, *, ctx: Optional[RequestContext] = None
    ) -> Dict[str, Dict]:
        statuses = {}
        backend = (
            await self._vikingdb_manager.get_account_backend(ctx.account_id)
            if ctx is not None else self._vikingdb_manager
        )

        for name in collection_names:
            try:
                if not await backend.collection_exists():
                    continue

                # Current OpenViking flow uses one managed default index per collection.
                index_count = 1
                vector_count = await self._vikingdb_manager.count(ctx=ctx)

                statuses[name] = {
                    "index_count": index_count,
                    "vector_count": vector_count,
                }
            except Exception as e:
                logger.error(f"Error getting status for collection '{name}': {e}")
                statuses[name] = {
                    "index_count": 0,
                    "vector_count": 0,
                    "error": str(e),
                }

        return statuses

    def _format_status_as_table(self, statuses: Dict[str, Dict]) -> str:
        from tabulate import tabulate

        data = []
        total_indexes = 0
        total_vectors = 0

        for name, status in statuses.items():
            index_count = status.get("index_count", 0)
            vector_count = status.get("vector_count", 0)
            error = status.get("error", "")

            data.append(
                {
                    "Collection": name,
                    "Index Count": index_count,
                    "Vector Count": vector_count,
                    "Status": "ERROR" if error else "OK",
                }
            )
            total_indexes += index_count
            total_vectors += vector_count

        if not data:
            return "No collections found."

        # Add total row
        data.append(
            {
                "Collection": "TOTAL",
                "Index Count": total_indexes,
                "Vector Count": total_vectors,
                "Status": "",
            }
        )

        return tabulate(data, headers="keys", tablefmt="pretty")

    def is_healthy(self) -> bool:
        """
        Check if VikingDB is healthy.

        Returns:
            True if system is healthy, False otherwise
        """
        return not self.has_errors()

    def has_errors(self) -> bool:
        """
        Check if VikingDB has any errors.

        Returns:
            True if errors exist, False otherwise
        """
        try:
            if not self._vikingdb_manager:
                return True
            run_async(self._vikingdb_manager.health_check())
            return False
        except Exception as e:
            logger.error(f"VikingDB health check failed: {e}")
            return True
