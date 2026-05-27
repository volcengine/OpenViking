#!/usr/bin/env python3
"""Step 4: Benchmark grep performance for the current engine config.

Prerequisites:
  1. Run step1_generate.py to create test data
  2. Run step2_quick_add_resource.py to upload files (skip VLM+embedding)
  3. Run step3_build_index.py to build index (embedding + content)
  4. Set ov.conf grep config and restart the server

NOTE: `engine` and `switch_to_remote_threshold` are server-side config
(ov.conf `grep` section). To benchmark different engines, update ov.conf
and restart the server before each run.

Usage:
  # Run 1: benchmark with fs engine
  #   1. Set ov.conf: "grep": {"engine": "fs"}
  #   2. Restart server
  python3 step4_benchmark.py --engine-label fs

  # Run 2: benchmark with auto engine (bm25)
  #   1. Set ov.conf: "grep": {"engine": "auto", "switch_to_remote_threshold": 0}
  #   2. Restart server
  python3 step4_benchmark.py --engine-label auto --compare step4_result_fs.json

Results are saved to step4_result_{engine_label}.json.
When --compare is given, a side-by-side comparison table is printed.
"""

import argparse
import json
import os
import subprocess
import time

BASE_URI = "viking://resources/benchmark"
OV_CMD = ["ov", "--account", "default", "--user", "default"]
RUNS = 3
WARMUP = 1

# Test cases: (label, pattern, uri)
TEST_CASES = [
    # --- Single keyword ---
    ("single keyword (VikingDB)", "VikingDB", BASE_URI),
    ("single keyword (FullText)", "FullText", BASE_URI),
    # --- Multi-keyword (regex alternation) ---
    ("2 keywords (VikingDB|FullText)", "VikingDB|FullText", BASE_URI),
    ("3 keywords (VikingDB|FullText|bm25)", "VikingDB|FullText|bm25", BASE_URI),
    # --- Rare keyword (lower hit count) ---
    ("rare keyword (search_by_keywords)", "search_by_keywords", BASE_URI),
    # --- Non-existent keyword (0 matches) ---
    ("no-match 1 keyword (zzz_nonexistent)", "zzz_nonexistent", BASE_URI),
    ("no-match 2 keywords (zzz_a|zzz_b)", "zzz_a|zzz_b", BASE_URI),
    ("no-match 3 keywords (zzz_a|zzz_b|zzz_c)", "zzz_a|zzz_b|zzz_c", BASE_URI),
    # --- Subdirectory scope (~8K files per level0 dir) ---
    ("subdir level0_00, VikingDB (~8K files)", "VikingDB", f"{BASE_URI}/level0_00"),
    ("subdir level0_00, no-match (~8K files)", "zzz_nonexistent", f"{BASE_URI}/level0_00"),
]


def run_grep(pattern: str, uri: str) -> tuple[float, int]:
    """Run a single grep command, return (elapsed_seconds, match_count)."""
    cmd = OV_CMD + ["--output", "json", "grep", "--uri", uri, "-n", "100000", pattern]

    start = time.monotonic()
    result = subprocess.run(cmd, capture_output=True, text=True, timeout=300)
    elapsed = time.monotonic() - start

    if result.returncode != 0:
        raise RuntimeError(
            f"ov grep failed (exit={result.returncode}): {result.stderr.strip()[:200]}"
        )

    # Find JSON line in stdout (skip echo_command output)
    json_line = None
    for line in result.stdout.strip().splitlines():
        line = line.strip()
        if line.startswith("{"):
            json_line = line
            break

    match_count = 0
    if json_line:
        try:
            resp = json.loads(json_line)
            grep_result = resp.get("result", resp)
            match_count = len(grep_result.get("matches", []))
        except json.JSONDecodeError:
            pass

    return elapsed, match_count


def benchmark_engine(engine_label: str) -> list[dict]:
    """Run all test cases for the current engine config."""
    results = []

    for label, pattern, uri in TEST_CASES:
        print(f"  {label} ...", end=" ", flush=True)

        # Warmup
        for _ in range(WARMUP):
            try:
                run_grep(pattern, uri)
            except Exception:
                pass

        # Measured runs
        times = []
        match_count = 0
        failed = False
        for _ in range(RUNS):
            try:
                elapsed, matches = run_grep(pattern, uri)
                times.append(elapsed)
                match_count = matches
            except Exception as e:
                failed = True
                print(f"FAILED ({e})")
                break

        if failed:
            results.append({"label": label, "pattern": pattern, "uri": uri, "error": True})
        else:
            avg_ms = sum(times) / len(times) * 1000
            min_ms = min(times) * 1000
            max_ms = max(times) * 1000
            print(f"avg={avg_ms:.1f}ms  min={min_ms:.1f}ms  matches={match_count}")
            results.append(
                {
                    "label": label,
                    "pattern": pattern,
                    "uri": uri,
                    "avg_ms": round(avg_ms, 1),
                    "min_ms": round(min_ms, 1),
                    "max_ms": round(max_ms, 1),
                    "matches": match_count,
                }
            )

    return results


def print_comparison(
    current_label: str, current: list[dict], compare_label: str, compare: list[dict]
):
    """Print side-by-side comparison table."""
    # Build lookup by label
    compare_by_label = {}
    for r in compare:
        if "error" not in r:
            compare_by_label[r["label"]] = r

    print()
    print("=" * 110)
    print(f"  Comparison: {compare_label} vs {current_label}")
    print("=" * 110)
    print(
        f"{'Label':<50} {compare_label + '(ms)':>12} {current_label + '(ms)':>12} {'speedup':>10}"
    )
    print("-" * 110)

    for r in current:
        label = r["label"]
        if "error" in r:
            print(f"{label:<50} {'ERR':>12} {'ERR':>12} {'---':>10}")
            continue
        cur_ms = r["avg_ms"]
        cmp = compare_by_label.get(label)
        if not cmp:
            print(f"{label:<50} {'N/A':>12} {cur_ms:>12.1f} {'---':>10}")
            continue
        cmp_ms = cmp["avg_ms"]
        if cur_ms > 0:
            speedup = cmp_ms / cur_ms
            speedup_str = f"{speedup:.1f}x"
        else:
            speedup_str = "inf"
        print(f"{label:<50} {cmp_ms:>12.1f} {cur_ms:>12.1f} {speedup_str:>10}")

    print()


def main():
    parser = argparse.ArgumentParser(description="Benchmark grep performance")
    parser.add_argument(
        "--engine-label",
        required=True,
        help="Label for this engine config (e.g. fs, auto). Used in output filename.",
    )
    parser.add_argument(
        "--compare",
        default=None,
        help="Path to a previous step4_result_*.json file for side-by-side comparison",
    )
    args = parser.parse_args()

    print("=" * 80)
    print(f"Step 4: Grep Performance Benchmark — engine={args.engine_label}")
    print("=" * 80)
    print()
    print("Ensure ov.conf has the desired grep config and the server is restarted.")
    print()

    # Run benchmark
    results = benchmark_engine(args.engine_label)

    # Save results
    output_file = f"step4_result_{args.engine_label}.json"
    with open(output_file, "w") as f:
        json.dump({"engine_label": args.engine_label, "results": results}, f, indent=2)
    print(f"\nResults saved to {output_file}")

    # Print current results table
    print()
    print(f"{'Label':<50} {'Avg(ms)':>10} {'Min(ms)':>10} {'Max(ms)':>10} {'Matches':>10}")
    print("-" * 95)
    for r in results:
        if "error" in r:
            print(f"{r['label']:<50} {'FAILED':>10}")
        else:
            print(
                f"{r['label']:<50} {r['avg_ms']:>10.1f} {r['min_ms']:>10.1f} "
                f"{r['max_ms']:>10.1f} {r['matches']:>10}"
            )
    print()

    # Compare with previous results
    if args.compare:
        if not os.path.isfile(args.compare):
            print(f"Warning: compare file not found: {args.compare}")
        else:
            with open(args.compare) as f:
                prev = json.load(f)
            prev_label = prev.get("engine_label", "previous")
            prev_results = prev.get("results", [])
            print_comparison(args.engine_label, results, prev_label, prev_results)


if __name__ == "__main__":
    main()
