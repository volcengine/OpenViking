#!/usr/bin/env python3
"""Step 1 (Performance): Import synthetic data into OpenViking WITHOUT indexing.

Imports each directory recursively via SyncOpenViking.add_resource with
build_index=False and summarize=False, to skip slow VLM/embedding steps.
Progress is saved after each directory for resumability.

After all imports are done, run step2_reindex.py to build vector indexes,
then step3_benchmark.py to measure performance.

Usage:
  python3 step1_add_resource.py
  python3 step1_add_resource.py --source ~/.openviking/data/benchmark/synthetic
"""

from __future__ import annotations

import argparse
import os
import time

from openviking.sync_client import SyncOpenViking

DEFAULT_SOURCE = os.path.expanduser("~/.openviking/data/benchmark/synthetic")
PROGRESS_FILE = os.path.expanduser("~/.openviking/data/benchmark/.perf-import-progress")
BENCHMARK_PARENT = "viking://resources/benchmark/performance"


def load_progress() -> set[str]:
    if not os.path.exists(PROGRESS_FILE):
        return set()
    with open(PROGRESS_FILE) as f:
        return {line.strip() for line in f if line.strip()}


def save_progress(rel_dir: str) -> None:
    os.makedirs(os.path.dirname(PROGRESS_FILE), exist_ok=True)
    with open(PROGRESS_FILE, "a") as f:
        f.write(rel_dir + "\n")


def scan_subdirs_recursive(root: str) -> list[str]:
    """Return sorted list of all subdirectory relative paths (deterministic order)."""
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
        description="Step 1 (Performance): Import synthetic data (no indexing)"
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
    print("Step 1 (Performance): Import Synthetic Data (no VLM/embedding)")
    print("=" * 80)
    print(f"  Source:   {source}")
    print(f"  Parent:   {args.parent}")
    print(f"  Progress: {PROGRESS_FILE}")
    print("  Indexing: DISABLED (build_index=False, summarize=False)")
    print()

    subdirs = scan_subdirs_recursive(source)
    total = len(subdirs)
    print(f"  Total directories to import: {total}")
    print()

    if total == 0:
        print("No subdirectories found. Nothing to import.")
        return

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
        parent_rel = os.path.dirname(rel_dir)
        parent_uri = f"{args.parent}/{parent_rel}" if parent_rel else args.parent
        print(f"  [{i}/{total}] Importing: {rel_dir} ...", end="", flush=True)

        t0 = time.monotonic()
        try:
            result = client.add_resource(
                path=dir_path,
                parent=parent_uri,
                reason=f"benchmark perf: {rel_dir}",
                wait=True,
                create_parent=True,
                build_index=False,
                summarize=False,
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

    print()
    print("Summary:")
    ok_count = sum(1 for r in results if r["status"] == "ok")
    failed_count = sum(1 for r in results if r["status"] == "failed")
    skipped_count = sum(1 for d in subdirs if d in completed)
    total_done = skipped_count + ok_count

    for r in results:
        status = r["status"]
        line = f"  {status.upper():>7s}  {r['dir']}  ({r['elapsed_s']}s)"
        if status == "failed":
            line += f"  -- {r.get('error', '')}"
        print(line)

    print()
    if total_done >= total and failed_count == 0:
        print(f"All {total} directories imported successfully (no indexing).")
        print("Next step: run step2_reindex.py to build vector indexes")
    else:
        print(
            f"  Imported: {ok_count}  Failed: {failed_count}  "
            f"Skipped: {skipped_count}  Remaining: {total - total_done}"
        )
        if failed_count > 0:
            print("Re-run this script to resume from where it left off.")


if __name__ == "__main__":
    main()
