# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: Apache-2.0
"""
VikingDBObserver: VikingDB storage observability tool.

Provides methods to observe and report VikingDB collection status.
"""

from typing import Dict

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

    async def get_status_table_async(self) -> str:
        if not self._vikingdb_manager:
            return "VikingDB manager not initialized."

        if not await self._vikingdb_manager.collection_exists():
            return "No collections found."

        statuses = await self._get_collection_statuses([self._vikingdb_manager.collection_name])
        return self._format_status_as_table(statuses)

    def get_status_table(self) -> str:
        return run_async(self.get_status_table_async())

    def __str__(self) -> str:
        return self.get_status_table()

    async def _get_collection_statuses(self, collection_names: list) -> Dict[str, Dict]:
        statuses = {}

        for name in collection_names:
            try:
                if not await self._vikingdb_manager.collection_exists():
                    continue

                # Current OpenViking flow uses one managed default index per collection.
                index_count = 1
                vector_count = await self._vikingdb_manager.count()

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
