#!/usr/bin/env python3

from __future__ import annotations

import argparse
import csv
from collections import Counter


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Summarize per-sample stability across repeated eval CSVs")
    parser.add_argument(
        "--inputs",
        nargs="+",
        required=True,
        help="Judge result CSV files from repeated runs",
    )
    parser.add_argument(
        "--output",
        default="",
        help="Optional output CSV for per-sample stability summary",
    )
    parser.add_argument(
        "--output-json",
        default="",
        help="Optional output JSON for grouped sample IDs by wrong_count/correct_count",
    )
    return parser.parse_args()


def main() -> int:
    args = parse_args()

    sample_map: dict[str, dict] = {}
    run_names = [f"run{i}" for i in range(1, len(args.inputs) + 1)]

    for run_idx, path in enumerate(args.inputs, start=1):
        with open(path, "r", encoding="utf-8", newline="") as f:
            reader = csv.DictReader(f)
            for row in reader:
                sample_id = row.get("sample_id", "")
                if not sample_id:
                    continue
                entry = sample_map.setdefault(
                    sample_id,
                    {
                        "sample_id": sample_id,
                        "question_type": row.get("question_type", ""),
                        "question": row.get("question", ""),
                        "correct_count": 0,
                        "wrong_count": 0,
                        "other_count": 0,
                        "stability": "",
                    },
                )
                result = row.get("result", "").strip().upper()
                if result == "CORRECT":
                    entry["correct_count"] += 1
                elif result == "WRONG":
                    entry["wrong_count"] += 1
                else:
                    entry["other_count"] += 1
                entry[f"run{run_idx}_result"] = result or "MISSING"

    rows: list[dict] = []
    stability_counter: Counter[str] = Counter()
    question_type_counter: dict[str, Counter[str]] = {}
    wrong_groups: dict[int, list[str]] = {}
    correct_groups: dict[int, list[str]] = {}
    run_total = len(args.inputs)

    for sample_id in sorted(sample_map):
        entry = sample_map[sample_id]
        correct_count = entry["correct_count"]
        wrong_count = entry["wrong_count"]
        other_count = entry["other_count"]
        if correct_count == run_total:
            stability = "stable_correct"
        elif wrong_count == run_total:
            stability = "stable_wrong"
        else:
            stability = "flaky"
        if other_count > 0:
            stability = f"{stability}_with_other"
        entry["stability"] = stability
        entry["accuracy_ratio"] = f"{correct_count}/{run_total}"
        rows.append(entry)
        stability_counter[stability] += 1
        wrong_groups.setdefault(wrong_count, []).append(sample_id)
        correct_groups.setdefault(correct_count, []).append(sample_id)
        question_type = entry["question_type"] or "<missing>"
        question_type_counter.setdefault(question_type, Counter())[stability] += 1

    print("=== Stability Summary ===")
    print(f"Runs: {run_total}")
    print(f"Samples: {len(rows)}")
    for key in sorted(stability_counter):
        print(f"{key}: {stability_counter[key]}")

    print("\n=== Stability By Question Type ===")
    print(
        f"{'question_type':<28} {'stable_correct':>14} {'stable_wrong':>12} {'flaky':>8} {'other':>8}"
    )
    for question_type in sorted(question_type_counter):
        counter = question_type_counter[question_type]
        other_count = sum(
            count for name, count in counter.items() if name not in {"stable_correct", "stable_wrong", "flaky"}
        )
        print(
            f"{question_type:<28} "
            f"{counter.get('stable_correct', 0):>14} "
            f"{counter.get('stable_wrong', 0):>12} "
            f"{counter.get('flaky', 0):>8} "
            f"{other_count:>8}"
        )

    print("\n=== Wrong Count Groups ===")
    for wrong_count in range(run_total + 1):
        sample_ids = wrong_groups.get(wrong_count, [])
        if not sample_ids:
            continue
        print(f"\n错 {wrong_count} 次的样本数: {len(sample_ids)}")
        print(f"题号: {', '.join(sorted(sample_ids))}")

    print("\n=== Correct Count Groups ===")
    for correct_count in range(run_total + 1):
        sample_ids = correct_groups.get(correct_count, [])
        if not sample_ids:
            continue
        print(f"\n对 {correct_count} 次的样本数: {len(sample_ids)}")
        print(f"题号: {', '.join(sorted(sample_ids))}")

    if args.output:
        fieldnames = [
            "sample_id",
            "question_type",
            "question",
            "correct_count",
            "wrong_count",
            "other_count",
            "accuracy_ratio",
            "stability",
            *run_names,
        ]
        with open(args.output, "w", encoding="utf-8", newline="") as f:
            writer = csv.DictWriter(f, fieldnames=fieldnames)
            writer.writeheader()
            for row in rows:
                writer.writerow({key: row.get(key, row.get(f"{key}_result", "")) for key in fieldnames})
        print(f"\nSaved per-sample stability to {args.output}")

    if args.output_json:
        import json

        payload = {
            "run_total": run_total,
            "sample_total": len(rows),
            "stability_counts": dict(sorted(stability_counter.items())),
            "wrong_groups": {
                str(wrong_count): sorted(sample_ids)
                for wrong_count, sample_ids in sorted(wrong_groups.items())
            },
            "correct_groups": {
                str(correct_count): sorted(sample_ids)
                for correct_count, sample_ids in sorted(correct_groups.items())
            },
        }
        with open(args.output_json, "w", encoding="utf-8") as f:
            json.dump(payload, f, ensure_ascii=False, indent=2)
        print(f"Saved grouped counts to {args.output_json}")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
