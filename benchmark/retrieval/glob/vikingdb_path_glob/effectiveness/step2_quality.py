#!/usr/bin/env python3
# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0
"""Step 2 (Effectiveness): score glob recall/precision against the manifest.

Ground truth comes from the dataset manifest (see ``common/glob_match.py``),
not from either engine, so both the ``fs`` and ``auto`` (remote VikingDB
path_glob) engines are scored against the same truth. This surfaces both missed
recall (false negatives) and extra recall (false positives) at every scale.

Run once per engine you want to score:
  1. glob: {"engine": "fs"} -> restart -> python3 step2_quality.py
  2. glob: {"engine": "auto", "switch_to_remote_threshold": 1} -> restart ->
       python3 step2_quality.py --engine-label auto

For each (scale, pattern) it queries glob with a node_limit above the bounded
default pattern cardinality, compares the returned URI set to the manifest
expectation, and dumps miss analysis (FN/FP) for inspection.

Usage:
  python3 step2_quality.py --engine-label fs
  python3 step2_quality.py --engine-label auto --scales 5k 10w 100w
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import time

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..")))

from vikingdb_path_glob.common import dataset  # noqa: E402
from vikingdb_path_glob.common.glob_match import expected_matches  # noqa: E402
from vikingdb_path_glob.common.patterns import PATTERNS  # noqa: E402

from openviking_cli.client.sync_http import SyncHTTPClient  # noqa: E402

DEFAULT_OUTPUT = os.path.expanduser("~/.openviking/data/benchmark/glob_synthetic_v2")
BASE_URI = "viking://resources/benchmark/glob"
# Default patterns have at most 1,000 manifest matches at every scale.
QUALITY_NODE_LIMIT = 2_000


def scale_base_uri(bucket: str) -> str:
    return BASE_URI if not bucket else f"{BASE_URI}/{bucket}"


def sdk_glob_uris(client: SyncHTTPClient, uri: str, pattern: str) -> tuple[set[str], float]:
    t0 = time.monotonic()
    result = client.glob(uri=uri, pattern=pattern, node_limit=QUALITY_NODE_LIMIT)
    elapsed = time.monotonic() - t0
    uris: set[str] = set()
    if isinstance(result, dict):
        # ``matches`` is a list of URI strings; directory hits carry a trailing
        # slash. Ground truth is file-only, so drop directories.
        for match in result.get("matches", []):
            if isinstance(match, str) and match and not match.endswith("/"):
                uris.add(match.rstrip("/"))
    return uris, elapsed


def expected_uri_set(base_uri: str, rel_paths: list[str], pattern: str) -> set[str]:
    """Manifest ground truth as absolute URIs under ``base_uri``."""
    expected_rel = expected_matches(pattern, rel_paths)
    base = base_uri.rstrip("/")
    return {f"{base}/{rel}" for rel in expected_rel}


def compute_metrics(truth: set[str], predicted: set[str]) -> dict:
    if not truth and not predicted:
        return {"recall": 1.0, "precision": 1.0, "f1": 1.0, "tp": 0, "fp": 0, "fn": 0}
    if not truth:
        return {"recall": 0.0, "precision": 0.0, "f1": 0.0, "tp": 0, "fp": len(predicted), "fn": 0}
    tp = len(truth & predicted)
    fp = len(predicted - truth)
    fn = len(truth - predicted)
    recall = tp / len(truth)
    precision = tp / len(predicted) if predicted else 0.0
    f1 = 2 * recall * precision / (recall + precision) if (recall + precision) > 0 else 0.0
    return {"recall": recall, "precision": precision, "f1": f1, "tp": tp, "fp": fp, "fn": fn}


def main() -> None:
    parser = argparse.ArgumentParser(description="Step 2 (Effectiveness): glob quality")
    parser.add_argument("--engine-label", default="current", help="label for output files")
    parser.add_argument("--output", default=DEFAULT_OUTPUT)
    parser.add_argument("--scales", nargs="+", default=None, help="subset of scales")
    parser.add_argument("--url", default=None, help="OpenViking server URL")
    args = parser.parse_args()

    output = os.path.expanduser(args.output)
    index = dataset.load_manifest_index(output)
    scale_entries = index["scales"]
    if args.scales:
        wanted = set(args.scales)
        scale_entries = [e for e in scale_entries if e["scale"] in wanted]

    result_dir = os.path.join(output, "effectiveness", args.engine_label)
    miss_dir = os.path.join(result_dir, "miss")
    os.makedirs(miss_dir, exist_ok=True)

    print("=" * 110)
    print(f"Effectiveness: glob vs manifest ground truth — engine={args.engine_label}")
    print("=" * 110)
    print(f"  Base URI: {BASE_URI}")
    print("  Ensure ov.conf glob config matches the engine label and server restarted.")
    print()

    client = (
        SyncHTTPClient(url=args.url, timeout=3600) if args.url else SyncHTTPClient(timeout=3600)
    )
    client.initialize()

    all_rows: list[dict] = []
    try:
        for entry in scale_entries:
            scale = entry["scale"]
            base_uri = scale_base_uri(entry["bucket"])
            rel_paths = dataset.load_scale_relpaths(output, scale)
            print(f"  scale={scale} (files~{entry['count']:,}) uri={base_uri}")

            for gp in PATTERNS:
                truth = expected_uri_set(base_uri, rel_paths, gp.pattern)
                try:
                    predicted, elapsed = sdk_glob_uris(client, base_uri, gp.pattern)
                except Exception as exc:  # noqa: BLE001
                    print(f"    {gp.label:18} FAILED: {str(exc)[:200]}")
                    all_rows.append(
                        {"scale": scale, "label": gp.label, "pattern": gp.pattern, "error": True}
                    )
                    continue

                metrics = compute_metrics(truth, predicted)
                fn_uris = sorted(truth - predicted)
                fp_uris = sorted(predicted - truth)
                if fn_uris or fp_uris:
                    miss_path = os.path.join(miss_dir, f"{scale}_{gp.label}.json")
                    with open(miss_path, "w", encoding="utf-8") as file:
                        json.dump(
                            {
                                "scale": scale,
                                "pattern": gp.pattern,
                                "missed_fn": fn_uris[:1000],
                                "missed_fn_count": len(fn_uris),
                                "extra_fp": fp_uris[:1000],
                                "extra_fp_count": len(fp_uris),
                            },
                            file,
                            indent=2,
                            ensure_ascii=False,
                        )

                flag = "" if metrics["fn"] == 0 and metrics["fp"] == 0 else "  <-- MISMATCH"
                tag = f"[{gp.tier}{'/' + gp.target if gp.target else ''}]"
                print(
                    f"    {gp.label:18} {tag:16} truth={len(truth):>7} found={len(predicted):>7} "
                    f"R={metrics['recall']:.4f} P={metrics['precision']:.4f} "
                    f"F1={metrics['f1']:.4f} FN={metrics['fn']} FP={metrics['fp']}"
                    f" ({elapsed:.2f}s){flag}"
                )
                all_rows.append(
                    {
                        "scale": scale,
                        "label": gp.label,
                        "pattern": gp.pattern,
                        "tier": gp.tier,
                        "target": gp.target,
                        "truth_count": len(truth),
                        "found_count": len(predicted),
                        "elapsed_s": round(elapsed, 3),
                        **metrics,
                    }
                )
            print()
    finally:
        client.close()

    summary_path = os.path.join(result_dir, "step2_result.json")
    with open(summary_path, "w", encoding="utf-8") as file:
        json.dump({"engine_label": args.engine_label, "results": all_rows}, file, indent=2)

    perfect = [r for r in all_rows if "error" not in r and r.get("fn") == 0 and r.get("fp") == 0]
    mism = [r for r in all_rows if "error" not in r and (r.get("fn") or r.get("fp"))]
    print("=" * 110)
    print(f"  Perfect (FN=0, FP=0): {len(perfect)}   Mismatched: {len(mism)}")
    for r in mism:
        print(
            f"    MISMATCH scale={r['scale']:>5} {r['label']:18} "
            f"FN={r['fn']} FP={r['fp']} (truth={r['truth_count']} found={r['found_count']})"
        )
    print()
    print(f"Results:      {summary_path}")
    print(f"Miss dumps:   {miss_dir}/")


if __name__ == "__main__":
    main()
