#!/usr/bin/env python3
"""Step 3 (Performance): Benchmark grep latency and match count.

Runs grep queries against the synthetic dataset, measuring latency and
returned match count with a fixed node_limit.

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

from openviking_cli.client.sync_http import SyncHTTPClient

BASE_URI = "viking://resources/benchmark/performance"
SYNTHETIC_DIR = os.path.expanduser("~/.openviking/data/benchmark/synthetic")

# Same target words as step0_prepare_data.py
TARGET_GROUPS: list[tuple[float, list[str]]] = [
    (0.01, ["heliofract", "prismcache", "fluxkernel"]),
    (0.001, ["auroracode", "kiteshade", "glyphvector"]),
    (0.001, ["cortexmint", "latticewave", "spiralsync"]),
    (0.0005, ["ripplehash", "embertrace", "novaframe"]),
    (0.0001, ["zephyrloom", "quartzrelay", "nebulaindex"]),
]

RUNS = 3
WARMUP = 1
GREP_NODE_LIMIT = 256


def _format_probability(probability: float) -> str:
    return f"{probability * 100:.3f}%"


def count_local_files() -> int:
    """Count total .txt files in the synthetic dataset."""
    count = 0
    if not os.path.isdir(SYNTHETIC_DIR):
        return 0
    for _root, _dirs, files in os.walk(SYNTHETIC_DIR):
        for f in files:
            if f.endswith(".txt"):
                count += 1
    return count


def run_grep(client: SyncHTTPClient, pattern: str, uri: str) -> tuple[float, int, set[str]]:
    start = time.monotonic()
    result = client.grep(uri=uri, pattern=pattern, node_limit=GREP_NODE_LIMIT)
    elapsed = time.monotonic() - start
    match_uris: set[str] = set()
    if isinstance(result, dict):
        for match in result.get("matches", []):
            uri_val = match.get("uri", "")
            if uri_val:
                match_uris.add(uri_val.rstrip("/"))
    return elapsed, len(match_uris), match_uris


def benchmark_engine(client: SyncHTTPClient, total_files: int) -> list[dict]:
    results = []

    for prob, words in sorted(TARGET_GROUPS, key=lambda item: item[0], reverse=True):
        for word in words:
            expected = int(total_files * prob)
            label = f"{word} (p={_format_probability(prob)}, expect~{expected})"

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
                    elapsed, matches, _ = run_grep(client, word, BASE_URI)
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
                print(f"avg={avg_ms:.1f}ms  matches={match_count}  expected~{expected}")
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
            elapsed, matches, _ = run_grep(client, "zzz_nonexistent_perf", BASE_URI)
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
                f"{word:<20} {_format_probability(r.get('probability', 0)):>8} {'N/A':>14} {cur_ms:>14.1f} {'---':>10}"
            )
            continue
        cmp_ms = cmp["avg_ms"]
        speedup = cmp_ms / cur_ms if cur_ms > 0 else float("inf")
        speedup_str = f"{speedup:.1f}x"
        print(
            f"{word:<20} {_format_probability(r.get('probability', 0)):>8} "
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

    client = SyncHTTPClient(timeout=3600)
    client.initialize()

    print("=" * 80)
    print(f"Step 3 (Performance): Grep Benchmark — engine={args.engine_label}")
    print("=" * 80)
    print(f"  URI:          {BASE_URI}")
    print(f"  Total files:  {total_files:,}")
    print(f"  Grep limit:   {GREP_NODE_LIMIT}")
    print(f"  Runs per test: {RUNS} (warmup: {WARMUP})")
    print()
    print("Ensure ov.conf has the desired grep config and the server is restarted.")
    print()

    try:
        results = benchmark_engine(client, total_files)
    finally:
        client.close()

    output_file = f"step3_result_{args.engine_label}.json"
    with open(output_file, "w", encoding="utf-8") as f:
        json.dump(
            {
                "engine_label": args.engine_label,
                "total_files": total_files,
                "grep_node_limit": GREP_NODE_LIMIT,
                "results": results,
            },
            f,
            indent=2,
            ensure_ascii=False,
        )
    print(f"\nResults saved to {output_file}")

    print()
    print(
        f"{'Word':<20} {'Prob':>8} {'Avg(ms)':>10} {'Min(ms)':>10} {'Max(ms)':>10} {'Matches':>10} {'Expect~':>10}"
    )
    print("-" * 96)
    for r in results:
        if "error" in r:
            print(f"{r.get('word', '?'):<20} {'FAILED':>10}")
        else:
            print(
                f"{r['word']:<20} {_format_probability(r.get('probability', 0)):>8} "
                f"{r['avg_ms']:>10.1f} {r['min_ms']:>10.1f} "
                f"{r['max_ms']:>10.1f} {r['matches']:>10} "
                f"{r.get('expected_approx', '?'):>10}"
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
