#!/usr/bin/env python3
"""Step 1 (Effectiveness): Import real code repos into OpenViking (with indexing).

Imports the entire source directory as a single resource via
SyncOpenViking.add_resource (wait=True, build_index=True, summarize=True).
add_resource handles recursive traversal internally.

After import, run step2_quality.py to evaluate retrieval quality.

Prerequisites:
  - Download code repos and place them under the source directory manually.

Usage:
  python3 step1_add_resource.py
  python3 step1_add_resource.py --source ~/.openviking/data/benchmark/OpenViking-main
"""

from __future__ import annotations

import argparse
import os
import time

from openviking.sync_client import SyncOpenViking

DEFAULT_SOURCE = os.path.expanduser("~/.openviking/data/benchmark/OpenViking-main")
BENCHMARK_PARENT = "viking://resources/benchmark/effectiveness"


def main():
    parser = argparse.ArgumentParser(
        description="Step 1 (Effectiveness): Import real code repos (with indexing)"
    )
    parser.add_argument(
        "--source",
        default=DEFAULT_SOURCE,
        help=f"Local directory to import (default: {DEFAULT_SOURCE})",
    )
    parser.add_argument(
        "--parent",
        default=BENCHMARK_PARENT,
        help=f"Parent Viking URI (default: {BENCHMARK_PARENT})",
    )
    args = parser.parse_args()

    source = os.path.expanduser(args.source)
    if not os.path.isdir(source):
        print(f"ERROR: Source directory does not exist: {source}")
        return

    print("=" * 80)
    print("Step 1 (Effectiveness): Import Code Repos (with VLM/embedding)")
    print("=" * 80)
    print(f"  Source:   {source}")
    print(f"  Parent:   {args.parent}")
    print("  Indexing: ENABLED (build_index=True, summarize=True)")
    print()

    client = SyncOpenViking()
    client.initialize()

    t0 = time.monotonic()
    try:
        result = client.add_resource(
            path=source,
            parent=args.parent,
            reason="benchmark effectiveness",
            wait=True,
            create_parent=True,
            build_index=True,
            summarize=True,
        )
        elapsed = time.monotonic() - t0
        root_uri = result.get("root_uri", "?")
        print(f"OK ({elapsed:.1f}s) -> {root_uri}")
        print()
        print("Import completed successfully.")
        print("Next step: run step2_quality.py to evaluate retrieval quality")
    except Exception as e:
        elapsed = time.monotonic() - t0
        print(f"FAILED ({elapsed:.1f}s): {e}")

    client.close()


if __name__ == "__main__":
    main()
