"""
FilesystemObserver: Filesystem observability tool.

Provides methods to observe and report filesystem operation statistics.
"""

from typing import Dict, Optional

from openviking.storage.observers.base_observer import BaseObserver
from openviking_cli.utils import run_async
from openviking_cli.utils.logger import get_logger

logger = get_logger(__name__)


class FilesystemObserver(BaseObserver):
    """
    FilesystemObserver: System observability tool for filesystem operations.

    Reads accumulated statistics from RAGFS and formats them for display.
    """

    def __init__(self, mount_path: Optional[str] = None):
        """
        Initialize the filesystem observer.

        Args:
            mount_path: Optional specific mount path to observe. If None,
                        observe all mounts.
        """
        self.mount_path = mount_path

    @staticmethod
    def _get_collector():
        """
        Get the stats collector from the service.

        Lazy import to avoid circular dependencies.
        """
        from openviking.server.dependencies import get_service
        service = get_service()
        return service.debug.observer

    def _format_stats_table(self, stats_data: dict) -> str:
        """
        Format filesystem statistics as a string table.

        Args:
            stats_data: The statistics data from RAGFS.

        Returns:
            Formatted table string.
        """
        from tabulate import tabulate

        if not stats_data:
            return "No filesystem statistics available."

        # Handle both single mount and all mounts cases
        if "path" in stats_data:
            # Single mount
            mounts = [stats_data]
        else:
            # All mounts
            mounts = stats_data.get("mounts", [])

        if not mounts:
            return "No filesystem statistics available."

        result = []
        # Sort mounts by path for consistent output
        mounts = sorted(mounts, key=lambda m: m.get("path", ""))
        for mount in mounts:
            mount_path = mount.get("path", "unknown")
            plugin_name = mount.get("plugin", "unknown")
            stats = mount.get("stats", {})
            operations = stats.get("operations", {})

            result.append(f"Mount: {mount_path} (plugin: {plugin_name})")
            result.append("=" * 60)

            # Prepare operation statistics
            table_data = []
            total_ops = 0
            total_time_us = 0

            for op_name, op_stats in operations.items():
                if op_stats:
                    count = op_stats.get("count", 0)
                    total_ops += count
                    total_time_us += op_stats.get("total_time_us", 0)

                    avg_us = op_stats.get("avg_time_us", 0.0)
                    min_us = op_stats.get("min_time_us", 0)
                    max_us = op_stats.get("max_time_us", 0)

                    if count > 0:
                        table_data.append({
                            "Operation": op_name,
                            "Count": count,
                            "Avg (ms)": f"{avg_us / 1000:.3f}",
                            "Min (ms)": f"{min_us / 1000:.3f}",
                            "Max (ms)": f"{max_us / 1000:.3f}",
                        })

            if table_data:
                result.append(tabulate(table_data, headers="keys", tablefmt="pretty"))
                result.append("")

                # Summary
                avg_total_us = total_time_us / total_ops if total_ops > 0 else 0
                summary = [
                    {"Metric": "Total Operations", "Value": total_ops},
                    {"Metric": "Total Time (s)", "Value": f"{total_time_us / 1000000:.3f}"},
                    {"Metric": "Overall Avg (ms)", "Value": f"{avg_total_us / 1000:.3f}"},
                ]
                result.append(tabulate(summary, headers="keys", tablefmt="pretty"))
            else:
                result.append("No operation statistics recorded yet.")

            result.append("\n")

        return "\n".join(result)

    def get_status_table(self) -> str:
        """
        Format filesystem statistics as a string table.

        Returns:
            Formatted table string.
        """
        try:
            observer_service = self._get_collector()
            stats_data = run_async(observer_service.get_filesystem_stats(self.mount_path))
            return self._format_stats_table(stats_data)
        except Exception as e:
            logger.error(f"Error getting filesystem stats: {e}")
            return f"Error retrieving filesystem statistics: {e}"

    def __str__(self) -> str:
        return self.get_status_table()

    def is_healthy(self) -> bool:
        """
        Check if the filesystem is healthy.

        Returns:
            True if filesystem is healthy, False otherwise.
        """
        # For now, always return True unless we have specific error conditions
        return True

    def has_errors(self) -> bool:
        """
        Check if there are filesystem errors.

        Returns:
            True if there are errors, False otherwise.
        """
        # For now, always return False unless we have specific error conditions
        return False
