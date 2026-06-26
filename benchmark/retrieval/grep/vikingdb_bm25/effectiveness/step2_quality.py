#!/usr/bin/env python3
"""Step 2 (Effectiveness): Evaluate retrieval quality for real code repos.

Compares grep results (current engine) against ground truth from fs-engine grep.
Computes Recall, Precision, F1 per query pattern.

Ground truth is obtained by running grep with engine=fs (must be configured
in ov.conf on first run). Results are cached locally so subsequent runs
can use a different engine config while still comparing against the same
ground truth.

Prerequisites:
  1. Run step1_add_resource.py to import repos (with indexing)
  2. First run: set ov.conf grep engine to "fs" and restart server

Usage:
  python3 step2_quality.py --keywords grep reindex SyncHTTPClient
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import time

from openviking_cli.client.sync_http import SyncHTTPClient

BASE_URI = "viking://resources/benchmark/effectiveness"
DATA_DIR = os.path.expanduser("~/.openviking/data/benchmark/effectiveness")
GROUND_TRUTH_DIR = os.path.join(DATA_DIR, ".ground_truth")
MISS_DIR = os.path.join(DATA_DIR, ".miss")
RESULT_DIR = os.path.join(DATA_DIR, ".result")

KEYWORDS: list[str] = [
    # High frequency English
    "embedding",
    "grep",
    # Medium frequency English
    "vikingdb",
    "reindex",
    # Low frequency English
    "build_index",
    # CamelCase
    "SyncHTTPClient",
    "MarkdownParser",
    "DataDirectoryLocked",
    # snake_case
    "add_resource",
    "process_lock",
    # Chinese
    "检索",
    "向量数据库",
]  # Can also be overridden via --keywords


def _sanitize_filename(s: str, max_len: int = 40) -> str:
    """Make a string safe for use as a filename component. Preserves Unicode."""
    s = re.sub(r'[/\\:*?"<>|\0]', "_", s)
    s = s.strip("_ ")
    return s[:max_len]


def _cache_hash(uri: str, pattern: str) -> str:
    """Short hash for cache disambiguation."""
    return hashlib.sha256(uri.encode("utf-8") + pattern.encode("utf-8")).hexdigest()[:8]


def build_test_patterns(keywords: list[str] | None = None) -> list[tuple[str, str]]:
    kws = keywords if keywords else KEYWORDS
    patterns = []
    for kw in kws:
        patterns.append((f"keyword: {kw}", kw))
    if len(kws) >= 2:
        patterns.append((f"multi 2: {kws[0]}|{kws[1]}", f"{kws[0]}|{kws[1]}"))
    patterns.append(("no-match: zzz_nonexistent_quality", "zzz_nonexistent_quality"))
    return patterns


def run_sdk_grep(client: SyncHTTPClient, uri: str, pattern: str) -> tuple[set[str], float]:
    t0 = time.monotonic()
    result = client.grep(uri=uri, pattern=pattern, node_limit=100000)
    elapsed = time.monotonic() - t0
    uris = set()
    if isinstance(result, dict):
        for match in result.get("matches", []):
            uri_val = match.get("uri", "")
            if uri_val:
                uris.add(uri_val.rstrip("/"))
    return uris, elapsed


def _ground_truth_cache_path(pattern: str, uri: str) -> str:
    h = _cache_hash(uri, pattern)
    safe_pattern = _sanitize_filename(pattern)
    return os.path.join(GROUND_TRUTH_DIR, f"eff_{safe_pattern}_{h}.json")


def _load_ground_truth_cache(pattern: str, uri: str) -> set[str] | None:
    path = _ground_truth_cache_path(pattern, uri)
    if not os.path.isfile(path):
        # Fallback: try old-style hash-only filename
        old_h = hashlib.sha256(uri.encode("utf-8")).hexdigest()
        old_h += hashlib.sha256(pattern.encode("utf-8")).hexdigest()[:16]
        old_path = os.path.join(GROUND_TRUTH_DIR, f"eff_{old_h[:16]}.json")
        if os.path.isfile(old_path):
            with open(old_path, encoding="utf-8") as f:
                data = json.load(f)
            return set(data.get("uris", []))
        return None
    with open(path, encoding="utf-8") as f:
        data = json.load(f)
    return set(data.get("uris", []))


def _save_ground_truth_cache(pattern: str, uri: str, uris: set[str]) -> None:
    os.makedirs(GROUND_TRUTH_DIR, exist_ok=True)
    path = _ground_truth_cache_path(pattern, uri)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(
            {"pattern": pattern, "uri": uri, "uris": sorted(uris)}, f, indent=2, ensure_ascii=False
        )


def compute_ground_truth(client: SyncHTTPClient, uri: str, pattern: str) -> tuple[set[str], float]:
    """Compute ground truth via OV grep (fs engine). First run must have engine=fs."""
    cached = _load_ground_truth_cache(pattern, uri)
    if cached is not None:
        return cached, 0.0

    truth_uris, elapsed = run_sdk_grep(client, uri, pattern)
    _save_ground_truth_cache(pattern, uri, truth_uris)
    return truth_uris, elapsed


def _miss_cache_path(pattern: str, uri: str) -> str:
    h = _cache_hash(uri, pattern)
    safe_pattern = _sanitize_filename(pattern)
    return os.path.join(MISS_DIR, f"eff_{safe_pattern}_{h}.json")


def _save_miss(pattern: str, uri: str, missed_uris: set[str], extra_uris: set[str]) -> None:
    """Save miss analysis (FN and FP) to .miss directory."""
    if not missed_uris and not extra_uris:
        return
    os.makedirs(MISS_DIR, exist_ok=True)
    path = _miss_cache_path(pattern, uri)
    data: dict = {"pattern": pattern, "uri": uri}
    if missed_uris:
        data["missed_fn"] = sorted(missed_uris)
        data["missed_fn_count"] = len(missed_uris)
    if extra_uris:
        data["extra_fp"] = sorted(extra_uris)
        data["extra_fp_count"] = len(extra_uris)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2, ensure_ascii=False)


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


def main():
    import argparse

    parser = argparse.ArgumentParser(
        description="Step 2 (Effectiveness): Evaluate retrieval quality"
    )
    parser.add_argument(
        "--keywords",
        nargs="+",
        default=None,
        help="Keywords to search (e.g. --keywords grep reindex SyncHTTPClient)",
    )
    parser.add_argument(
        "--regenerate-ground-truth",
        action="store_true",
        help="Regenerate ground truth cache (requires engine=fs in ov.conf)",
    )
    args = parser.parse_args()

    uri = BASE_URI
    keywords = args.keywords if args.keywords else KEYWORDS
    if not keywords:
        print("WARNING: KEYWORDS list is empty. Fill it with real terms before running.")
        print("         Use --keywords kw1 kw2 ... or edit step2_quality.py.\n")

    test_patterns = build_test_patterns(keywords)

    print("=" * 110)
    print("Effectiveness Evaluation: grep vs ground truth (fs engine)")
    print("=" * 110)
    print(f"URI:       {uri}")
    print(f"Patterns:  {len(test_patterns)}")
    print()
    print("NOTE: First run requires ov.conf grep engine=fs to generate ground truth.")
    print("      Subsequent runs can use any engine; cached ground truth is reused.")
    print()

    client = SyncHTTPClient()
    client.initialize()

    # Phase 1: Compute ground truth (needs fs engine on first run)
    print("--- Phase 1: Ground truth (fs engine) ---")
    ground_truth_map: dict[str, tuple[set[str], float]] = {}
    for label, pattern in test_patterns:
        if args.regenerate_ground_truth:
            cache_path = _ground_truth_cache_path(pattern, uri)
            if os.path.isfile(cache_path):
                os.remove(cache_path)
        truth_uris, gt_elapsed = compute_ground_truth(client, uri, pattern)
        ground_truth_map[pattern] = (truth_uris, gt_elapsed)
        cached_str = "(cached)" if gt_elapsed == 0.0 else f"({gt_elapsed:.2f}s)"
        print(f"  {label}: {len(truth_uris)} matches {cached_str}")

    print()
    print("--- Phase 2: Evaluate with current engine ---")

    results = []
    try:
        for label, pattern in test_patterns:
            truth_uris, gt_elapsed = ground_truth_map[pattern]
            try:
                auto_uris, auto_elapsed = run_sdk_grep(client, uri, pattern)
            except Exception as e:
                print(f"  {label} FAILED: {e}")
                results.append(
                    {
                        "label": label,
                        "pattern": pattern,
                        "error": str(e),
                        "truth_count": len(truth_uris),
                    }
                )
                continue

            metrics = compute_metrics(truth_uris, auto_uris)

            # Save miss analysis
            missed_uris = truth_uris - auto_uris  # FN
            extra_uris = auto_uris - truth_uris  # FP
            _save_miss(pattern, uri, missed_uris, extra_uris)

            miss_str = f" missed={len(missed_uris)}" if missed_uris else ""
            extra_str = f" extra={len(extra_uris)}" if extra_uris else ""
            print(
                f"  {label}: truth={len(truth_uris)} found={len(auto_uris)}  "
                f"Recall={metrics['recall']:.4f} Prec={metrics['precision']:.4f} F1={metrics['f1']:.4f}"
                f"{miss_str}{extra_str}"
            )

            results.append(
                {
                    "label": label,
                    "pattern": pattern,
                    "truth_count": len(truth_uris),
                    "found_count": len(auto_uris),
                    "gt_elapsed_s": round(gt_elapsed, 3),
                    "sdk_elapsed_s": round(auto_elapsed, 3),
                    **metrics,
                }
            )
    finally:
        client.close()

    # Summary table
    print()
    print("=" * 120)
    print(
        f"{'Label':<45} {'Truth':>6} {'Found':>6} {'Recall':>8} {'Prec':>8} {'F1':>8} {'Missed':>8} {'Extra':>8}"
    )
    print("-" * 120)
    for r in results:
        if "error" in r:
            print(
                f"{r['label']:<45} {r['truth_count']:>6} {'ERR':>6} "
                f"{'---':>8} {'---':>8} {'---':>8} {'---':>8} {'---':>8}"
            )
        else:
            print(
                f"{r['label']:<45} {r['truth_count']:>6} {r['found_count']:>6} "
                f"{r['recall']:>8.4f} {r['precision']:>8.4f} {r['f1']:>8.4f} {r['fn']:>8} {r['fp']:>8}"
            )
    print()

    # Save results to local file
    os.makedirs(RESULT_DIR, exist_ok=True)
    output_file = os.path.join(RESULT_DIR, "step2_result.json")
    with open(output_file, "w", encoding="utf-8") as f:
        json.dump(
            {"uri": uri, "patterns": len(test_patterns), "results": results},
            f,
            indent=2,
            ensure_ascii=False,
        )
    print(f"Results saved to:        {output_file}")
    print(f"Miss analysis saved to:  {MISS_DIR}/")
    print(f"Ground truth cache:      {GROUND_TRUTH_DIR}/")


if __name__ == "__main__":
    main()
