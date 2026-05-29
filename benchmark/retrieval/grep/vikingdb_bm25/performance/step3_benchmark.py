#!/usr/bin/env python3
"""Step 3 (Performance): Benchmark grep performance and recall.

Runs grep queries against the synthetic dataset, measuring both latency
and recall. Target words from step0 are used as test queries, with
expected hit counts computed from the injection probabilities.

Run twice with different ov.conf engine settings to compare:
  1. Set ov.conf: "grep": {"engine": "fs"}, restart, then:
     python3 step3_benchmark.py --engine-label fs
  2. Set ov.conf: "grep": {"engine": "auto", "switch_to_remote_threshold": 0}, restart, then:
     python3 step3_benchmark.py --engine-label auto --compare step3_result_fs.json

Results are saved to step3_result_{engine_label}.json.
"""

from __future__ import annotations

import argparse
import json
import os
import time

from openviking.sync_client import SyncOpenViking

BASE_URI = "viking://resources/benchmark/performance"
DATA_DIR = os.path.expanduser("~/.openviking/data/benchmark/synthetic")

# Same target words as step0_prepare_data.py
TARGET_WORDS = {
    0.50: ["quantumnexus", "synapseflow", "deepvector"],
    0.10: ["bm25engine", "vikingcore", "retrievex"],
    0.001: ["zephyrhash", "cryptolattice", "nebulalink"],
    0.0001: ["xenoform", "quarkpulse", "omegabind"],
}

RUNS = 3
WARMUP = 1


def count_local_files() -> int:
    """Count total .txt files in the synthetic dataset."""
    count = 0
    if not os.path.isdir(DATA_DIR):
        return 0
    for root, _dirs, files in os.walk(DATA_DIR):
        for f in files:
            if f.endswith(".txt"):
                count += 1
    return count


def run_grep(client: SyncOpenViking, pattern: str, uri: str) -> tuple[float, int]:
    start = time.monotonic()
    result = client.grep(uri=uri, pattern=pattern, node_limit=100000)
    elapsed = time.monotonic() - start
    match_count = 0
    if isinstance(result, dict):
        matches = result.get("matches", [])
        match_count = len(matches)
    return elapsed, match_count


def benchmark_engine(client: SyncOpenViking, total_files: int) -> list[dict]:
    results = []

    for prob in sorted(TARGET_WORDS.keys(), reverse=True):
        words = TARGET_WORDS[prob]
        for word in words:
            expected = int(total_files * prob)
            label = f"{word} (p={prob * 100:.2f}%, expect~{expected})"

            print(f"  {label} ...", end=" ", flush=True)

            # Warmup
            for _ in range(WARMUP):
                try:
                    run_grep(client, word, BASE_URI)
                except Exception:
                    pass

            # Benchmark runs
            times = []
            match_count = 0
            failed = False
            for _ in range(RUNS):
                try:
                    elapsed, matches = run_grep(client, word, BASE_URI)
                    times.append(elapsed)
                    match_count = matches
                except Exception as e:
                    failed = True
                    print(f"FAILED ({e})")
                    break

            if failed:
                results.append({"label": label, "word": word, "probability": prob, "error": True})
            else:
                avg_ms = sum(times) / len(times) * 1000
                min_ms = min(times) * 1000
                max_ms = max(times) * 1000
                # Recall: how many of the expected files were found
                # This is approximate since injection is probabilistic
                recall = match_count / expected if expected > 0 else 1.0
                print(
                    f"avg={avg_ms:.1f}ms  matches={match_count}  expected~{expected}  recall~{recall:.2f}"
                )
                results.append(
                    {
                        "label": label,
                        "word": word,
                        "probability": prob,
                        "avg_ms": round(avg_ms, 1),
                        "min_ms": round(min_ms, 1),
                        "max_ms": round(max_ms, 1),
                        "matches": match_count,
                        "expected_approx": expected,
                        "recall_approx": round(recall, 4),
                    }
                )

    # No-match test
    label = "no-match: zzz_nonexistent_perf"
    print(f"  {label} ...", end=" ", flush=True)
    for _ in range(WARMUP):
        try:
            run_grep(client, "zzz_nonexistent_perf", BASE_URI)
        except Exception:
            pass
    times = []
    match_count = 0
    failed = False
    for _ in range(RUNS):
        try:
            elapsed, matches = run_grep(client, "zzz_nonexistent_perf", BASE_URI)
            times.append(elapsed)
            match_count = matches
        except Exception as e:
            failed = True
            print(f"FAILED ({e})")
            break
    if failed:
        results.append({"label": label, "word": "zzz_nonexistent_perf", "error": True})
    else:
        avg_ms = sum(times) / len(times) * 1000
        min_ms = min(times) * 1000
        max_ms = max(times) * 1000
        print(f"avg={avg_ms:.1f}ms  matches={match_count}")
        results.append(
            {
                "label": label,
                "word": "zzz_nonexistent_perf",
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
    compare_by_word = {}
    for r in compare:
        if "error" not in r and "word" in r:
            compare_by_word[r["word"]] = r

    print()
    print("=" * 120)
    print(f"  Comparison: {compare_label} vs {current_label}")
    print("=" * 120)
    print(
        f"{'Word':<20} {'Prob':>8} {compare_label + '(ms)':>14} {current_label + '(ms)':>14} {'speedup':>10} {'Cmp matches':>12} {'Cur matches':>12}"
    )
    print("-" * 120)

    for r in current:
        if "error" in r:
            print(f"{r.get('word', '?'):<20} {'ERR':>8} {'ERR':>14} {'ERR':>14} {'---':>10}")
            continue
        word = r["word"]
        cur_ms = r["avg_ms"]
        cmp = compare_by_word.get(word)
        if not cmp:
            print(
                f"{word:<20} {r.get('probability', 0) * 100:>7.2f}% {'N/A':>14} {cur_ms:>14.1f} {'---':>10}"
            )
            continue
        cmp_ms = cmp["avg_ms"]
        speedup = cmp_ms / cur_ms if cur_ms > 0 else float("inf")
        speedup_str = f"{speedup:.1f}x"
        print(
            f"{word:<20} {r.get('probability', 0) * 100:>7.2f}% "
            f"{cmp_ms:>14.1f} {cur_ms:>14.1f} {speedup_str:>10} "
            f"{cmp.get('matches', '?'):>12} {r.get('matches', '?'):>12}"
        )
    print()


def main():
    parser = argparse.ArgumentParser(description="Step 3 (Performance): Benchmark grep")
    parser.add_argument(
        "--engine-label",
        required=True,
        help="Label for this engine config (e.g. fs, auto). Used in output filename.",
    )
    parser.add_argument(
        "--compare",
        default=None,
        help="Path to a previous step3_result_*.json for comparison",
    )
    args = parser.parse_args()

    total_files = count_local_files()

    print("=" * 80)
    print(f"Step 3 (Performance): Grep Benchmark — engine={args.engine_label}")
    print("=" * 80)
    print(f"  URI:          {BASE_URI}")
    print(f"  Total files:  {total_files:,}")
    print(f"  Runs per test: {RUNS} (warmup: {WARMUP})")
    print()
    print("Ensure ov.conf has the desired grep config and the server is restarted.")
    print()

    client = SyncOpenViking()
    client.initialize()

    try:
        results = benchmark_engine(client, total_files)
    finally:
        client.close()

    output_file = f"step3_result_{args.engine_label}.json"
    with open(output_file, "w") as f:
        json.dump(
            {"engine_label": args.engine_label, "total_files": total_files, "results": results},
            f,
            indent=2,
        )
    print(f"\nResults saved to {output_file}")

    print()
    print(
        f"{'Word':<20} {'Prob':>8} {'Avg(ms)':>10} {'Min(ms)':>10} {'Max(ms)':>10} {'Matches':>10} {'Expect~':>10} {'Recall~':>10}"
    )
    print("-" * 108)
    for r in results:
        if "error" in r:
            print(f"{r.get('word', '?'):<20} {'FAILED':>10}")
        else:
            print(
                f"{r['word']:<20} {r.get('probability', 0) * 100:>7.2f}% "
                f"{r['avg_ms']:>10.1f} {r['min_ms']:>10.1f} "
                f"{r['max_ms']:>10.1f} {r['matches']:>10} "
                f"{r.get('expected_approx', '?'):>10} {r.get('recall_approx', '?'):>10}"
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
