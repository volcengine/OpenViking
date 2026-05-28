#!/usr/bin/env python3
"""Step 3: Retrieval quality evaluation — compare auto (bm25) vs fs grep.

Prerequisites:
  1. Run step1_add_resource.py to import repos (includes VLM+embedding)
  2. Ensure ov.conf has:
       "grep": {"engine": "auto", "switch_to_remote_threshold": 0}
     (switch_to_remote_threshold = 0 forces VikingDB BM25 for all queries)
  3. Restart the server after changing ov.conf

Approach:
  - Ground truth: scan local repo files with Python regex (equivalent to fs engine)
  - Test: call SDK grep to get structured results
  - Compare: compute Recall, Precision, F1 per query pattern

KEYWORDS: Fill the KEYWORDS list below with real terms from the imported
repos. Each keyword will be tested for retrieval quality.

Usage:
  python3 step3_retrieval_quality.py
"""

from __future__ import annotations

import os
import re
import time

from openviking.sync_client import SyncOpenViking

BASE_URI = "viking://resources/benchmark"
DATA_DIR = os.path.expanduser("~/.openviking/data")

KEYWORDS: list[str] = []


def build_test_patterns() -> list[tuple[str, str]]:
    patterns = []

    for kw in KEYWORDS:
        patterns.append((f"keyword: {kw}", kw))

    if len(KEYWORDS) >= 2:
        patterns.append((f"multi 2: {KEYWORDS[0]}|{KEYWORDS[1]}", f"{KEYWORDS[0]}|{KEYWORDS[1]}"))

    patterns.append(("no-match: zzz_nonexistent_benchmark", "zzz_nonexistent_benchmark"))

    return patterns


def run_sdk_grep(client: SyncOpenViking, uri: str, pattern: str) -> tuple[set[str], float]:
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


def local_path_to_viking_uri(filepath: str) -> str:
    rel = os.path.relpath(filepath, DATA_DIR)
    return "viking://resources/" + rel.replace(os.sep, "/").rstrip("/")


def compute_ground_truth(pattern: str, search_dirs: list[str]) -> tuple[set[str], float]:
    compiled = re.compile(pattern)
    truth_uris = set()
    t0 = time.monotonic()
    for search_dir in search_dirs:
        if not os.path.isdir(search_dir):
            continue
        for root, dirs, files in os.walk(search_dir):
            dirs.sort()
            for fname in sorted(files):
                if not (
                    fname.endswith(".py")
                    or fname.endswith(".md")
                    or fname.endswith(".rs")
                    or fname.endswith(".toml")
                    or fname.endswith(".yaml")
                    or fname.endswith(".yml")
                    or fname.endswith(".json")
                    or fname.endswith(".txt")
                    or fname.endswith(".cfg")
                    or fname.endswith(".ini")
                ):
                    continue
                filepath = os.path.join(root, fname)
                try:
                    with open(filepath, errors="ignore") as f:
                        content = f.read()
                    if compiled.search(content):
                        truth_uris.add(local_path_to_viking_uri(filepath))
                except Exception:
                    pass
    elapsed = time.monotonic() - t0
    return truth_uris, elapsed


def discover_local_repo_dirs() -> list[str]:
    benchmark_dir = os.path.join(DATA_DIR, "benchmark")
    if not os.path.isdir(benchmark_dir):
        return []
    dirs = []
    for entry in sorted(os.listdir(benchmark_dir)):
        path = os.path.join(benchmark_dir, entry)
        if os.path.isdir(path) and not entry.startswith("."):
            dirs.append(path)
    return dirs


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
    uri = BASE_URI
    search_dirs = discover_local_repo_dirs()

    if not search_dirs:
        print(f"Error: No repo directories found under {DATA_DIR}/benchmark/")
        print("Run step1_add_resource.py first.")
        return

    if not KEYWORDS:
        print("WARNING: KEYWORDS list is empty. Fill it with real terms before running.")
        print("         Edit step3_retrieval_quality.py and add keywords to the KEYWORDS list.\n")

    test_patterns = build_test_patterns()

    print("=" * 110)
    print("Retrieval Quality Evaluation: auto (bm25+fs) vs local fs (ground truth)")
    print("=" * 110)
    print(f"URI:       {uri}")
    print(f"Data dir:  {DATA_DIR}/benchmark/")
    print(f"Local dirs: {search_dirs}")
    print(f"Patterns:  {len(test_patterns)}")
    print()
    print("Ensure ov.conf has:")
    print('  "grep": {"engine": "auto", "switch_to_remote_threshold": 0}')
    print("And the server has been restarted.")
    print()

    results = []

    client = SyncOpenViking()
    client.initialize()

    try:
        for label, pattern in test_patterns:
            print(f"--- {label} (pattern: {pattern}) ---")

            truth_uris, fs_elapsed = compute_ground_truth(pattern, search_dirs)
            print(f"  Ground truth (local fs): {len(truth_uris)} matches ({fs_elapsed:.2f}s)")

            try:
                auto_uris, auto_elapsed = run_sdk_grep(client, uri, pattern)
            except Exception as e:
                print(f"  Auto grep FAILED: {e}")
                results.append(
                    {
                        "label": label,
                        "pattern": pattern,
                        "error": str(e),
                        "truth_count": len(truth_uris),
                    }
                )
                continue
            print(f"  Auto grep (bm25+fs):   {len(auto_uris)} matches ({auto_elapsed:.2f}s)")

            metrics = compute_metrics(truth_uris, auto_uris)
            print(
                f"  Recall: {metrics['recall']:.4f}  "
                f"Precision: {metrics['precision']:.4f}  "
                f"F1: {metrics['f1']:.4f}"
            )
            if metrics["fn"] > 0:
                print(f"  Missed (FN): {metrics['fn']}")
            if metrics["fp"] > 0:
                print(f"  Extra (FP): {metrics['fp']}")

            if metrics["fn"] > 0:
                missed = sorted(truth_uris - auto_uris)[:5]
                print("  Sample missed URIs:")
                for u in missed:
                    print(f"    {u}")

            results.append(
                {
                    "label": label,
                    "pattern": pattern,
                    "truth_count": len(truth_uris),
                    "auto_count": len(auto_uris),
                    "fs_elapsed_s": round(fs_elapsed, 3),
                    "auto_elapsed_s": round(auto_elapsed, 3),
                    **metrics,
                }
            )
    finally:
        client.close()

    print()
    print("=" * 120)
    print(
        f"{'Label':<45} {'Truth':>6} {'Auto':>6} {'Recall':>8} {'Prec':>8} {'F1':>8} {'Missed':>8}"
    )
    print("-" * 120)
    for r in results:
        if "error" in r:
            print(
                f"{r['label']:<45} {r['truth_count']:>6} {'ERR':>6} "
                f"{'---':>8} {'---':>8} {'---':>8} {'---':>8}"
            )
        else:
            print(
                f"{r['label']:<45} {r['truth_count']:>6} {r['auto_count']:>6} "
                f"{r['recall']:>8.4f} {r['precision']:>8.4f} {r['f1']:>8.4f} {r['fn']:>8}"
            )
    print()

    has_recall_loss = any(r.get("fn", 0) > 0 for r in results)
    has_precision_loss = any(r.get("fp", 0) > 0 for r in results)
    if not has_recall_loss and not has_precision_loss:
        print(
            "VERDICT: All queries achieved perfect recall and precision. bm25 recall is complete."
        )
    else:
        if has_recall_loss:
            print("VERDICT: Recall loss detected — some files not recalled by bm25.")
            print(
                "  Possible causes: content field truncation, tokenizer mismatch, or incomplete reindex."
            )
        if has_precision_loss:
            print("VERDICT: Precision loss detected — unexpected matches in auto results.")
            print(
                "  This should not happen (phase 2 regex guarantees precision). Investigate URI format."
            )
    print()


if __name__ == "__main__":
    main()
