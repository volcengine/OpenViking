#!/usr/bin/env python
# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: Apache-2.0
"""
Play recorder CLI tool.

Replay recorded IO operations and compare performance across different backends.

Usage:
    uv run play_recorder.py --record_file ./records/io_recorder_20260214.jsonl --config_file ./ov.conf
    uv run play_recorder.py --record_file ./records/io_recorder_20260214.jsonl --stats-only
    uv run play_recorder.py --record_file ./records/io_recorder_20260214.jsonl --config_file ./ov.conf --fs
    uv run play_recorder.py --record_file ./records/io_recorder_20260214.jsonl --config_file ./ov.conf --vikingdb
"""

import argparse
import asyncio
import json
import sys
from pathlib import Path

from openviking_cli.utils.logger import get_logger

from .playback import (
    IOPlayback,
    PlaybackStats,
)
from .record_analysis import (
    analyze_records,
    print_analysis_stats,
)

logger = get_logger(__name__)





def print_playback_stats(stats: PlaybackStats) -> None:
    """Print playback statistics."""
    print(f"\n{'=' * 60}")
    print("Playback Results")
    print(f"{'=' * 60}")

    print(f"\nTotal Records: {stats.total_records}")
    print(f"Successful: {stats.success_count}")
    print(f"Failed: {stats.error_count}")
    print(f"Success Rate: {stats.success_count / stats.total_records * 100:.1f}%" if stats.total_records > 0 else "N/A")

    print("\nPerformance:")
    print(f"  Original Total Latency: {stats.total_original_latency_ms:.2f} ms")
    print(f"  Playback Total Latency: {stats.total_playback_latency_ms:.2f} ms")

    speedup = stats.to_dict().get("speedup_ratio", 0)
    if speedup > 0:
        if speedup > 1:
            print(f"  Speedup: {speedup:.2f}x (playback is faster)")
        else:
            print(f"  Slowdown: {1/speedup:.2f}x (playback is slower)")

    if stats.total_viking_fs_operations > 0:
        stats_dict = stats.to_dict()
        viking_fs_stats = stats_dict.get("viking_fs_stats", {})
        agfs_fs_stats = stats_dict.get("agfs_fs_stats", {})

        print("\nVikingFS Detailed Stats:")
        print(f"  Total VikingFS Operations: {viking_fs_stats.get('total_operations', 0)}")
        print(f"  VikingFS Success Rate: {viking_fs_stats.get('success_rate_percent', 0):.1f}%")
        print(f"  Average AGFS Calls per VikingFS Operation: {viking_fs_stats.get('avg_agfs_calls_per_operation', 0):.2f}")

        print("\nAGFS FS Detailed Stats:")
        print(f"  Total AGFS Calls: {agfs_fs_stats.get('total_calls', 0)}")
        print(f"  AGFS Success Rate: {agfs_fs_stats.get('success_rate_percent', 0):.1f}%")

    if stats.fs_stats:
        print("\nFS Operations:")
        print(f"{'Operation':<30} {'Count':>10} {'Orig Avg (ms)':>15} {'Play Avg (ms)':>15}")
        print(f"{'-' * 72}")
        for op, data in sorted(stats.fs_stats.items()):
            count = data["count"]
            orig_avg = data["total_original_latency_ms"] / count if count > 0 else 0
            play_avg = data["total_playback_latency_ms"] / count if count > 0 else 0
            print(f"{op:<30} {count:>10} {orig_avg:>15.2f} {play_avg:>15.2f}")

    if stats.vikingdb_stats:
        print("\nVikingDB Operations:")
        print(f"{'Operation':<30} {'Count':>10} {'Orig Avg (ms)':>15} {'Play Avg (ms)':>15}")
        print(f"{'-' * 72}")
        for op, data in sorted(stats.vikingdb_stats.items()):
            count = data["count"]
            orig_avg = data["total_original_latency_ms"] / count if count > 0 else 0
            play_avg = data["total_playback_latency_ms"] / count if count > 0 else 0
            print(f"{op:<30} {count:>10} {orig_avg:>15.2f} {play_avg:>15.2f}")


async def main_async(args: argparse.Namespace) -> int:
    """Main async function."""
    record_file = Path(args.record_file)
    if not record_file.exists():
        logger.error(f"Record file not found: {record_file}")
        return 1

    if args.stats_only:
        io_type = args.io_type
        if args.fs and not args.vikingdb:
            io_type = "fs"
        elif args.vikingdb and not args.fs:
            io_type = "vikingdb"

        stats = analyze_records(
            record_file=str(record_file),
            io_type=io_type,
            operation=args.operation,
        )
        print_analysis_stats(stats)
        return 0

    enable_fs = args.fs
    enable_vikingdb = args.vikingdb

    if not enable_fs and not enable_vikingdb:
        enable_fs = True
        enable_vikingdb = True

    playback = IOPlayback(
        config_file=args.config_file,
        compare_response=args.compare_response,
        fail_fast=args.fail_fast,
        enable_fs=enable_fs,
        enable_vikingdb=enable_vikingdb,
    )

    stats = await playback.play(
        record_file=str(record_file),
        limit=args.limit,
        offset=args.offset,
        io_type=args.io_type,
        operation=args.operation,
    )

    print_playback_stats(stats)

    if args.output:
        with open(args.output, "w", encoding="utf-8") as f:
            json.dump(stats.to_dict(), f, indent=2, ensure_ascii=False)
        logger.info(f"Results saved to: {args.output}")

    return 0 if stats.error_count == 0 else 1


def main() -> int:
    """Main entry point."""
    parser = argparse.ArgumentParser(
        description="Play recorded IO operations and compare performance",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  # Show statistics only
  uv run play_recorder.py --record_file ./records/io_recorder_20260214.jsonl --stats-only

  # Playback with remote config
  uv run play_recorder.py --record_file ./records/io_recorder_20260214.jsonl --config_file ./ov-remote.conf

  # Only test FS operations
  uv run play_recorder.py --record_file ./records/io_recorder_20260214.jsonl --config_file ./ov.conf --fs

  # Only test VikingDB operations
  uv run play_recorder.py --record_file ./records/io_recorder_20260214.jsonl --config_file ./ov.conf --vikingdb

  # Filter by operation type
  uv run play_recorder.py --record_file ./records/io_recorder_20260214.jsonl --config_file ./ov.conf --io-type fs --operation read

  # Save results to file
  uv run play_recorder.py --record_file ./records/io_recorder_20260214.jsonl --config_file ./ov.conf --output results.json
        """,
    )

    parser.add_argument(
        "--record_file",
        type=str,
        required=True,
        help="Path to the record JSONL file",
    )
    parser.add_argument(
        "--config_file",
        type=str,
        default=None,
        help="Path to OpenViking config file (ov.conf)",
    )
    parser.add_argument(
        "--stats-only",
        action="store_true",
        help="Only show statistics without playback",
    )
    parser.add_argument(
        "--fs",
        action="store_true",
        help="Only play FS operations (default: both FS and VikingDB)",
    )
    parser.add_argument(
        "--vikingdb",
        action="store_true",
        help="Only play VikingDB operations (default: both FS and VikingDB)",
    )
    parser.add_argument(
        "--limit",
        type=int,
        default=None,
        help="Maximum number of records to play",
    )
    parser.add_argument(
        "--offset",
        type=int,
        default=0,
        help="Number of records to skip",
    )
    parser.add_argument(
        "--io-type",
        type=str,
        choices=["fs", "vikingdb"],
        default=None,
        help="Filter by IO type",
    )
    parser.add_argument(
        "--operation",
        type=str,
        default=None,
        help="Filter by operation name (e.g., read, search)",
    )
    parser.add_argument(
        "--compare-response",
        action="store_true",
        help="Compare playback response with original",
    )
    parser.add_argument(
        "--fail-fast",
        action="store_true",
        help="Stop on first error",
    )
    parser.add_argument(
        "--output",
        type=str,
        default=None,
        help="Output file for results (JSON)",
    )

    args = parser.parse_args()
    return asyncio.run(main_async(args))


if __name__ == "__main__":
    sys.exit(main())
