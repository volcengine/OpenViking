#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
from pathlib import Path

from benchmark.memory_organization.grader import mcnemar_exact_p_value, paired_counts, summarize


def paired_report(rows: list[dict], *, metric: str) -> dict:
    counts = paired_counts(rows, metric=metric)
    return {**counts, "mcnemar_exact_p_value": mcnemar_exact_p_value(counts)}


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("input")
    parser.add_argument("--output", help="Optional path for the JSON summary")
    args = parser.parse_args()
    rows = [json.loads(line) for line in Path(args.input).read_text().splitlines() if line.strip()]
    categories = sorted({str(row["category"]) for row in rows})
    result = {
        "summary": summarize(rows),
        "paired": paired_report(rows, metric="organization_success"),
        "paired_content": paired_report(rows, metric="content_organization_success"),
        "by_category": {
            category: {
                "summary": summarize([row for row in rows if str(row["category"]) == category]),
                "paired": paired_report(
                    [row for row in rows if str(row["category"]) == category],
                    metric="organization_success",
                ),
                "paired_content": paired_report(
                    [row for row in rows if str(row["category"]) == category],
                    metric="content_organization_success",
                ),
            }
            for category in categories
        },
    }
    rendered = json.dumps(result, ensure_ascii=False, indent=2)
    if args.output:
        output = Path(args.output)
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_text(rendered + "\n")
    print(rendered)


if __name__ == "__main__":
    main()
