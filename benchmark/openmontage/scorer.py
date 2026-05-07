#!/usr/bin/env python3
"""Deterministic scorer for the OpenMontage benchmark MVP."""

from __future__ import annotations

import argparse
import json
from pathlib import Path


def normalize_text(value: str) -> str:
    return value.lower().strip()


def score_case(case: dict) -> dict:
    expected_uri_suffix = case["expected_uri_suffix"]
    expected_keywords = [normalize_text(keyword) for keyword in case["expected_keywords"]]
    hits = case.get("hits", [])

    hit_uris = [hit.get("uri", "") for hit in hits]
    combined_evidence = "\n".join(
        [
            hit.get("uri", "")
            + "\n"
            + hit.get("abstract", "")
            + "\n"
            + hit.get("overview", "")
            for hit in hits
        ]
    ).lower()

    uri_match = any(uri.endswith(expected_uri_suffix) for uri in hit_uris)
    keyword_match = all(keyword in combined_evidence for keyword in expected_keywords)
    passed = uri_match and keyword_match

    return {
        "id": case["id"],
        "passed": passed,
        "uri_match": uri_match,
        "keyword_match": keyword_match,
        "expected_uri_suffix": expected_uri_suffix,
        "returned_uris": hit_uris,
    }


def score_report(report: dict) -> dict:
    scored_cases = [score_case(case) for case in report["cases"]]
    passed = sum(1 for case in scored_cases if case["passed"])
    total = len(scored_cases)
    return {
        "project_id": report["project_id"],
        "passed": passed,
        "total": total,
        "score": passed / total if total else 0.0,
        "cases": scored_cases,
    }


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("report", help="Path to run_eval.py output JSON")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    report_path = Path(args.report)
    report = json.loads(report_path.read_text(encoding="utf-8"))
    print(json.dumps(score_report(report), indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
