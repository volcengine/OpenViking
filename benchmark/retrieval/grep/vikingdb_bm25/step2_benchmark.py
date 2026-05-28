#!/usr/bin/env python3
"""Step 2: Benchmark grep performance for the current engine config.

Prerequisites:
  1. Run step1_add_resource.py to import repos (includes VLM+embedding)
  2. Set ov.conf grep config and restart the server

NOTE: `engine` and `switch_to_remote_threshold` are server-side config
(ov.conf `grep` section). To benchmark different engines, update ov.conf
and restart the server before each run.

KEYWORDS: Fill the KEYWORDS list below with real terms from the imported
repos. Each keyword will be tested individually, plus multi-keyword regex
and no-match scenarios.

Usage:
  # Run 1: benchmark with fs engine
  #   1. Set ov.conf: "grep": {"engine": "fs"}
  #   2. Restart server
  python3 step2_benchmark.py --engine-label fs

  # Run 2: benchmark with auto engine (bm25)
  #   1. Set ov.conf: "grep": {"engine": "auto", "switch_to_remote_threshold": 0}
  #   2. Restart server
  python3 step2_benchmark.py --engine-label auto --compare step2_result_fs.json

Results are saved to step2_result_{engine_label}.json.
When --compare is given, a side-by-side comparison table is printed.
"""

from __future__ import annotations

import argparse
import json
import os
import time

from openviking.sync_client import SyncOpenViking

BASE_URI = "viking://resources/benchmark"
RUNS = 3
WARMUP = 1

KEYWORDS: list[str] = []


def build_test_cases() -> list[tuple[str, str, str]]:
    cases = []

    for kw in KEYWORDS:
        cases.append((f"keyword: {kw}", kw, BASE_URI))

    if len(KEYWORDS) >= 2:
        cases.append(
            (f"multi 2: {KEYWORDS[0]}|{KEYWORDS[1]}", f"{KEYWORDS[0]}|{KEYWORDS[1]}", BASE_URI)
        )
    if len(KEYWORDS) >= 3:
        cases.append(
            (
                f"multi 3: {KEYWORDS[0]}|{KEYWORDS[1]}|{KEYWORDS[2]}",
                f"{KEYWORDS[0]}|{KEYWORDS[1]}|{KEYWORDS[2]}",
                BASE_URI,
            )
        )

    cases.append(("no-match: zzz_nonexistent_benchmark", "zzz_nonexistent_benchmark", BASE_URI))
    cases.append(("no-match 2: zzz_a|zzz_b", "zzz_a|zzz_b", BASE_URI))

    return cases


def run_grep(client: SyncOpenViking, pattern: str, uri: str) -> tuple[float, int]:
    start = time.monotonic()
    result = client.grep(uri=uri, pattern=pattern, node_limit=100000)
    elapsed = time.monotonic() - start

    match_count = 0
    if isinstance(result, dict):
        matches = result.get("matches", [])
        match_count = len(matches)

    return elapsed, match_count


def benchmark_engine(client: SyncOpenViking, engine_label: str) -> list[dict]:
    test_cases = build_test_cases()
    results = []

    for label, pattern, uri in test_cases:
        print(f"  {label} ...", end=" ", flush=True)

        for _ in range(WARMUP):
            try:
                run_grep(client, pattern, uri)
            except Exception:
                pass

        times = []
        match_count = 0
        failed = False
        for _ in range(RUNS):
            try:
                elapsed, matches = run_grep(client, pattern, uri)
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
        help="Path to a previous step2_result_*.json file for side-by-side comparison",
    )
    args = parser.parse_args()

    if not KEYWORDS:
        print("WARNING: KEYWORDS list is empty. Fill it with real terms before running.")
        print("         Edit step2_benchmark.py and add keywords to the KEYWORDS list.\n")

    print("=" * 80)
    print(f"Step 2: Grep Performance Benchmark — engine={args.engine_label}")
    print("=" * 80)
    print()
    print("Ensure ov.conf has the desired grep config and the server is restarted.")
    print()

    client = SyncOpenViking()
    client.initialize()

    try:
        results = benchmark_engine(client, args.engine_label)
    finally:
        client.close()

    output_file = f"step2_result_{args.engine_label}.json"
    with open(output_file, "w") as f:
        json.dump({"engine_label": args.engine_label, "results": results}, f, indent=2)
    print(f"\nResults saved to {output_file}")

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
