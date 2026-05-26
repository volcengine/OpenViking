#!/usr/bin/env python3
"""Step 5: Retrieval quality evaluation — compare auto (bm25) vs fs grep.

Prerequisites:
  1. Run step1_generate.py to create test data
  2. Run step2_quick_add_resource.py to upload files
  3. Run step3_build_index.py to build index (embedding + content)
  4. Ensure ov.conf has:
       "grep": {"engine": "auto", "switch_to_remote_threshold": 0}
     (switch_to_remote_threshold = 0 forces VikingDB BM25 for all queries)
  5. Restart the server after changing ov.conf

Approach:
  - Ground truth: scan local benchmark files with Python regex (equivalent to fs engine)
  - Test: call `ov grep` CLI with --output json to get structured results
  - Compare: compute Recall, Precision, F1 per query pattern

NOTE: `remote_return_limit` defaults to 0 (auto-adapt to 100000), so bm25 recall
is not truncated. No need to test different limit values.

Usage:
  python3 step5_retrieval_quality.py [--uri URI] [--case-insensitive] [--output FILE]
"""

import argparse
import json
import os
import re
import shlex
import subprocess
import time

BASE_URI = "viking://resources/benchmark"
OV_CMD = ["ov", "--account", "default", "--user", "default"]
DATA_DIR = os.path.expanduser("~/.openviking/data")
BENCHMARK_DIR = os.path.join(DATA_DIR, "benchmark")

# Test patterns covering different keyword types
# (label, pattern)
TEST_PATTERNS = [
    # CamelCase
    ("CamelCase: VikingDB", "VikingDB"),
    # PascalCase
    ("PascalCase: FullText", "FullText"),
    # lowercase
    ("lowercase: bm25", "bm25"),
    # snake_case
    ("snake_case: search_by_keywords", "search_by_keywords"),
    # Multi-keyword regex
    ("multi: VikingDB|FullText", "VikingDB|FullText"),
    ("multi: VikingDB|FullText|bm25", "VikingDB|FullText|bm25"),
    # No-match
    ("no-match: zzz_nonexistent", "zzz_nonexistent"),
]


def run_ov_grep(uri: str, pattern: str, case_insensitive: bool = False) -> tuple[set[str], float]:
    """Run `ov grep --output json` and extract matched URIs."""
    cmd = OV_CMD + [
        "--output",
        "json",
        "grep",
        "--uri",
        uri,
        "-n",
        "100000",
        pattern,
    ]
    if case_insensitive:
        cmd.insert(cmd.index("grep") + 1, "-i")

    cmd_str = shlex.join(cmd)
    print(f"  $ {cmd_str}")

    t0 = time.monotonic()
    result = subprocess.run(cmd, capture_output=True, text=True, timeout=300)
    elapsed = time.monotonic() - t0

    if result.returncode != 0:
        stderr = result.stderr.strip()[:200]
        raise RuntimeError(f"ov grep failed (exit={result.returncode}): {stderr}")

    # Parse JSON response: {"status": "ok", "result": {"matches": [...], ...}}
    try:
        resp = json.loads(result.stdout)
    except json.JSONDecodeError as e:
        raise RuntimeError(f"Failed to parse ov grep output: {e}") from e

    grep_result = resp.get("result", resp)
    uris = set()
    for match in grep_result.get("matches", []):
        uri = match.get("uri", "")
        if uri:
            uris.add(uri.rstrip("/"))
    return uris, elapsed


def local_path_to_viking_uri(filepath: str) -> str:
    """Convert a local benchmark file path to a viking URI."""
    rel = os.path.relpath(filepath, DATA_DIR)
    return "viking://resources/" + rel.replace(os.sep, "/").rstrip("/")


def compute_ground_truth(pattern: str, case_insensitive: bool = False) -> tuple[set[str], float]:
    """Scan local benchmark files with Python regex to get ground truth."""
    flags = re.IGNORECASE if case_insensitive else 0
    compiled = re.compile(pattern, flags)
    truth_uris = set()
    t0 = time.monotonic()
    for root, dirs, files in os.walk(BENCHMARK_DIR):
        dirs.sort()
        for fname in sorted(files):
            if not fname.endswith(".md"):
                continue
            filepath = os.path.join(root, fname)
            try:
                with open(filepath) as f:
                    content = f.read()
                if compiled.search(content):
                    truth_uris.add(local_path_to_viking_uri(filepath))
            except Exception:
                pass
    elapsed = time.monotonic() - t0
    return truth_uris, elapsed


def compute_metrics(truth: set[str], predicted: set[str]) -> dict:
    """Compute recall, precision, F1."""
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
    parser = argparse.ArgumentParser(description="Step 5: Retrieval quality evaluation")
    parser.add_argument("--uri", default=BASE_URI, help=f"Base URI to search (default: {BASE_URI})")
    parser.add_argument("--case-insensitive", action="store_true", help="Case-insensitive matching")
    parser.add_argument(
        "--output", default=None, help="Output JSON file path (default: print to stdout only)"
    )
    args = parser.parse_args()

    if not os.path.isdir(BENCHMARK_DIR):
        print(f"Error: Benchmark data not found at {BENCHMARK_DIR}")
        print("Run step1_generate.py first.")
        return

    print("=" * 100)
    print("Retrieval Quality Evaluation: auto (bm25+fs) vs local fs (ground truth)")
    print("=" * 100)
    print(f"URI:              {args.uri}")
    print(f"Case insensitive: {args.case_insensitive}")
    print(f"Data dir:         {BENCHMARK_DIR}")
    print()
    print("Ensure ov.conf has:")
    print('  "grep": {"engine": "auto", "switch_to_remote_threshold": 0}')
    print("And the server has been restarted.")
    print()

    results = []

    for label, pattern in TEST_PATTERNS:
        print(f"--- {label} (pattern: {pattern}) ---")

        # Ground truth: scan local files
        truth_uris, fs_elapsed = compute_ground_truth(pattern, args.case_insensitive)
        print(f"  Ground truth (local fs): {len(truth_uris)} matches ({fs_elapsed:.2f}s)")

        # Auto grep (via ov CLI with --output json)
        try:
            auto_uris, auto_elapsed = run_ov_grep(args.uri, pattern, args.case_insensitive)
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

        # Compute metrics
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

        # Show sample missed URIs for debugging
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

    # Summary table
    print()
    print("=" * 110)
    print(
        f"{'Label':<40} {'Truth':>6} {'Auto':>6} {'Recall':>8} {'Prec':>8} {'F1':>8} {'Missed':>8}"
    )
    print("-" * 110)
    for r in results:
        if "error" in r:
            print(
                f"{r['label']:<40} {r['truth_count']:>6} {'ERR':>6} "
                f"{'---':>8} {'---':>8} {'---':>8} {'---':>8}"
            )
        else:
            print(
                f"{r['label']:<40} {r['truth_count']:>6} {r['auto_count']:>6} "
                f"{r['recall']:>8.4f} {r['precision']:>8.4f} {r['f1']:>8.4f} {r['fn']:>8}"
            )
    print()

    # Verdict
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

    # Save results
    if args.output:
        with open(args.output, "w") as f:
            json.dump(results, f, indent=2)
        print(f"Results saved to {args.output}")


if __name__ == "__main__":
    main()
