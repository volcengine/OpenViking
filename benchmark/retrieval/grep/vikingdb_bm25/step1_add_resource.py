#!/usr/bin/env python3
"""Step 1: Import local code directories as benchmark data via OpenViking SDK.

Recursively scans a local directory, imports each subdirectory (at all depths)
separately via SyncOpenViking.add_resource (wait=True), and saves progress
after each directory for resumability. Directory order is deterministic
(sorted at each level).

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
PROGRESS_FILE = os.path.expanduser("~/.openviking/data/benchmark/.code-import-progress")
BENCHMARK_PARENT = "viking://resources/benchmark"


def load_progress() -> set[str]:
    """Load completed directory names from progress file."""
    if not os.path.exists(PROGRESS_FILE):
        return set()
    with open(PROGRESS_FILE) as f:
        return {line.strip() for line in f if line.strip()}


def save_progress(dir_name: str) -> None:
    """Append a completed directory name to progress file."""
    os.makedirs(os.path.dirname(PROGRESS_FILE), exist_ok=True)
    with open(PROGRESS_FILE, "a") as f:
        f.write(dir_name + "\n")


def scan_subdirs_recursive(root: str) -> list[str]:
    """Return sorted list of all subdirectory relative paths under root (recursive, deterministic order).

    Skips hidden directories (starting with '.'). Order is deterministic:
    sorted at each level, parent before children.
    """
    result: list[str] = []

    def _walk(dir_path: str, rel_prefix: str) -> None:
        try:
            entries = sorted(os.listdir(dir_path))
        except OSError:
            return
        for name in entries:
            if name.startswith("."):
                continue
            full = os.path.join(dir_path, name)
            if not os.path.isdir(full):
                continue
            rel = f"{rel_prefix}/{name}" if rel_prefix else name
            result.append(rel)
            _walk(full, rel)

    _walk(root, "")
    return result


def main():
    parser = argparse.ArgumentParser(
        description="Step 1: Import local code directories as benchmark data via SDK"
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
    print("Step 1: Import Local Code Directories as Benchmark Data")
    print("=" * 80)
    print(f"  Source:   {source}")
    print(f"  Parent:   {args.parent}")
    print(f"  Progress: {PROGRESS_FILE}")
    print()

    # Scan subdirectories recursively
    subdirs = scan_subdirs_recursive(source)
    total = len(subdirs)
    print(f"  Total directories to import: {total}")
    print()

    if total == 0:
        print("No subdirectories found. Nothing to import.")
        return

    # Load progress
    completed = load_progress()
    if completed:
        already_done = [d for d in subdirs if d in completed]
        print(f"  Resuming: {len(already_done)} directories already imported")
        print()

    client = SyncOpenViking()
    client.initialize()

    results = []
    for i, rel_dir in enumerate(subdirs, 1):
        if rel_dir in completed:
            print(f"  [{i}/{total}] SKIP (already done): {rel_dir}")
            continue

        dir_path = os.path.join(source, rel_dir)
        # Build parent URI: viking://resources/benchmark/<parent_rel_path>
        parent_rel = os.path.dirname(rel_dir)
        parent_uri = f"{args.parent}/{parent_rel}" if parent_rel else args.parent
        print(f"  [{i}/{total}] Importing: {rel_dir} ...", end="", flush=True)

        t0 = time.monotonic()
        try:
            result = client.add_resource(
                path=dir_path,
                parent=parent_uri,
                reason=f"benchmark data: {rel_dir}",
                wait=True,
                create_parent=True,
            )
            elapsed = time.monotonic() - t0
            root_uri = result.get("root_uri", "?")
            print(f" OK ({elapsed:.1f}s) -> {root_uri}")
            save_progress(rel_dir)
            results.append({"dir": rel_dir, "status": "ok", "elapsed_s": round(elapsed, 1)})
        except Exception as e:
            elapsed = time.monotonic() - t0
            print(f" FAILED ({elapsed:.1f}s): {e}")
            results.append(
                {
                    "dir": rel_dir,
                    "status": "failed",
                    "elapsed_s": round(elapsed, 1),
                    "error": str(e)[:500],
                }
            )

    client.close()

    # Summary
    print()
    print("Summary:")
    for r in results:
        status = r["status"]
        dir_name = r["dir"]
        elapsed = r["elapsed_s"]
        line = f"  {status.upper():>7s}  {dir_name}  ({elapsed}s)"
        if status == "failed":
            line += f"  -- {r.get('error', '')}"
        print(line)

    ok_count = sum(1 for r in results if r["status"] == "ok")
    failed_count = sum(1 for r in results if r["status"] == "failed")
    skipped_count = sum(1 for d in subdirs if d in completed)
    total_done = skipped_count + ok_count

    print()
    if total_done >= total and failed_count == 0:
        print(f"All {total} directories imported and processed successfully.")
        print("Next step: run step2_benchmark.py to measure grep performance")
    else:
        print(
            f"  Imported: {ok_count}  Failed: {failed_count}  Skipped: {skipped_count}  Remaining: {total - total_done}"
        )
        if failed_count > 0:
            print("Re-run this script to resume from where it left off.")


if __name__ == "__main__":
    main()
