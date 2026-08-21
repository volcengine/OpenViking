#!/usr/bin/env python3
"""Import real code repos through the Python HTTP SDK with indexing enabled."""

from __future__ import annotations

import argparse
import os
import time

from openviking_sdk import OpenVikingError, SyncHTTPClient

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
    print("  Processing: semantic_and_vectors")
    print()

    client = SyncHTTPClient()
    client.initialize()

    t0 = time.monotonic()
    try:
        try:
            client.mkdir(uri=args.parent)
        except OpenVikingError as exc:
            if exc.code != "ALREADY_EXISTS":
                raise
        result = client.add_resource(
            path=source,
            parent=args.parent,
            wait=True,
            options={
                "reason": "benchmark effectiveness",
                "processing_mode": "semantic_and_vectors",
            },
        )
        elapsed = time.monotonic() - t0
        root_uri = result.get("root_uri", "?")
        print(f"OK ({elapsed:.1f}s) -> {root_uri}")
        print()
        print("Import completed successfully.")
        print("Next step: run step2_quality.py to evaluate retrieval quality")
    except Exception as exc:
        elapsed = time.monotonic() - t0
        print(f"FAILED ({elapsed:.1f}s): {exc}")
    finally:
        client.close()


if __name__ == "__main__":
    main()
