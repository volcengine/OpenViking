#!/usr/bin/env python3
"""Step 2 (Performance): Build vector indexes for imported data.

Submits async reindex tasks for each first-level subdirectory via
SyncHTTPClient.reindex(wait=False), with a concurrency limit of 2
running tasks.  When a task completes, the next one is submitted.
This avoids tree-lock conflicts and prevents resource exhaustion.

Prerequisites:
  1. Run step1_add_resource.py to import data (without indexing)
  2. Start openviking-server manually

Usage:
  python3 step2_reindex.py
"""

from __future__ import annotations

import argparse
import os
import time

from openviking_cli.client.sync_http import SyncHTTPClient

DEFAULT_SOURCE = os.path.expanduser("~/.openviking/data/benchmark/synthetic")
PROGRESS_FILE = os.path.expanduser("~/.openviking/data/benchmark/.perf-reindex-progress")
BENCHMARK_PARENT = "viking://resources/benchmark/performance"

POLL_INTERVAL = 5  # seconds between task status checks
MAX_CONCURRENT = 16  # max running tasks at a time


def load_progress() -> set[str]:
    if not os.path.exists(PROGRESS_FILE):
        return set()
    with open(PROGRESS_FILE) as f:
        return {line.strip() for line in f if line.strip()}


def save_progress(rel_dir: str) -> None:
    os.makedirs(os.path.dirname(PROGRESS_FILE), exist_ok=True)
    with open(PROGRESS_FILE, "a") as f:
        f.write(rel_dir + "\n")


def scan_first_level_dirs(root: str) -> list[str]:
    """Return sorted list of first-level subdirectory names."""
    try:
        entries = sorted(os.listdir(root))
    except OSError:
        return []
    return [e for e in entries if not e.startswith(".") and os.path.isdir(os.path.join(root, e))]


def main():
    parser = argparse.ArgumentParser(
        description="Step 2 (Performance): Build vector indexes via openviking-server"
    )
    parser.add_argument(
        "--source",
        default=DEFAULT_SOURCE,
        help=f"Local source directory (must match step1, default: {DEFAULT_SOURCE})",
    )
    parser.add_argument(
        "--parent",
        default=BENCHMARK_PARENT,
        help=f"Parent Viking URI (default: {BENCHMARK_PARENT})",
    )
    parser.add_argument(
        "--concurrency",
        type=int,
        default=MAX_CONCURRENT,
        help=f"Max concurrent reindex tasks (default: {MAX_CONCURRENT})",
    )
    args = parser.parse_args()

    source = os.path.expanduser(args.source)
    max_concurrent = max(1, args.concurrency)

    print("=" * 80)
    print("Step 2 (Performance): Build Vector Indexes (via openviking-server)")
    print("=" * 80)
    print(f"  Source:       {source}")
    print(f"  Parent:       {args.parent}")
    print(f"  Progress:     {PROGRESS_FILE}")
    print("  Mode:         vectors_only (wait=False, async)")
    print(f"  Concurrency:  {max_concurrent}")
    print()
    print("  Prerequisite: openviking-server must be running!")
    print()

    # Scan first-level dirs only
    first_level = scan_first_level_dirs(source)
    total = len(first_level)
    print(f"  First-level directories to reindex: {total}")
    print()

    if total == 0:
        print("No subdirectories found. Run step1_add_resource.py first.")
        return

    completed = load_progress()
    if completed:
        already_done = [d for d in first_level if d in completed]
        print(f"  Resuming: {len(already_done)} directories already reindexed")
        print()

    client = SyncHTTPClient()
    client.initialize()

    # Build work queue (skip already completed)
    work_queue: list[str] = [name for name in first_level if name not in completed]
    skipped_count = len(first_level) - len(work_queue)

    # running: task_id -> (name, submit_time)
    running: dict[str, tuple[str, float]] = {}
    results: list[dict] = []

    def _submit_next() -> bool:
        """Submit the next item from work_queue if slot available. Returns True if submitted."""
        if not work_queue or len(running) >= max_concurrent:
            return False
        name = work_queue.pop(0)
        dir_uri = f"{args.parent}/{name}"
        idx = total - len(work_queue)
        print(f"  [{idx}/{total}] Submitting: {name} ...", end="", flush=True)
        try:
            result = client.reindex(uri=dir_uri, mode="vectors_only", wait=False)
            task_id = result.get("task_id", "")
            if task_id:
                print(f" task_id={task_id[:8]}...")
                running[task_id] = (name, time.monotonic())
            else:
                print(" completed synchronously")
                save_progress(name)
                results.append({"dir": name, "status": "ok", "elapsed_s": 0.0})
            return True
        except Exception as e:
            print(f" FAILED: {e}")
            results.append({"dir": name, "status": "failed", "error": str(e)[:500]})
            return True

    # Fill initial slots
    while len(running) < max_concurrent and work_queue:
        _submit_next()

    if not running and not results:
        client.close()
        _print_summary(results, skipped_count, first_level)
        return

    # Poll loop: check running tasks, submit new ones as slots free up
    print()
    print(f"  Running {len(running)} tasks, {len(work_queue)} queued")
    print()

    while running:
        done_ids = []
        for task_id, (name, submit_time) in list(running.items()):
            try:
                task_info = client.get_task(task_id)
            except Exception:
                continue
            if task_info is None:
                continue
            status = task_info.get("status", "")
            if status in ("completed", "failed"):
                elapsed = time.monotonic() - submit_time
                if status == "completed":
                    print(f"    DONE  {name}  ({elapsed:.1f}s)")
                    save_progress(name)
                    results.append({"dir": name, "status": "ok", "elapsed_s": round(elapsed, 1)})
                else:
                    error = task_info.get("error", "unknown error")
                    print(f"    FAIL  {name}  ({elapsed:.1f}s): {error}")
                    results.append(
                        {
                            "dir": name,
                            "status": "failed",
                            "elapsed_s": round(elapsed, 1),
                            "error": error,
                        }
                    )
                done_ids.append(task_id)

        for tid in done_ids:
            del running[tid]

        # Fill freed slots
        while len(running) < max_concurrent and work_queue:
            _submit_next()

        if running:
            time.sleep(POLL_INTERVAL)

    client.close()
    _print_summary(results, skipped_count, first_level)


def _print_summary(results: list[dict], skipped_count: int, all_dirs: list[str]) -> None:
    print()
    print("Summary:")
    ok_count = sum(1 for r in results if r.get("status") == "ok")
    failed_count = sum(1 for r in results if r.get("status") == "failed")
    total_done = skipped_count + ok_count

    for r in results:
        status = r.get("status", "unknown")
        line = f"  {status.upper():>7s}  {r.get('dir', '?')}"
        if "elapsed_s" in r:
            line += f"  ({r['elapsed_s']}s)"
        if status == "failed":
            line += f"  -- {r.get('error', '')}"
        print(line)

    print()
    total = len(all_dirs)
    if total_done >= total and failed_count == 0:
        print(f"All {total} directories reindexed successfully.")
        print("Next step: run step3_benchmark.py to measure performance")
    else:
        print(
            f"  Reindexed: {ok_count}  Failed: {failed_count}  "
            f"Skipped: {skipped_count}  Remaining: {total - total_done}"
        )
        if failed_count > 0:
            print("Re-run this script to resume from where it left off.")


if __name__ == "__main__":
    main()
