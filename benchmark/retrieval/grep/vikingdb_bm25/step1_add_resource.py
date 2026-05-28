#!/usr/bin/env python3
"""Step 1: Import GitHub repositories as benchmark data via OpenViking SDK.

Imports one or more GitHub repositories into OpenViking using the Python SDK
(SyncOpenViking) to avoid HTTP timeout issues with the CLI. Resources are
added with wait=True so VLM summarization and embedding are completed
before the script returns.

Usage:
  python3 step1_add_resource.py
  python3 step1_add_resource.py --repos "https://github.com/volcengine/OpenViking"
  python3 step1_add_resource.py --repos "https://github.com/volcengine/OpenViking" "https://github.com/another/repo"
"""

from __future__ import annotations

import argparse
import time

from openviking.sync_client import SyncOpenViking

DEFAULT_REPOS = [
    "https://github.com/volcengine/OpenViking",
]

BENCHMARK_PARENT = "viking://resources/benchmark"


def main():
    parser = argparse.ArgumentParser(
        description="Step 1: Import GitHub repos as benchmark data via SDK"
    )
    parser.add_argument(
        "--repos",
        nargs="+",
        default=DEFAULT_REPOS,
        help="GitHub repo URLs to import (default: OpenViking repo)",
    )
    parser.add_argument(
        "--parent",
        default=BENCHMARK_PARENT,
        help=f"Parent Viking URI (default: {BENCHMARK_PARENT})",
    )
    args = parser.parse_args()

    print("=" * 80)
    print("Step 1: Import GitHub Repositories as Benchmark Data")
    print("=" * 80)
    print(f"  Repos:  {args.repos}")
    print(f"  Parent: {args.parent}")
    print()

    client = SyncOpenViking()
    client.initialize()

    results = []
    for repo_url in args.repos:
        repo_name = repo_url.rstrip("/").split("/")[-1]
        print(f"--- Importing {repo_name} ---")

        t0 = time.monotonic()
        try:
            result = client.add_resource(
                path=repo_url,
                parent=args.parent,
                reason=f"benchmark data: {repo_name}",
                wait=True,
                create_parent=True,
            )
            elapsed = time.monotonic() - t0
            root_uri = result.get("root_uri", "?")
            print(f"  OK ({elapsed:.1f}s) -> {root_uri}")
            results.append(
                {
                    "repo": repo_url,
                    "status": "ok",
                    "elapsed_s": round(elapsed, 1),
                    "root_uri": root_uri,
                }
            )
        except Exception as e:
            elapsed = time.monotonic() - t0
            print(f"  FAILED ({elapsed:.1f}s): {e}")
            results.append(
                {
                    "repo": repo_url,
                    "status": "failed",
                    "elapsed_s": round(elapsed, 1),
                    "error": str(e)[:500],
                }
            )

    client.close()

    print()
    print("Summary:")
    for r in results:
        status = r["status"]
        repo = r["repo"]
        elapsed = r["elapsed_s"]
        print(f"  {status.upper():>7s}  {repo}  ({elapsed}s)")

    ok_count = sum(1 for r in results if r["status"] == "ok")
    if ok_count == len(results):
        print(f"\nAll {ok_count} repos imported and processed successfully.")
        print("Next step: run step2_benchmark.py to measure grep performance")
    else:
        print(f"\n{ok_count}/{len(results)} repos imported successfully. Check errors above.")


if __name__ == "__main__":
    main()
