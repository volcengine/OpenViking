#!/usr/bin/env python3
# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0
"""Compare two privacy-safe Codex auto-recall benchmark reports."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

LOWER_IS_BETTER = (
    "false_injection_rate",
    "latency_ms_p50",
    "latency_ms_p95",
    "injection_tokens_p95",
)
HIGHER_IS_BETTER = ("positive_recall_rate",)


def load_report(path: Path) -> dict[str, Any]:
    try:
        report = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ValueError(f"invalid report: {path}") from exc
    if report.get("schema_version") != 1 or not isinstance(report.get("summary"), dict):
        raise ValueError(f"unsupported report schema: {path}")
    return report


def compare_reports(
    baseline: dict[str, Any],
    candidate: dict[str, Any],
    *,
    latency_ratio: float,
    latency_jitter_ms: float,
) -> dict[str, Any]:
    if baseline.get("fixture_sha256") != candidate.get("fixture_sha256"):
        raise ValueError("baseline and candidate used different fixtures")
    if baseline.get("protocol") != candidate.get("protocol"):
        raise ValueError("baseline and candidate used different benchmark protocols")
    if baseline["summary"].get("tokenizer") != candidate["summary"].get("tokenizer"):
        raise ValueError("baseline and candidate used different tokenizers")

    base = baseline["summary"]
    new = candidate["summary"]
    failures: list[str] = []
    deltas: dict[str, float] = {}
    for metric in LOWER_IS_BETTER + HIGHER_IS_BETTER:
        deltas[metric] = round(float(new[metric]) - float(base[metric]), 4)

    if new["false_injection_rate"] > base["false_injection_rate"]:
        failures.append("false_injection_rate")
    if new["positive_recall_rate"] < base["positive_recall_rate"]:
        failures.append("positive_recall_rate")
    if new["injection_tokens_p95"] > base["injection_tokens_p95"]:
        failures.append("injection_tokens_p95")
    for metric in ("latency_ms_p50", "latency_ms_p95"):
        allowed = float(base[metric]) * (1 + latency_ratio) + latency_jitter_ms
        if float(new[metric]) > allowed:
            failures.append(metric)

    return {
        "baseline_label": baseline.get("label", "baseline"),
        "candidate_label": candidate.get("label", "candidate"),
        "deltas": deltas,
        "non_regression_passed": not failures,
        "failures": failures,
    }


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--baseline", type=Path, required=True)
    parser.add_argument("--candidate", type=Path, required=True)
    parser.add_argument("--max-latency-regression-ratio", type=float, default=0.05)
    parser.add_argument("--latency-jitter-ms", type=float, default=25.0)
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    if args.max_latency_regression_ratio < 0 or args.latency_jitter_ms < 0:
        raise ValueError("latency tolerances must be non-negative")
    result = compare_reports(
        load_report(args.baseline),
        load_report(args.candidate),
        latency_ratio=args.max_latency_regression_ratio,
        latency_jitter_ms=args.latency_jitter_ms,
    )
    print(json.dumps(result, ensure_ascii=False, sort_keys=True))
    return 0 if result["non_regression_passed"] else 1


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except ValueError as exc:
        print(f"Codex recall comparison failed: {exc}", file=sys.stderr)
        raise SystemExit(2) from exc
