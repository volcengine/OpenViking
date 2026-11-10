#!/usr/bin/env python3
"""Import synthetic data through the Python HTTP SDK without VLM processing."""

from __future__ import annotations

import argparse
import os
import time

from openviking_sdk import OpenVikingError, SyncHTTPClient

DEFAULT_SOURCE = os.path.expanduser("~/.openviking/data/benchmark/synthetic")
PROGRESS_FILE = os.path.expanduser("~/.openviking/data/benchmark/.perf-import-progress")
BENCHMARK_PARENT = "viking://resources/benchmark/performance"


def load_progress() -> set[str]:
    if not os.path.exists(PROGRESS_FILE):
        return set()
    with open(PROGRESS_FILE) as file:
        return {line.strip() for line in file if line.strip()}


def save_progress(rel_dir: str) -> None:
    os.makedirs(os.path.dirname(PROGRESS_FILE), exist_ok=True)
    with open(PROGRESS_FILE, "a") as file:
        file.write(rel_dir + "\n")


def scan_subdirs_recursive(root: str) -> list[str]:
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
        description="Step 1 (Performance): Import synthetic data (vectors only)"
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
    print("Step 1 (Performance): Import Synthetic Data (vectors only, no VLM)")
    print("=" * 80)
    print(f"  Source:   {source}")
    print(f"  Parent:   {args.parent}")
    print(f"  Progress: {PROGRESS_FILE}")
    print("  Processing: vectors_only (VLM semantic processing disabled)")
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
        already_done = [directory for directory in subdirs if directory in completed]
        print(f"  Resuming: {len(already_done)} directories already imported")
        print()

    client = SyncHTTPClient()
    client.initialize()

    results = []
    try:
        for index, rel_dir in enumerate(subdirs, 1):
            if rel_dir in completed:
                print(f"  [{index}/{total}] SKIP (already done): {rel_dir}")
                continue

            dir_path = os.path.join(source, rel_dir)
            parent_rel = os.path.dirname(rel_dir)
            parent_uri = f"{args.parent}/{parent_rel}" if parent_rel else args.parent
            print(f"  [{index}/{total}] Importing: {rel_dir} ...", end="", flush=True)

            t0 = time.monotonic()
            try:
                try:
                    client.mkdir(uri=parent_uri)
                except OpenVikingError as exc:
                    if exc.code != "ALREADY_EXISTS":
                        raise
                result = client.add_resource(
                    path=dir_path,
                    parent=parent_uri,
                    wait=True,
                    options={
                        "reason": f"benchmark perf: {rel_dir}",
                        "processing_mode": "vectors_only",
                    },
                )
                elapsed = time.monotonic() - t0
                root_uri = result.get("root_uri", "?")
                print(f" OK ({elapsed:.1f}s) -> {root_uri}")
                save_progress(rel_dir)
                results.append({"dir": rel_dir, "status": "ok", "elapsed_s": round(elapsed, 1)})
            except Exception as exc:
                elapsed = time.monotonic() - t0
                print(f" FAILED ({elapsed:.1f}s): {exc}")
                results.append(
                    {
                        "dir": rel_dir,
                        "status": "failed",
                        "elapsed_s": round(elapsed, 1),
                        "error": str(exc)[:500],
                    }
                )
    finally:
        client.close()

    print()
    print("Summary:")
    ok_count = sum(1 for result in results if result["status"] == "ok")
    failed_count = sum(1 for result in results if result["status"] == "failed")
    skipped_count = sum(1 for directory in subdirs if directory in completed)
    total_done = skipped_count + ok_count

    for result in results:
        status = result["status"]
        line = f"  {status.upper():>7s}  {result['dir']}  ({result['elapsed_s']}s)"
        if status == "failed":
            line += f"  -- {result.get('error', '')}"
        print(line)

    print()
    if total_done >= total and failed_count == 0:
        print(f"All {total} directories imported successfully (vectors only, no VLM).")
        print("Next step: optionally run step2_reindex.py, then run step3_benchmark.py")
    else:
        print(
            f"  Imported: {ok_count}  Failed: {failed_count}  "
            f"Skipped: {skipped_count}  Remaining: {total - total_done}"
        )
        if failed_count > 0:
            print("Re-run this script to resume from where it left off.")


if __name__ == "__main__":
    main()
