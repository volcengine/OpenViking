#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
from collections import defaultdict
from pathlib import Path
from statistics import mean
from typing import Any

from benchmark.memory_organization.autonomous_cases import build_cases
from benchmark.memory_organization.autonomous_grader import (
    autonomous_metrics,
    grade_autonomous_result,
)
from benchmark.memory_organization.grader import mcnemar_exact_p_value

PRIMARY_METRICS = ("organization_action_success", "information_integrity")
CASE_ID_ALIASES = {"oversized_preference_splits_into_two": "oversized_preference_splits"}


def regrade_rows(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    cases = {case.case_id: case for case in build_cases()}
    regraded = []
    for row in rows:
        row_case_id = str(row["case_id"])
        case = cases.get(CASE_ID_ALIASES.get(row_case_id, row_case_id))
        if case is None:
            regraded.append(row)
            continue
        files = {
            uri: (str(item["memory_type"]), str(item["content"]))
            for uri, item in row["actual_files"].items()
        }
        replacements = {str(key): str(value) for key, value in row["actual_replacements"].items()}
        grade = grade_autonomous_result(case, files, replacements)
        regraded.append(
            {
                **row,
                "case_id": case.case_id,
                "grade": grade.to_dict(),
                "autonomous_metrics": autonomous_metrics(case, files, replacements, grade),
            }
        )
    return regraded


def summarize_autonomous(rows: list[dict[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for case_id in sorted({str(row["case_id"]) for row in rows}):
        case_rows = [row for row in rows if row["case_id"] == case_id]
        protocol_summary = {}
        for protocol in ("json", "python"):
            protocol_rows = [row for row in case_rows if row["protocol"] == protocol]
            if not protocol_rows:
                continue
            protocol_summary[protocol] = {
                "runs": len(protocol_rows),
                "organization_action_success_rate": mean(
                    row["autonomous_metrics"]["organization_action_success"]
                    for row in protocol_rows
                ),
                "information_integrity_rate": mean(
                    row["autonomous_metrics"]["information_integrity"] for row in protocol_rows
                ),
            }
        result[case_id] = {
            "summary": protocol_summary,
            "paired": {metric: _paired_metric(case_rows, metric) for metric in PRIMARY_METRICS},
        }
    return result


def _paired_metric(rows: list[dict[str, Any]], metric: str) -> dict[str, int | float]:
    pairs: dict[int, dict[str, bool]] = defaultdict(dict)
    for row in rows:
        pairs[int(row["repeat_index"])][str(row["protocol"])] = bool(
            row["autonomous_metrics"][metric]
        )
    counts = {"both_success": 0, "python_only": 0, "json_only": 0, "both_fail": 0}
    for pair in pairs.values():
        if set(pair) != {"json", "python"}:
            continue
        python_success = pair["python"]
        json_success = pair["json"]
        key = (
            "both_success"
            if python_success and json_success
            else "python_only"
            if python_success
            else "json_only"
            if json_success
            else "both_fail"
        )
        counts[key] += 1
    return {**counts, "mcnemar_exact_p_value": mcnemar_exact_p_value(counts)}


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("inputs", nargs="+")
    parser.add_argument("--output")
    args = parser.parse_args()
    rows = regrade_rows(
        [
            json.loads(line)
            for input_path in args.inputs
            for line in Path(input_path).read_text().splitlines()
            if line.strip()
        ]
    )
    rendered = json.dumps(summarize_autonomous(rows), ensure_ascii=False, indent=2)
    if args.output:
        output = Path(args.output)
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_text(rendered + "\n")
    print(rendered)


if __name__ == "__main__":
    main()
