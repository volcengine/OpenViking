# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: Apache-2.0
"""
RetrievalObserver: Retrieval system observability tool.

Provides methods to observe and report retrieval quality metrics
accumulated by the HierarchicalRetriever.
"""

from openviking.storage.observers.base_observer import BaseObserver
from openviking_cli.utils.logger import get_logger

logger = get_logger(__name__)


class RetrievalObserver(BaseObserver):
    """
    RetrievalObserver: System observability tool for retrieval quality.

    Reads accumulated statistics from the global RetrievalStatsCollector
    and formats them for display via the observer API.
    """

    # A zero-result rate above this threshold is considered unhealthy.
    UNHEALTHY_ZERO_RESULT_RATE = 0.5

    @staticmethod
    def _get_collector():
        """Lazy import to avoid circular dependency with storage module."""
        from openviking.retrieve.retrieval_stats import get_stats_collector

        return get_stats_collector()

    def get_status_table(self) -> str:
        """Format retrieval statistics as a string table."""
        return self._format_status_as_table()

    def _format_status_as_table(self) -> str:
        """Format retrieval stats as a table using tabulate."""
        from tabulate import tabulate

        stats = self._get_collector().snapshot()

        if stats.total_queries == 0:
            return "No retrieval queries recorded."

        summary = [
            {"Metric": "Total Queries", "Value": stats.total_queries},
            {"Metric": "Total Results", "Value": stats.total_results},
            {"Metric": "Avg Results/Query", "Value": f"{stats.avg_results_per_query:.1f}"},
            {"Metric": "Zero-Result Queries", "Value": stats.zero_result_queries},
            {
                "Metric": "Zero-Result Rate",
                "Value": f"{stats.zero_result_rate:.1%}",
            },
            {"Metric": "Avg Score", "Value": f"{stats.avg_score:.4f}"},
            {
                "Metric": "Score Range",
                "Value": f"{stats.min_score:.4f} - {stats.max_score:.4f}"
                if stats.total_results > 0
                else "N/A",
            },
            {"Metric": "Rerank Used", "Value": stats.rerank_used},
            {"Metric": "Rerank Fallback", "Value": stats.rerank_fallback},
            {"Metric": "Avg Latency (ms)", "Value": f"{stats.avg_latency_ms:.1f}"},
            {"Metric": "Max Latency (ms)", "Value": f"{stats.max_latency_ms:.1f}"},
        ]

        lines = [tabulate(summary, headers="keys", tablefmt="pretty")]

        # Query breakdown by context type
        if stats.queries_by_type:
            type_data = [
                {"Context Type": ctype, "Queries": count}
                for ctype, count in sorted(
                    stats.queries_by_type.items(), key=lambda x: x[1], reverse=True
                )
            ]
            lines.append("")
            lines.append(tabulate(type_data, headers="keys", tablefmt="pretty"))

        return "\n".join(lines)

    def __str__(self) -> str:
        return self.get_status_table()

    def is_healthy(self) -> bool:
        """Retrieval is healthy when the zero-result rate is acceptable."""
        stats = self._get_collector().snapshot()
        if stats.total_queries == 0:
            return True
        return stats.zero_result_rate < self.UNHEALTHY_ZERO_RESULT_RATE

    def has_errors(self) -> bool:
        """Errors are flagged when too many queries return zero results."""
        stats = self._get_collector().snapshot()
        if stats.total_queries < 5:
            return False
        return stats.zero_result_rate >= self.UNHEALTHY_ZERO_RESULT_RATE
