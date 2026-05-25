#!/usr/bin/env python3
"""Step 4: Benchmark grep performance: pure fs vs vikingdb bm25 + fs.

Prerequisites:
  1. Run step1_generate.py to create test data
  2. Run step2_quick_add_resource.py to upload files (skip VLM+embedding)
  3. Run step3_build_index.py to build index (embedding)

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

# Test cases: (label, pattern, engine, extra_args)
# extra_args can override --uri; if present, the default --uri is omitted.
TEST_CASES = [
    # --- Single keyword ---
    ("fs: single keyword (VikingDB)", "VikingDB", "fs", []),
    ("bm25: single keyword (VikingDB)", "VikingDB", "auto", ["--switch-to-remote-threshold", "0"]),
    ("fs: single keyword (FullText)", "FullText", "fs", []),
    ("bm25: single keyword (FullText)", "FullText", "auto", ["--switch-to-remote-threshold", "0"]),
    # --- Multi-keyword (regex alternation) ---
    ("fs: 2 keywords (VikingDB|FullText)", "VikingDB|FullText", "fs", []),
    (
        "bm25: 2 keywords (VikingDB|FullText)",
        "VikingDB|FullText",
        "auto",
        ["--switch-to-remote-threshold", "0"],
    ),
    ("fs: 3 keywords (VikingDB|FullText|bm25)", "VikingDB|FullText|bm25", "fs", []),
    (
        "bm25: 3 keywords (VikingDB|FullText|bm25)",
        "VikingDB|FullText|bm25",
        "auto",
        ["--switch-to-remote-threshold", "0"],
    ),
    # --- Rare keyword (lower hit count) ---
    ("fs: rare keyword (search_by_keywords)", "search_by_keywords", "fs", []),
    (
        "bm25: rare keyword (search_by_keywords)",
        "search_by_keywords",
        "auto",
        ["--switch-to-remote-threshold", "0"],
    ),
    # --- Non-existent keyword (0 matches) ---
    ("fs: no-match 1 keyword (zzz_nonexistent)", "zzz_nonexistent", "fs", []),
    (
        "bm25: no-match 1 keyword (zzz_nonexistent)",
        "zzz_nonexistent",
        "auto",
        ["--switch-to-remote-threshold", "0"],
    ),
    ("fs: no-match 2 keywords (zzz_a|zzz_b)", "zzz_a|zzz_b", "fs", []),
    (
        "bm25: no-match 2 keywords (zzz_a|zzz_b)",
        "zzz_a|zzz_b",
        "auto",
        ["--switch-to-remote-threshold", "0"],
    ),
    ("fs: no-match 3 keywords (zzz_a|zzz_b|zzz_c)", "zzz_a|zzz_b|zzz_c", "fs", []),
    (
        "bm25: no-match 3 keywords (zzz_a|zzz_b|zzz_c)",
        "zzz_a|zzz_b|zzz_c",
        "auto",
        ["--switch-to-remote-threshold", "0"],
    ),
    # --- Subdirectory scope (~8K files per level0 dir) ---
    (
        "fs: subdir level0_00, VikingDB (~8K files)",
        "VikingDB",
        "fs",
        ["--uri", f"{BASE_URI}/level0_00"],
    ),
    (
        "bm25: subdir level0_00, VikingDB (~8K files)",
        "VikingDB",
        "auto",
        ["--uri", f"{BASE_URI}/level0_00", "--switch-to-remote-threshold", "0"],
    ),
    (
        "fs: subdir level0_00, no-match (~8K files)",
        "zzz_nonexistent",
        "fs",
        ["--uri", f"{BASE_URI}/level0_00"],
    ),
    (
        "bm25: subdir level0_00, no-match (~8K files)",
        "zzz_nonexistent",
        "auto",
        ["--uri", f"{BASE_URI}/level0_00", "--switch-to-remote-threshold", "0"],
    ),
]


def _has_uri_arg(extra_args: list) -> bool:
    """Check if extra_args contains --uri."""
    return "--uri" in extra_args


def run_grep(pattern: str, engine: str, extra_args: list) -> tuple[float, int, str, str]:
    """Run a single grep command, return (elapsed_seconds, match_count, stdout, stderr)."""
    cmd = OV_CMD + ["grep"]
    if not _has_uri_arg(extra_args):
        cmd += ["--uri", BASE_URI]
    cmd += ["--engine", engine] + extra_args + [pattern]

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

    print(f"{'Label':<50} {'Engine':<8} {'Avg(ms)':<10} {'Min(ms)':<10} {'Max(ms)':<10}")
    print("-" * 98)

    # Collect results for summary report: key = scenario name, value = {engine: (avg_ms, match_count)}
    results = {}

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

        avg_ms = 0.0
        if failed:
            print(f"{label:<50} {engine:<8} FAILED")
        elif not times:
            print(f"{label:<50} {engine:<8} NO DATA")
        else:
            avg_ms = sum(times) / len(times) * 1000
            min_ms = min(times) * 1000
            max_ms = max(times) * 1000
            print(f"{label:<50} {engine:<8} {avg_ms:<10.1f} {min_ms:<10.1f} {max_ms:<10.1f}")

        # Print output from last run (compact)
        if last_stdout.strip():
            for line in last_stdout.strip().splitlines()[:3]:
                print(f"    {line}")
            if len(last_stdout.strip().splitlines()) > 3:
                print(f"    ... ({len(last_stdout.strip().splitlines())} lines total)")
        if last_stderr.strip():
            for line in last_stderr.strip().splitlines()[:2]:
                print(f"    [stderr] {line}")

        # Store result for summary
        # Derive scenario name by stripping engine prefix: "fs: xxx" or "bm25: xxx" -> "xxx"
        scenario = label.split(": ", 1)[1].strip() if ": " in label else label.strip()
        if scenario not in results:
            results[scenario] = {}
        results[scenario][engine] = (avg_ms, match_count)

    # Print summary report
    print()
    print("=" * 80)
    print("PERFORMANCE REPORT: fs vs bm25 (auto)")
    print("=" * 80)
    print(f"{'Scenario':<45} {'fs(ms)':<12} {'auto(ms)':<12} {'Speedup':<10}")
    print("-" * 80)

    for scenario, engines in results.items():
        fs_data = engines.get("fs")
        auto_data = engines.get("auto")
        if fs_data and auto_data and fs_data[0] > 0:
            fs_ms = fs_data[0]
            auto_ms = auto_data[0]
            speedup = f"{fs_ms / auto_ms:.1f}x"
            print(f"{scenario:<45} {fs_ms:<12.1f} {auto_ms:<12.1f} {speedup:<10}")
        elif fs_data:
            fs_ms = fs_data[0]
            print(f"{scenario:<45} {fs_ms:<12.1f} {'N/A':<12} {'N/A':<10}")
        elif auto_data:
            auto_ms = auto_data[0]
            print(f"{scenario:<45} {'N/A':<12} {auto_ms:<12.1f} {'N/A':<10}")

    print()


if __name__ == "__main__":
    main()
