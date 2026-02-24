# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: Apache-2.0
"""
Record Analysis module for IORecorder.

Analyzes recorded IO operations to provide insights into performance metrics.
"""

import json
from dataclasses import dataclass
from typing import Any, Dict, List, Optional

from openviking.eval.recorder import IORecord, IOType
from openviking_cli.utils.logger import get_logger

logger = get_logger(__name__)


@dataclass
class OperationStats:
    """
    Statistics for a single operation type.

    Attributes:
        count: Number of operations
        total_latency_ms: Total latency across all operations
        avg_latency_ms: Average latency per operation
        min_latency_ms: Minimum latency
        max_latency_ms: Maximum latency
        success_count: Number of successful operations
        error_count: Number of failed operations
        success_rate_percent: Success rate percentage
    """

    count: int = 0
    total_latency_ms: float = 0.0
    avg_latency_ms: float = 0.0
    min_latency_ms: float = float("inf")
    max_latency_ms: float = 0.0
    success_count: int = 0
    error_count: int = 0
    success_rate_percent: float = 0.0


@dataclass
class VikingFSStats:
    """
    Statistics for VikingFS operations.

    Attributes:
        total_operations: Total number of VikingFS operations
        success_count: Number of successful VikingFS operations
        error_count: Number of failed VikingFS operations
        success_rate_percent: Success rate percentage
        total_agfs_calls: Total number of AGFS calls across all operations
        avg_agfs_calls_per_operation: Average AGFS calls per VikingFS operation
        agfs_total_latency_ms: Total AGFS latency across all calls
        agfs_avg_latency_ms: Average AGFS latency per call
        agfs_success_count: Number of successful AGFS calls
        agfs_error_count: Number of failed AGFS calls
        agfs_success_rate_percent: AGFS success rate percentage
    """

    total_operations: int = 0
    success_count: int = 0
    error_count: int = 0
    success_rate_percent: float = 0.0
    total_agfs_calls: int = 0
    avg_agfs_calls_per_operation: float = 0.0
    agfs_total_latency_ms: float = 0.0
    agfs_avg_latency_ms: float = 0.0
    agfs_success_count: int = 0
    agfs_error_count: int = 0
    agfs_success_rate_percent: float = 0.0


@dataclass
class RecordAnalysisStats:
    """
    Comprehensive statistics for record file analysis.

    Attributes:
        file_path: Path to the record file
        total_records: Total number of records
        fs_count: Number of FS operations
        vikingdb_count: Number of VikingDB operations
        total_latency_ms: Total latency across all operations
        fs_operations: Statistics per FS operation type
        vikingdb_operations: Statistics per VikingDB operation type
        viking_fs_stats: Detailed VikingFS statistics
        time_range: Time range of the records
    """

    file_path: str
    total_records: int = 0
    fs_count: int = 0
    vikingdb_count: int = 0
    total_latency_ms: float = 0.0
    fs_operations: Dict[str, OperationStats] = None
    vikingdb_operations: Dict[str, OperationStats] = None
    viking_fs_stats: Optional[VikingFSStats] = None
    time_range: Dict[str, Optional[str]] = None

    def __post_init__(self):
        if self.fs_operations is None:
            self.fs_operations = {}
        if self.vikingdb_operations is None:
            self.vikingdb_operations = {}
        if self.time_range is None:
            self.time_range = {"start": None, "end": None}

    def to_dict(self) -> Dict[str, Any]:
        """Convert to dictionary for output."""
        result = {
            "file_path": self.file_path,
            "total_records": self.total_records,
            "fs_count": self.fs_count,
            "vikingdb_count": self.vikingdb_count,
            "total_latency_ms": self.total_latency_ms,
            "time_range": self.time_range,
            "fs_operations": {
                op: {
                    "count": stats.count,
                    "total_latency_ms": stats.total_latency_ms,
                    "avg_latency_ms": stats.avg_latency_ms,
                    "min_latency_ms": stats.min_latency_ms,
                    "max_latency_ms": stats.max_latency_ms,
                    "success_count": stats.success_count,
                    "error_count": stats.error_count,
                    "success_rate_percent": stats.success_rate_percent,
                }
                for op, stats in self.fs_operations.items()
            },
            "vikingdb_operations": {
                op: {
                    "count": stats.count,
                    "total_latency_ms": stats.total_latency_ms,
                    "avg_latency_ms": stats.avg_latency_ms,
                    "min_latency_ms": stats.min_latency_ms,
                    "max_latency_ms": stats.max_latency_ms,
                    "success_count": stats.success_count,
                    "error_count": stats.error_count,
                    "success_rate_percent": stats.success_rate_percent,
                }
                for op, stats in self.vikingdb_operations.items()
            },
        }

        if self.viking_fs_stats:
            result["viking_fs_stats"] = {
                "total_operations": self.viking_fs_stats.total_operations,
                "success_count": self.viking_fs_stats.success_count,
                "error_count": self.viking_fs_stats.error_count,
                "success_rate_percent": self.viking_fs_stats.success_rate_percent,
                "total_agfs_calls": self.viking_fs_stats.total_agfs_calls,
                "avg_agfs_calls_per_operation": self.viking_fs_stats.avg_agfs_calls_per_operation,
                "agfs_total_latency_ms": self.viking_fs_stats.agfs_total_latency_ms,
                "agfs_avg_latency_ms": self.viking_fs_stats.agfs_avg_latency_ms,
                "agfs_success_count": self.viking_fs_stats.agfs_success_count,
                "agfs_error_count": self.viking_fs_stats.agfs_error_count,
                "agfs_success_rate_percent": self.viking_fs_stats.agfs_success_rate_percent,
            }

        return result


def load_records(record_file: str) -> List[IORecord]:
    """
    Load records from a JSONL file.

    Args:
        record_file: Path to the record file

    Returns:
        List of IORecord objects
    """
    records = []
    with open(record_file, "r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if line:
                records.append(IORecord.from_dict(json.loads(line)))
    return records


def _update_operation_stats(
    stats_dict: Dict[str, OperationStats], operation: str, record: IORecord
) -> None:
    """
    Update operation statistics with a new record.

    Args:
        stats_dict: Dictionary of operation stats
        operation: Operation name
        record: IO record to process
    """
    if operation not in stats_dict:
        stats_dict[operation] = OperationStats()

    stats = stats_dict[operation]
    stats.count += 1
    stats.total_latency_ms += record.latency_ms

    if record.latency_ms < stats.min_latency_ms:
        stats.min_latency_ms = record.latency_ms
    if record.latency_ms > stats.max_latency_ms:
        stats.max_latency_ms = record.latency_ms

    if record.success:
        stats.success_count += 1
    else:
        stats.error_count += 1


def _finalize_operation_stats(stats_dict: Dict[str, OperationStats]) -> None:
    """
    Calculate derived statistics for all operations.

    Args:
        stats_dict: Dictionary of operation stats to finalize
    """
    for stats in stats_dict.values():
        if stats.count > 0:
            stats.avg_latency_ms = stats.total_latency_ms / stats.count
            stats.success_rate_percent = (
                stats.success_count / stats.count * 100
            )
        else:
            stats.avg_latency_ms = 0.0
            stats.min_latency_ms = 0.0
            stats.success_rate_percent = 0.0


def analyze_records(
    record_file: str,
    io_type: Optional[str] = None,
    operation: Optional[str] = None,
) -> RecordAnalysisStats:
    """
    Analyze a record file and return comprehensive statistics.

    Args:
        record_file: Path to the record file
        io_type: Optional filter by IO type (fs or vikingdb)
        operation: Optional filter by operation name

    Returns:
        RecordAnalysisStats with comprehensive analysis results
    """
    records = load_records(record_file)
    stats = RecordAnalysisStats(file_path=record_file)

    viking_fs_stats = VikingFSStats()

    for record in records:
        if io_type and record.io_type != io_type:
            continue
        if operation and record.operation != operation:
            continue

        stats.total_records += 1
        stats.total_latency_ms += record.latency_ms

        if stats.time_range["start"] is None:
            stats.time_range["start"] = record.timestamp
        stats.time_range["end"] = record.timestamp

        if record.io_type == IOType.FS.value:
            stats.fs_count += 1
            _update_operation_stats(stats.fs_operations, record.operation, record)

            if hasattr(record, "agfs_calls") and record.agfs_calls:
                viking_fs_stats.total_operations += 1
                if record.success:
                    viking_fs_stats.success_count += 1
                else:
                    viking_fs_stats.error_count += 1

                viking_fs_stats.total_agfs_calls += len(record.agfs_calls)

                for call in record.agfs_calls:
                    if isinstance(call, dict):
                        viking_fs_stats.agfs_total_latency_ms += call.get("latency_ms", 0.0)
                        if call.get("success", True):
                            viking_fs_stats.agfs_success_count += 1
                        else:
                            viking_fs_stats.agfs_error_count += 1
                    else:
                        viking_fs_stats.agfs_total_latency_ms += call.latency_ms
                        if call.success:
                            viking_fs_stats.agfs_success_count += 1
                        else:
                            viking_fs_stats.agfs_error_count += 1
        else:
            stats.vikingdb_count += 1
            _update_operation_stats(stats.vikingdb_operations, record.operation, record)

    _finalize_operation_stats(stats.fs_operations)
    _finalize_operation_stats(stats.vikingdb_operations)

    if viking_fs_stats.total_operations > 0:
        viking_fs_stats.success_rate_percent = (
            viking_fs_stats.success_count / viking_fs_stats.total_operations * 100
        )
        viking_fs_stats.avg_agfs_calls_per_operation = (
            viking_fs_stats.total_agfs_calls / viking_fs_stats.total_operations
        )

        agfs_total = viking_fs_stats.agfs_success_count + viking_fs_stats.agfs_error_count
        if agfs_total > 0:
            viking_fs_stats.agfs_avg_latency_ms = (
                viking_fs_stats.agfs_total_latency_ms / agfs_total
            )
            viking_fs_stats.agfs_success_rate_percent = (
                viking_fs_stats.agfs_success_count / agfs_total * 100
            )

        stats.viking_fs_stats = viking_fs_stats

    return stats


def print_analysis_stats(stats: RecordAnalysisStats) -> None:
    """
    Print analysis statistics in a human-readable format using tables.

    Args:
        stats: RecordAnalysisStats to print
    """
    print("=" * 80)
    print("Record Analysis Report")
    print("=" * 80)

    print(f"\nFile: {stats.file_path}")
    print(f"Total Records: {stats.total_records}")
    print(f"FS Operations: {stats.fs_count}")
    print(f"VikingDB Operations: {stats.vikingdb_count}")
    print(f"Total Latency: {stats.total_latency_ms:.2f}ms")

    if stats.time_range["start"] and stats.time_range["end"]:
        print(f"Time Range: {stats.time_range['start']} to {stats.time_range['end']}")

    if stats.viking_fs_stats:
        vfs = stats.viking_fs_stats
        print("\n" + "=" * 80)
        print("VikingFS Detailed Statistics")
        print("=" * 80)

        print("\n" + "-" * 50)
        print(f"{'Metric':<30} {'Value':>18}")
        print("-" * 50)
        print(f"{'Total VikingFS Operations':<30} {vfs.total_operations:>18}")
        print(f"{'Success':<30} {vfs.success_count:>18}")
        print(f"{'Errors':<30} {vfs.error_count:>18}")
        print(f"{'Success Rate':<30} {f'{vfs.success_rate_percent:.1f}%':>18}")
        print("-" * 50)
        print(f"{'Total AGFS Calls':<30} {vfs.total_agfs_calls:>18}")
        print(f"{'Avg AGFS Calls per Op':<30} {f'{vfs.avg_agfs_calls_per_operation:.2f}':>18}")
        print("-" * 50)
        print(f"{'AGFS Total Latency':<30} {f'{vfs.agfs_total_latency_ms:.2f}ms':>18}")
        print(f"{'AGFS Avg Latency':<30} {f'{vfs.agfs_avg_latency_ms:.2f}ms':>18}")
        print(f"{'AGFS Success':<30} {vfs.agfs_success_count:>18}")
        print(f"{'AGFS Errors':<30} {vfs.agfs_error_count:>18}")
        print(f"{'AGFS Success Rate':<30} {f'{vfs.agfs_success_rate_percent:.1f}%':>18}")
        print("-" * 50)

    if stats.fs_operations:
        print("\n" + "=" * 80)
        print("FS Operation Statistics")
        print("=" * 80)

        all_ops = list(stats.fs_operations.keys())
        op_width = max(len(op) for op in all_ops) if all_ops else 15
        op_width = max(op_width, 15)
        table_width = op_width + 6 + 12 + 12 + 12 + 12 + 10 + 10 + 10 + 9

        print("\n" + "-" * table_width)
        print(
            f"{'Operation':<{op_width}} "
            f"{'Count':>6} "
            f"{'Total(ms)':>12} "
            f"{'Avg(ms)':>12} "
            f"{'Min(ms)':>12} "
            f"{'Max(ms)':>12} "
            f"{'Success':>10} "
            f"{'Errors':>10} "
            f"{'Rate':>10}"
        )
        print("-" * table_width)
        for op, op_stats in sorted(stats.fs_operations.items()):
            print(
                f"{op:<{op_width}} "
                f"{op_stats.count:>6} "
                f"{op_stats.total_latency_ms:>12.2f} "
                f"{op_stats.avg_latency_ms:>12.2f} "
                f"{op_stats.min_latency_ms:>12.2f} "
                f"{op_stats.max_latency_ms:>12.2f} "
                f"{op_stats.success_count:>10} "
                f"{op_stats.error_count:>10} "
                f"{f'{op_stats.success_rate_percent:.1f}%':>10}"
            )
        print("-" * table_width)

    if stats.vikingdb_operations:
        print("\n" + "=" * 80)
        print("VikingDB Operation Statistics")
        print("=" * 80)

        all_ops = list(stats.vikingdb_operations.keys())
        op_width = max(len(op) for op in all_ops) if all_ops else 15
        op_width = max(op_width, 15)
        table_width = op_width + 6 + 12 + 12 + 12 + 12 + 10 + 10 + 10 + 9

        print("\n" + "-" * table_width)
        print(
            f"{'Operation':<{op_width}} "
            f"{'Count':>6} "
            f"{'Total(ms)':>12} "
            f"{'Avg(ms)':>12} "
            f"{'Min(ms)':>12} "
            f"{'Max(ms)':>12} "
            f"{'Success':>10} "
            f"{'Errors':>10} "
            f"{'Rate':>10}"
        )
        print("-" * table_width)
        for op, op_stats in sorted(stats.vikingdb_operations.items()):
            print(
                f"{op:<{op_width}} "
                f"{op_stats.count:>6} "
                f"{op_stats.total_latency_ms:>12.2f} "
                f"{op_stats.avg_latency_ms:>12.2f} "
                f"{op_stats.min_latency_ms:>12.2f} "
                f"{op_stats.max_latency_ms:>12.2f} "
                f"{op_stats.success_count:>10} "
                f"{op_stats.error_count:>10} "
                f"{f'{op_stats.success_rate_percent:.1f}%':>10}"
            )
        print("-" * table_width)

    print("\n" + "=" * 80)
    print("Analysis Complete")
    print("=" * 80)
