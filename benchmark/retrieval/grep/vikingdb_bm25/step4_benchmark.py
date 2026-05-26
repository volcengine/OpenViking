#!/usr/bin/env python3
"""Step 4: Benchmark grep performance: pure fs vs vikingdb bm25 + fs.

Prerequisites:
  1. Run step1_generate.py to create test data
  2. Run step2_quick_add_resource.py to upload files (skip VLM+embedding)
  3. Run step3_build_index.py to build index (embedding)

NOTE: `engine` and `switch_to_remote_threshold` are now server-side config
(ov.conf `[grep]` section).  To benchmark different engines, update ov.conf
and restart the server before each run.  The default config uses engine="auto"
with switch_to_remote_threshold=1000; set switch_to_remote_threshold=0 to
force VikingDB bm25 recall.

Usage:
  python3 step4_benchmark.py [--runs N] [--warmup N]

Outputs a comparison table of elapsed time and match count for each query.
"""

import argparse
import shlex
import subprocess
import time

BASE_URI = "viking://resources/benchmark"
OV_CMD = ["ov", "--account", "default", "--user", "default"]

# Test cases: (label, pattern, extra_args)
# extra_args can override --uri; if present, the default --uri is omitted.
TEST_CASES = [
    # --- Single keyword ---
    ("single keyword (VikingDB)", "VikingDB", []),
    ("single keyword (FullText)", "FullText", []),
    # --- Multi-keyword (regex alternation) ---
    ("2 keywords (VikingDB|FullText)", "VikingDB|FullText", []),
    ("3 keywords (VikingDB|FullText|bm25)", "VikingDB|FullText|bm25", []),
    # --- Rare keyword (lower hit count) ---
    ("rare keyword (search_by_keywords)", "search_by_keywords", []),
    # --- Non-existent keyword (0 matches) ---
    ("no-match 1 keyword (zzz_nonexistent)", "zzz_nonexistent", []),
    ("no-match 2 keywords (zzz_a|zzz_b)", "zzz_a|zzz_b", []),
    ("no-match 3 keywords (zzz_a|zzz_b|zzz_c)", "zzz_a|zzz_b|zzz_c", []),
    # --- Subdirectory scope (~8K files per level0 dir) ---
    ("subdir level0_00, VikingDB (~8K files)", "VikingDB", ["--uri", f"{BASE_URI}/level0_00"]),
    (
        "subdir level0_00, no-match (~8K files)",
        "zzz_nonexistent",
        ["--uri", f"{BASE_URI}/level0_00"],
    ),
]


def _has_uri_arg(extra_args: list) -> bool:
    """Check if extra_args contains --uri."""
    return "--uri" in extra_args


def run_grep(pattern: str, extra_args: list) -> tuple[float, int, str, str]:
    """Run a single grep command, return (elapsed_seconds, match_count, stdout, stderr)."""
    cmd = OV_CMD + ["grep"]
    if not _has_uri_arg(extra_args):
        cmd += ["--uri", BASE_URI]
    cmd += extra_args + [pattern]

    cmd_str = shlex.join(cmd)
    print(f"  $ {cmd_str}")

    start = time.monotonic()
    result = subprocess.run(cmd, capture_output=True, text=True)
    elapsed = time.monotonic() - start

    match_count = 0
    if result.stdout:
        match_count = len([l for l in result.stdout.strip().splitlines() if l.strip()])

    return elapsed, match_count, result.stdout, result.stderr


def main():
    parser = argparse.ArgumentParser(description="Benchmark grep: fs vs bm25")
    parser.add_argument(
        "--runs", type=int, default=3, help="Number of runs per test case (default: 3)"
    )
    parser.add_argument(
        "--warmup", type=int, default=1, help="Warmup runs before measuring (default: 1)"
    )
    args = parser.parse_args()

    print(f"{'Label':<50} {'Avg(ms)':<10} {'Min(ms)':<10} {'Max(ms)':<10}")
    print("-" * 88)

    for label, pattern, extra_args in TEST_CASES:
        # Warmup runs
        for _ in range(args.warmup):
            try:
                run_grep(pattern, extra_args)
            except Exception:
                break

        # Measured runs
        times = []
        last_stdout = ""
        last_stderr = ""
        failed = False
        for _ in range(args.runs):
            try:
                elapsed, matches, stdout, stderr = run_grep(pattern, extra_args)
                times.append(elapsed)
                last_stdout = stdout
                last_stderr = stderr
            except Exception:
                failed = True
                break

        if failed:
            print(f"{label:<50} FAILED")
        elif not times:
            print(f"{label:<50} NO DATA")
        else:
            avg_ms = sum(times) / len(times) * 1000
            min_ms = min(times) * 1000
            max_ms = max(times) * 1000
            print(f"{label:<50} {avg_ms:<10.1f} {min_ms:<10.1f} {max_ms:<10.1f}")

        # Print output from last run (compact)
        if last_stdout.strip():
            for line in last_stdout.strip().splitlines()[:3]:
                print(f"    {line}")
            if len(last_stdout.strip().splitlines()) > 3:
                print(f"    ... ({len(last_stdout.strip().splitlines())} lines total)")
        if last_stderr.strip():
            for line in last_stderr.strip().splitlines()[:2]:
                print(f"    [stderr] {line}")

    print()


if __name__ == "__main__":
    main()
