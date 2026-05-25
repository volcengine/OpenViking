#!/usr/bin/env python3
"""Step 3: Build index — trigger VLM+embedding on already-uploaded files via CLI.

After step2_quick_add_resource.py uploads all files with build_index=False (skipping
VLM and embedding), this script calls `ov reindex` on each level3 directory to
trigger VLM summarization and embedding in-place, without re-uploading files.

Uses level3 directory granularity for progress tracking (8000 dirs, ~10 files each),
which gives fine-grained resume capability.

Usage:
  python3 step3_build_index.py [--no-resume] [--mode MODE] [--max-failures N]
"""

import argparse
import os
import shlex
import subprocess
import sys
import time

BASE_DIR = os.path.expanduser("~/.openviking/data/benchmark")
PROGRESS_FILE = os.path.join(BASE_DIR, ".build_index_progress")
BENCHMARK_URI = "viking://resources/benchmark"

# Tree structure from step1_generate.py
LEVEL0_DIRS = 10
LEVEL1_DIRS = 10
LEVEL2_DIRS = 10
LEVEL3_DIRS = 8


def discover_level3_dirs() -> list[str]:
    """Discover all level3 directories under BASE_DIR (deterministic order)."""
    dirs = []
    for i0 in range(LEVEL0_DIRS):
        d0 = os.path.join(BASE_DIR, f"level0_{i0:02d}")
        if not os.path.isdir(d0):
            continue
        for i1 in range(LEVEL1_DIRS):
            d1 = os.path.join(d0, f"level1_{i1:02d}")
            if not os.path.isdir(d1):
                continue
            for i2 in range(LEVEL2_DIRS):
                d2 = os.path.join(d1, f"level2_{i2:02d}")
                if not os.path.isdir(d2):
                    continue
                for i3 in range(LEVEL3_DIRS):
                    d3 = os.path.join(d2, f"level3_{i3:02d}")
                    if os.path.isdir(d3):
                        dirs.append(os.path.relpath(d3, BASE_DIR))
    return dirs


def load_progress() -> set:
    """Load set of already-indexed level3 relative paths from progress file."""
    done = set()
    if os.path.exists(PROGRESS_FILE):
        with open(PROGRESS_FILE) as f:
            for line in f:
                line = line.strip()
                if line:
                    done.add(line)
    return done


def save_progress(rel_path: str) -> None:
    """Append a completed level3 relative path to the progress file."""
    with open(PROGRESS_FILE, "a") as f:
        f.write(rel_path + "\n")
        f.flush()
        os.fsync(f.fileno())


def run_cmd(cmd: list[str]) -> tuple[int, str, str, float]:
    """Run command, return (returncode, stdout, stderr, elapsed_seconds)."""
    t0 = time.time()
    result = subprocess.run(cmd, capture_output=True, text=True, timeout=600)
    elapsed = time.time() - t0
    return result.returncode, result.stdout, result.stderr, elapsed


def main():
    parser = argparse.ArgumentParser(
        description="Step 3: Build index — trigger VLM+embedding via ov reindex"
    )
    parser.add_argument(
        "--no-resume", action="store_true", help="Disable auto-resume, start from scratch"
    )
    parser.add_argument(
        "--mode",
        choices=["vectors_only", "semantic_and_vectors"],
        default="vectors_only",
        help="Reindex mode (default: vectors_only = embedding)",
    )
    parser.add_argument(
        "--max-failures", type=int, default=50, help="Abort after N failures (default: 50)"
    )
    args = parser.parse_args()

    level3_dirs = discover_level3_dirs()

    if not level3_dirs:
        print(f"No level3 directories found under {BASE_DIR}")
        print("Did you run step1_generate.py and step2_quick_add_resource.py first?")
        sys.exit(1)

    # Load resume state
    done_set = set()
    if not args.no_resume:
        done_set = load_progress()
        if done_set:
            print(f"Resuming: {len(done_set)} dirs already indexed (from {PROGRESS_FILE})")

    count = 0
    skipped = 0
    failed = 0
    total = len(level3_dirs)
    print(f"{total} level3 dirs to index, {len(done_set)} already done")

    for rel_dir in level3_dirs:
        if rel_dir in done_set:
            skipped += 1
            continue

        uri = f"{BENCHMARK_URI}/{rel_dir}"
        cmd = [
            "ov",
            "reindex",
            "--account",
            "default",
            "--user",
            "default",
            "--mode",
            args.mode,
            "--wait",
            "true",
            uri,
        ]
        idx = count + skipped + 1
        cmd_str = shlex.join(cmd)
        print(f"[{idx}/{total}] $ {cmd_str}")

        try:
            rc, stdout, stderr, elapsed = run_cmd(cmd)

            if stdout.strip():
                for line in stdout.strip().splitlines():
                    print(f"  {line}")
            if stderr.strip():
                for line in stderr.strip().splitlines():
                    print(f"  [stderr] {line}")

            if rc != 0:
                print(f"  FAILED (exit={rc}, {elapsed:.1f}s)")
                failed += 1
            else:
                print(f"  OK ({elapsed:.1f}s)")
                save_progress(rel_dir)
        except subprocess.TimeoutExpired:
            print("  TIMEOUT (600s)")
            failed += 1
        except Exception as e:
            print(f"  ERROR: {e}")
            failed += 1

        if failed >= args.max_failures:
            print(f"\nToo many failures ({failed}), aborting. Re-run to resume.")
            sys.exit(1)

        count += 1
        if count % 100 == 0:
            print(f"  ... {count} dirs indexed this run ({failed} failed, {skipped} skipped)")

    print(f"\nDone! {count} dirs indexed, {skipped} skipped, {failed} failed")
    if failed == 0:
        print("Next step: run step4_benchmark.py to measure grep performance")


if __name__ == "__main__":
    main()
