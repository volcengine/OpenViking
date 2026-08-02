#!/usr/bin/env python3
# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0
"""Run privacy-safe JSONL regressions against the recall admission policy."""

from __future__ import annotations

import argparse
import json
import math
import sys
import time
from pathlib import Path
from typing import Any

import httpx

MEMORY_TYPES = ("events", "entities", "preferences", "experiences")


def parse_type_score(value: str) -> tuple[str, float]:
    try:
        memory_type, raw_score = value.split("=", 1)
        score = float(raw_score)
    except ValueError as exc:
        raise argparse.ArgumentTypeError("expected TYPE=SCORE") from exc
    if memory_type not in MEMORY_TYPES:
        raise argparse.ArgumentTypeError(f"unknown memory type: {memory_type}")
    if not math.isfinite(score) or not 0.0 <= score <= 1.0:
        raise argparse.ArgumentTypeError("score must be between 0 and 1")
    return memory_type, score


def load_cases(path: Path) -> list[dict[str, str]]:
    cases: list[dict[str, str]] = []
    for line_number, raw_line in enumerate(path.read_text(encoding="utf-8").splitlines(), start=1):
        if not raw_line.strip():
            continue
        try:
            value = json.loads(raw_line)
        except json.JSONDecodeError as exc:
            raise ValueError(f"line {line_number}: invalid JSON") from exc
        case_id = str(value.get("id", "")).strip()
        query = str(value.get("query", "")).strip()
        expected = str(value.get("expected", "")).strip().lower()
        if not case_id or not query or expected not in {"accept", "abstain"}:
            raise ValueError(
                f"line {line_number}: id, query, and expected=accept|abstain are required"
            )
        cases.append({"id": case_id, "query": query, "expected": expected})
    if not cases:
        raise ValueError("input contains no cases")
    return cases


def percentile(values: list[float], fraction: float) -> float:
    if not values:
        return 0.0
    ordered = sorted(values)
    index = min(len(ordered) - 1, math.ceil(len(ordered) * fraction) - 1)
    return round(ordered[max(0, index)], 2)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--input", type=Path, required=True, help="JSONL regression cases")
    parser.add_argument("--url", default="http://127.0.0.1:1933", help="OpenViking base URL")
    parser.add_argument("--api-key", default="", help="optional API key; never printed")
    parser.add_argument("--actor-peer", default="", help="optional actor peer header")
    parser.add_argument("--timeout", type=float, default=30.0)
    parser.add_argument("--admission-mode", choices=("shadow", "enforce"), default="shadow")
    parser.add_argument(
        "--type-min-score",
        action="append",
        default=[],
        type=parse_type_score,
        metavar="TYPE=SCORE",
    )
    parser.add_argument("--other-peer-score-delta", type=float, default=0.0)
    parser.add_argument("--min-score", type=float, default=0.35)
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    if not 0.0 <= args.other_peer_score_delta <= 1.0:
        raise ValueError("--other-peer-score-delta must be between 0 and 1")
    if not 0.0 <= args.min_score <= 1.0:
        raise ValueError("--min-score must be between 0 and 1")

    cases = load_cases(args.input)
    type_scores = dict(args.type_min_score)
    headers = {"Content-Type": "application/json"}
    if args.api_key:
        headers["Authorization"] = f"Bearer {args.api_key}"
    if args.actor_peer:
        headers["X-OpenViking-Actor-Peer"] = args.actor_peer

    records: list[dict[str, Any]] = []
    endpoint = f"{args.url.rstrip('/')}/api/v1/search/recall"
    with httpx.Client(timeout=args.timeout, headers=headers) as client:
        for case in cases:
            started = time.perf_counter()
            response = client.post(
                endpoint,
                json={
                    "query": case["query"],
                    "min_score": args.min_score,
                    "render": False,
                    "admission": {
                        "mode": args.admission_mode,
                        "type_min_scores": type_scores,
                        "other_peer_score_delta": args.other_peer_score_delta,
                    },
                },
            )
            elapsed_ms = round((time.perf_counter() - started) * 1000, 2)
            response.raise_for_status()
            result = response.json().get("result", {})
            stats = result.get("stats", {})
            admission = stats.get("admission", {})
            if admission.get("mode") != args.admission_mode:
                raise ValueError(
                    "server response does not contain the requested admission telemetry"
                )
            abstained = bool(
                admission.get("would_abstain")
                if args.admission_mode == "shadow"
                else admission.get("abstained")
            )
            actual = "abstain" if abstained else "accept"
            record = {
                "id": case["id"],
                "expected": case["expected"],
                "actual": actual,
                "passed": actual == case["expected"],
                "latency_ms": elapsed_ms,
                "returned": int(stats.get("returned", 0)),
                "evaluated": int(admission.get("evaluated", 0)),
                "rejected": int(admission.get("rejected", 0)),
                "reason_counts": admission.get("reason_counts", {}),
            }
            records.append(record)
            print(json.dumps(record, ensure_ascii=False, sort_keys=True))

    negative = [record for record in records if record["expected"] == "abstain"]
    positive = [record for record in records if record["expected"] == "accept"]
    latencies = [float(record["latency_ms"]) for record in records]
    summary = {
        "summary": {
            "cases": len(records),
            "passed": sum(bool(record["passed"]) for record in records),
            "false_injection_rate": round(
                sum(record["actual"] == "accept" for record in negative) / len(negative), 4
            )
            if negative
            else 0.0,
            "missed_recall_rate": round(
                sum(record["actual"] == "abstain" for record in positive) / len(positive), 4
            )
            if positive
            else 0.0,
            "latency_ms_p50": percentile(latencies, 0.5),
            "latency_ms_p95": percentile(latencies, 0.95),
        }
    }
    print(json.dumps(summary, ensure_ascii=False, sort_keys=True))
    return 0 if all(record["passed"] for record in records) else 1


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except (ValueError, httpx.HTTPError) as exc:
        print(f"recall admission regression failed: {exc}", file=sys.stderr)
        raise SystemExit(2) from exc
