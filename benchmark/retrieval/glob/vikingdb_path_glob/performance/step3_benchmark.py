#!/usr/bin/env python3
# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0
"""Step 3 (Performance): benchmark glob latency across scales; fs vs auto.

Runs each benchmark pattern against each scale's glob root, measuring latency
and returned match count with a fixed node_limit. Runs are repeated (with a
warmup) and the average/min/max are recorded.

Run twice with different ov.conf glob settings to compare engines:
  1. glob: {"engine": "fs"} -> restart server:
       python3 step3_benchmark.py --engine-label fs
  2. glob: {"engine": "auto", "switch_to_remote_threshold": 1} -> restart:
       python3 step3_benchmark.py --engine-label auto --compare step3_result_fs.json

threshold=1 forces every non-empty subtree to the remote VikingDB path_glob.
Results are saved to step3_result_<engine-label>.json.

Usage:
  python3 step3_benchmark.py --engine-label fs
  python3 step3_benchmark.py --engine-label auto --scales 5k 10w 100w
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import time

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..")))

from vikingdb_path_glob.common import dataset  # noqa: E402
from vikingdb_path_glob.common.patterns import PATTERNS  # noqa: E402

from openviking_cli.client.sync_http import SyncHTTPClient  # noqa: E402

DEFAULT_OUTPUT = os.path.expanduser("~/.openviking/data/benchmark/glob_synthetic_v2")
BASE_URI = "viking://resources/benchmark/glob"

RUNS = 3
WARMUP = 1
# Default patterns have at most 1,000 manifest matches at every scale.
NODE_LIMIT = 2_000


def scale_base_uri(bucket: str) -> str:
    return BASE_URI if not bucket else f"{BASE_URI}/{bucket}"


def run_glob(client: SyncHTTPClient, uri: str, pattern: str) -> tuple[float, int]:
    start = time.monotonic()
    result = client.glob(uri=uri, pattern=pattern, node_limit=NODE_LIMIT)
    elapsed = time.monotonic() - start
    count = 0
    if isinstance(result, dict):
        # ``matches`` is a list of URI strings; directory hits carry a trailing
        # slash. Count files only so the number lines up with the manifest and
        # the effectiveness report.
        count = sum(
            1 for m in result.get("matches", []) if isinstance(m, str) and not m.endswith("/")
        )
    return elapsed, count


def benchmark_scale(client: SyncHTTPClient, scale: str, uri: str) -> list[dict]:
    rows = []
    for gp in PATTERNS:
        for _ in range(WARMUP):
            try:
                run_glob(client, uri, gp.pattern)
            except Exception:  # noqa: BLE001
                pass

        times: list[float] = []
        matches = 0
        failed = False
        for _ in range(RUNS):
            try:
                elapsed, matches = run_glob(client, uri, gp.pattern)
                times.append(elapsed)
            except Exception as exc:  # noqa: BLE001
                failed = True
                print(f"    {gp.label:18} FAILED: {str(exc)[:200]}")
                break

        if failed:
            rows.append({"scale": scale, "label": gp.label, "pattern": gp.pattern, "error": True})
            continue

        avg_ms = sum(times) / len(times) * 1000
        row = {
            "scale": scale,
            "label": gp.label,
            "pattern": gp.pattern,
            "tier": gp.tier,
            "target": gp.target,
            "avg_ms": round(avg_ms, 1),
            "min_ms": round(min(times) * 1000, 1),
            "max_ms": round(max(times) * 1000, 1),
            "matches": matches,
        }
        rows.append(row)
        tag = f"[{gp.tier}]"
        print(f"    {gp.label:18} {tag:12} avg={avg_ms:8.1f}ms  matches={matches}")
    return rows


def print_comparison(current_label, current, compare_label, compare) -> None:
    key = lambda r: (r.get("scale"), r.get("label"))  # noqa: E731
    cmp_by_key = {key(r): r for r in compare if "error" not in r}

    print()
    print("=" * 110)
    print(f"  Comparison: {compare_label} vs {current_label}")
    print("=" * 110)
    print(
        f"{'Scale':>6} {'Pattern':<20} {compare_label + '(ms)':>14} "
        f"{current_label + '(ms)':>14} {'speedup':>9} {'cmp/cur matches':>18}"
    )
    print("-" * 110)
    for r in current:
        if "error" in r:
            print(f"{r.get('scale', '?'):>6} {r.get('label', '?'):<20} {'ERR':>14}")
            continue
        cmp = cmp_by_key.get(key(r))
        cur_ms = r["avg_ms"]
        if not cmp:
            print(f"{r['scale']:>6} {r['label']:<20} {'N/A':>14} {cur_ms:>14.1f}")
            continue
        cmp_ms = cmp["avg_ms"]
        speedup = cmp_ms / cur_ms if cur_ms > 0 else float("inf")
        match_flag = "" if cmp.get("matches") == r.get("matches") else " *"
        print(
            f"{r['scale']:>6} {r['label']:<20} {cmp_ms:>14.1f} {cur_ms:>14.1f} "
            f"{speedup:>8.1f}x {str(cmp.get('matches')) + '/' + str(r.get('matches')) + match_flag:>18}"
        )
    print()
    print("  '*' marks patterns where match counts differ between engines.")


def main() -> None:
    parser = argparse.ArgumentParser(description="Step 3 (Performance): benchmark glob")
    parser.add_argument("--engine-label", required=True, help="label for this run (e.g. fs, auto)")
    parser.add_argument("--output", default=DEFAULT_OUTPUT)
    parser.add_argument("--scales", nargs="+", default=None, help="subset of scales")
    parser.add_argument("--url", default=None, help="OpenViking server URL")
    parser.add_argument("--compare", default=None, help="previous step3_result_*.json to compare")
    args = parser.parse_args()

    output = os.path.expanduser(args.output)
    index = dataset.load_manifest_index(output)
    scale_entries = index["scales"]
    if args.scales:
        wanted = set(args.scales)
        scale_entries = [e for e in scale_entries if e["scale"] in wanted]

    client = (
        SyncHTTPClient(url=args.url, timeout=3600) if args.url else SyncHTTPClient(timeout=3600)
    )
    client.initialize()

    print("=" * 80)
    print(f"Step 3 (Performance): Glob Benchmark — engine={args.engine_label}")
    print("=" * 80)
    print(f"  Base URI:   {BASE_URI}")
    print(f"  node_limit: {NODE_LIMIT}   runs: {RUNS} (warmup {WARMUP})")
    print("  Ensure ov.conf glob config matches the engine label and server restarted.")
    print()

    results: list[dict] = []
    try:
        for entry in scale_entries:
            scale = entry["scale"]
            uri = scale_base_uri(entry["bucket"])
            print(f"  scale={scale} (files~{entry['count']:,}) uri={uri}")
            results.extend(benchmark_scale(client, scale, uri))
            print()
    finally:
        client.close()

    out_file = f"step3_result_{args.engine_label}.json"
    with open(out_file, "w", encoding="utf-8") as file:
        json.dump(
            {"engine_label": args.engine_label, "node_limit": NODE_LIMIT, "results": results},
            file,
            indent=2,
            ensure_ascii=False,
        )
    print(f"Results saved to {out_file}")

    if args.compare:
        if not os.path.isfile(args.compare):
            print(f"Warning: compare file not found: {args.compare}")
        else:
            with open(args.compare) as file:
                prev = json.load(file)
            print_comparison(
                args.engine_label,
                results,
                prev.get("engine_label", "previous"),
                prev.get("results", []),
            )


if __name__ == "__main__":
    main()
