#!/usr/bin/env python
# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: Apache-2.0
"""
Record Analysis CLI tool.

Analyzes recorded IO operations to provide insights into performance metrics.

Usage:
    uv run analyze_records.py --record_file ./records/io_recorder_20260214.jsonl
    uv run analyze_records.py --record_file ./records/io_recorder_20260214.jsonl --fs
    uv run analyze_records.py --record_file ./records/io_recorder_20260214.jsonl --vikingdb
    uv run analyze_records.py --record_file ./records/io_recorder_20260214.jsonl --io-type fs --operation read
"""

import argparse
import json
import sys
from pathlib import Path

from openviking_cli.utils.logger import get_logger

from .record_analysis import (
    analyze_records,
    print_analysis_stats,
)

logger = get_logger(__name__)


def main() -> int:
    """Main entry point."""
    parser = argparse.ArgumentParser(
        description="Analyze recorded IO operations",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  # Analyze all records
  uv run analyze_records.py --record_file ./records/io_recorder_20260214.jsonl

  # Only analyze FS operations
  uv run analyze_records.py --record_file ./records/io_recorder_20260214.jsonl --fs

  # Only analyze VikingDB operations
  uv run analyze_records.py --record_file ./records/io_recorder_20260214.jsonl --vikingdb

  # Filter by operation type
  uv run analyze_records.py --record_file ./records/io_recorder_20260214.jsonl --io-type fs --operation read

  # Save results to file
  uv run analyze_records.py --record_file ./records/io_recorder_20260214.jsonl --output analysis.json
        """,
    )

    parser.add_argument(
        "--record_file",
        type=str,
        required=True,
        help="Path to the record JSONL file",
    )
    parser.add_argument(
        "--fs",
        action="store_true",
        help="Only analyze FS operations (default: both FS and VikingDB)",
    )
    parser.add_argument(
        "--vikingdb",
        action="store_true",
        help="Only analyze VikingDB operations (default: both FS and VikingDB)",
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
        "--output",
        type=str,
        default=None,
        help="Output file for results (JSON)",
    )
    parser.add_argument(
        "--quiet",
        action="store_true",
        help="Don't print detailed stats to console",
    )

    args = parser.parse_args()

    record_file = Path(args.record_file)
    if not record_file.exists():
        logger.error(f"Record file not found: {record_file}")
        return 1

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

    if not args.quiet:
        print_analysis_stats(stats)

    if args.output:
        with open(args.output, "w", encoding="utf-8") as f:
            json.dump(stats.to_dict(), f, indent=2, ensure_ascii=False)
        logger.info(f"Results saved to: {args.output}")

    return 0


if __name__ == "__main__":
    sys.exit(main())
