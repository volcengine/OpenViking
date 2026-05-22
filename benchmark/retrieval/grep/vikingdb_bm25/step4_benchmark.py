#!/usr/bin/env python3
"""Step 4: Benchmark grep performance: pure fs vs vikingdb bm25 + fs.

Prerequisites:
  1. Run step1_generate.py to create test data
  2. Run step2_quick_add_resource.py to upload files (skip VLM+embedding)
  3. Run step3_build_index.py to build index (embedding)

Expected results on 80K files (~4GB):
  - engine=fs:    tens to hundreds of seconds, frequent timeouts
  - engine=auto:  under 500ms per query (bm25 recall + local regex filter)

Usage:
  python3 step4_benchmark.py [--runs N] [--warmup N]

Outputs a comparison table of elapsed time and match count for each query.
"""
import argparse
import shlex
import subprocess
import sys
import time

BASE_URI = "viking://resources/benchmark"
OV_CMD = ["ov", "--account", "default", "--user", "default"]

# Test cases: (label, pattern, engine, extra_args)
# Each case is run with `ov grep --uri <URI> [extra_args] --engine <engine> <pattern>`
TEST_CASES = [
    # --- Single keyword, different engines ---
    ("fs: single keyword (VikingDB)", "VikingDB", "fs", []),
    ("bm25: single keyword (VikingDB)", "VikingDB", "auto", ["--switch-to-remote-threshold", "0"]),

    ("fs:  single keyword (FullText)", "FullText", "fs", []),
    ("bm25: single keyword (FullText)", "FullText", "auto", ["--switch-to-remote-threshold", "0"]),

    # --- Multi-keyword (regex alternation) ---
    ("fs:  2 keywords (VikingDB|FullText)", "VikingDB|FullText", "fs", []),
    ("bm25: 2 keywords (VikingDB|FullText)", "VikingDB|FullText", "auto", ["--switch-to-remote-threshold", "0"]),

    ("fs:  3 keywords (VikingDB|FullText|bm25)", "VikingDB|FullText|bm25", "fs", []),
    ("bm25: 3 keywords (VikingDB|FullText|bm25)", "VikingDB|FullText|bm25", "auto", ["--switch-to-remote-threshold", "0"]),

    # --- Rare keyword (lower hit count) ---
    ("fs:  rare keyword (search_by_keywords)", "search_by_keywords", "fs", []),
    ("bm25: rare keyword (search_by_keywords)", "search_by_keywords", "auto", ["--switch-to-remote-threshold", "0"]),

    # --- Non-existent keyword (0 matches) ---
    ("fs:  no-match keyword (zzz_nonexistent)", "zzz_nonexistent", "fs", []),
    ("bm25: no-match keyword (zzz_nonexistent)", "zzz_nonexistent", "auto", ["--switch-to-remote-threshold", "0"]),

    # --- Subdirectory scope (narrower URI, ~8K files) ---
    ("fs:  subdir scope (level0_00)", "VikingDB", "fs", ["--uri", f"{BASE_URI}/level0_00"]),
    ("bm25: subdir scope (level0_00)", "VikingDB", "auto", ["--switch-to-remote-threshold", "0", "--uri", f"{BASE_URI}/level0_00"]),

    # --- Different remote_return_limit ---
    ("bm25: return_limit=10", "VikingDB", "auto", ["--switch-to-remote-threshold", "0", "--remote-return-limit", "10"]),
    ("bm25: return_limit=1000", "VikingDB", "auto", ["--switch-to-remote-threshold", "0", "--remote-return-limit", "1000"]),
]


def run_grep(pattern: str, engine: str, extra_args: list) -> tuple[float, int, str, str]:
    """Run a single grep command, return (elapsed_seconds, match_count, stdout, stderr)."""
    cmd = OV_CMD + [
        "grep",
        "--uri", BASE_URI,
        "--engine", engine,
    ] + extra_args + [pattern]

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
    parser.add_argument("--runs", type=int, default=3, help="Number of runs per test case (default: 3)")
    parser.add_argument("--warmup", type=int, default=1, help="Warmup runs before measuring (default: 1)")
    args = parser.parse_args()

    print(f"{'Label':<50} {'Engine':<8} {'Avg(ms)':<10} {'Min(ms)':<10} {'Max(ms)':<10} {'Matches':<10}")
    print("-" * 108)

    for label, pattern, engine, extra_args in TEST_CASES:
        # Resolve URI from extra_args if overridden
        uri = BASE_URI
        for i, a in enumerate(extra_args):
            if a == "--uri" and i + 1 < len(extra_args):
                uri = extra_args[i + 1]

        # Warmup runs
        for _ in range(args.warmup):
            try:
                run_grep(pattern, engine, extra_args)
            except Exception:
                break

        # Measured runs
        times = []
        match_count = -1
        last_stdout = ""
        last_stderr = ""
        failed = False
        for _ in range(args.runs):
            try:
                elapsed, matches, stdout, stderr = run_grep(pattern, engine, extra_args)
                times.append(elapsed)
                match_count = matches
                last_stdout = stdout
                last_stderr = stderr
            except Exception:
                failed = True
                break

        if failed:
            print(f"{label:<50} {engine:<8} FAILED")
        elif not times:
            print(f"{label:<50} {engine:<8} NO DATA")
        else:
            avg_ms = sum(times) / len(times) * 1000
            min_ms = min(times) * 1000
            max_ms = max(times) * 1000
            print(f"{label:<50} {engine:<8} {avg_ms:<10.1f} {min_ms:<10.1f} {max_ms:<10.1f} {match_count:<10}")

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
