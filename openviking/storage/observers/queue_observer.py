# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: Apache-2.0
"""
QueueObserver: Queue system observability tool.

Provides methods to observe and report queue status in various formats.
"""

from typing import Dict

from openviking.storage.observers.base_observer import BaseObserver
from openviking.storage.queuefs.named_queue import QueueStatus
from openviking.storage.queuefs.queue_manager import QueueManager
from openviking.utils import run_async
from openviking.utils.logger import get_logger

logger = get_logger(__name__)


class QueueObserver(BaseObserver):
    """
    QueueObserver: System observability tool for queue management.

    Provides methods to query queue status and format output.
    """

    def __init__(self, queue_manager: QueueManager):
        self._queue_manager = queue_manager

    async def get_status_table_async(self) -> str:
        statuses = await self._queue_manager.check_status()
        return self._format_status_as_table(statuses)

    def get_status_table(self) -> str:
        return run_async(self.get_status_table_async())

    def __str__(self) -> str:
        return self.get_status_table()

    def _format_status_as_table(self, statuses: Dict[str, QueueStatus]) -> str:
        """
        Format queue statuses as a string table.

        Args:
            statuses: Dict mapping queue names to QueueStatus

        Returns:
            Formatted table string
        """
        if not statuses:
            return "No queue status data available."

        data = []
        total_pending = 0
        total_in_progress = 0
        total_processed = 0
        total_errors = 0

        for queue_name, status in statuses.items():
            total = status.pending + status.in_progress + status.processed
            data.append(
                {
                    "Queue": queue_name,
                    "Pending": str(status.pending),
                    "In Progress": str(status.in_progress),
                    "Processed": str(status.processed),
                    "Errors": str(status.error_count),
                    "Total": str(total),
                }
            )
            total_pending += status.pending
            total_in_progress += status.in_progress
            total_processed += status.processed
            total_errors += status.error_count

        # Add total row
        total_total = total_pending + total_in_progress + total_processed
        data.append(
            {
                "Queue": "TOTAL",
                "Pending": str(total_pending),
                "In Progress": str(total_in_progress),
                "Processed": str(total_processed),
                "Errors": str(total_errors),
                "Total": str(total_total),
            }
        )

        # Simple table formatter
        headers = ["Queue", "Pending", "In Progress", "Processed", "Errors", "Total"]
        # Default minimum widths similar to previous col_space
        min_widths = {
            "Queue": 20,
            "Pending": 10,
            "In Progress": 12,
            "Processed": 10,
            "Errors": 8,
            "Total": 10,
        }

        col_widths = {h: len(h) for h in headers}

        # Calculate max width based on content and min_widths
        for row in data:
            for h in headers:
                content_len = len(str(row.get(h, "")))
                col_widths[h] = max(col_widths[h], content_len, min_widths.get(h, 0))

        # Add padding
        for h in headers:
            col_widths[h] += 2

        # Build string
        lines = []

        # Header
        header_line = "".join(h.ljust(col_widths[h]) for h in headers)
        lines.append(header_line)

        # Rows
        for row in data:
            line = "".join(str(row.get(h, "")).ljust(col_widths[h]) for h in headers)
            lines.append(line)

        return "\n".join(lines)

    def is_healthy(self) -> bool:
        return not self.has_errors()

    def has_errors(self) -> bool:
        return self._queue_manager.has_errors()
