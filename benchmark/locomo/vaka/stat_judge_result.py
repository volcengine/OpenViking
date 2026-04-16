from __future__ import annotations

import argparse
import csv
import os
from collections import defaultdict
from pathlib import Path


SCRIPT_DIR = Path(__file__).parent.resolve()
DEFAULT_INPUT = str(SCRIPT_DIR / "result" / "vaka_qa_result.csv")


def _as_float(value: str | None) -> float | None:
    text = (value or "").strip()
    if not text:
        return None
    try:
        return float(text)
    except ValueError:
        return None


def _is_pass(value: str | None) -> bool:
    return (value or "").strip().lower() in {"1", "true", "yes", "pass", "correct"}


def main() -> None:
    parser = argparse.ArgumentParser(description="Statistics for Vaka judge result CSV")
    parser.add_argument(
        "--input",
        default=DEFAULT_INPUT,
        help=f"Path to judge result CSV file, default: {DEFAULT_INPUT}",
    )
    args = parser.parse_args()

    if not os.path.exists(args.input):
        print(f"Error: File not found: {args.input}")
        raise SystemExit(1)

    with open(args.input, "r", encoding="utf-8", newline="") as f:
        rows = list(csv.DictReader(f))

    correct = 0
    wrong = 0
    ungraded = 0
    latencies: list[float] = []
    source_cmdfollow_total = 0
    source_cmdfollow_pass = 0
    by_session: dict[str, dict[str, int]] = defaultdict(lambda: defaultdict(int))
    by_case: dict[str, dict[str, int]] = defaultdict(lambda: defaultdict(int))

    for row in rows:
        result = (row.get("result") or "").strip().upper()
        case_id = row.get("case_id") or "unknown"
        local_session_id = row.get("local_session_id") or "unknown"

        if result == "CORRECT":
            correct += 1
            by_case[case_id]["correct"] += 1
            by_session[local_session_id]["correct"] += 1
        elif result == "WRONG":
            wrong += 1
            by_case[case_id]["wrong"] += 1
            by_session[local_session_id]["wrong"] += 1
        else:
            ungraded += 1
            by_case[case_id]["ungraded"] += 1
            by_session[local_session_id]["ungraded"] += 1

        latency = _as_float(row.get("latency_time"))
        if latency is not None:
            latencies.append(latency)

        source_cmd = row.get("source_result_cmdfollow_check")
        if source_cmd is not None and str(source_cmd).strip():
            source_cmdfollow_total += 1
            if _is_pass(source_cmd):
                source_cmdfollow_pass += 1

    total = len(rows)
    graded = correct + wrong
    accuracy = correct / graded if graded else 0.0
    avg_latency = sum(latencies) / len(latencies) if latencies else 0.0
    source_cmdfollow_rate = (
        source_cmdfollow_pass / source_cmdfollow_total if source_cmdfollow_total else 0.0
    )

    output_lines = [
        "=== Vaka Judge Result Statistics ===",
        f"Total eval rows: {total}",
        f"Graded rows: {graded}",
        f"Ungraded rows: {ungraded}",
        f"Correct: {correct}",
        f"Wrong: {wrong}",
        f"Accuracy: {accuracy:.2%}",
        f"Average latency_time: {avg_latency:.2f}s",
        "",
        "=== Source command-follow check ===",
        f"Rows with source check: {source_cmdfollow_total}",
        f"Source pass: {source_cmdfollow_pass}",
        f"Source pass rate: {source_cmdfollow_rate:.2%}",
        "",
        "=== By Local Eval Session ===",
    ]

    for session_id in sorted(by_session, key=lambda value: int(value) if value.isdigit() else 999):
        stats = by_session[session_id]
        session_graded = stats["correct"] + stats["wrong"]
        session_accuracy = stats["correct"] / session_graded if session_graded else 0.0
        output_lines.append(
            f"S{session_id}: {stats['correct']}/{session_graded} correct, "
            f"accuracy={session_accuracy:.2%}, ungraded={stats['ungraded']}"
        )

    output_lines.extend(["", "=== By Case ==="])
    for case_id in sorted(by_case):
        stats = by_case[case_id]
        case_graded = stats["correct"] + stats["wrong"]
        case_accuracy = stats["correct"] / case_graded if case_graded else 0.0
        output_lines.append(
            f"{case_id}: {stats['correct']}/{case_graded} correct, "
            f"accuracy={case_accuracy:.2%}, ungraded={stats['ungraded']}"
        )

    for line in output_lines:
        print(line)

    summary_path = os.path.join(os.path.dirname(args.input), "summary.txt")
    with open(summary_path, "w", encoding="utf-8") as f:
        f.write("\n".join(output_lines) + "\n")
    print(f"\nSummary saved to {summary_path}")


if __name__ == "__main__":
    main()
