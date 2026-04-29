#!/usr/bin/env python3

from __future__ import annotations

import argparse
import csv
import json
import subprocess
import sys
from pathlib import Path
from typing import Any


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Repeat LongMemEval eval/judge/stat multiple times")
    parser.add_argument(
        "--input",
        required=True,
        help="Path to input JSON/CSV for run_eval.py",
    )
    parser.add_argument(
        "--output-prefix",
        required=True,
        help="Prefix for per-run csv outputs, e.g. result/longmemeval_baseline_dense_sparse_v2",
    )
    parser.add_argument(
        "--runs",
        type=int,
        default=5,
        help="How many repeated runs to execute, default: 5",
    )
    parser.add_argument(
        "--threads",
        type=int,
        default=4,
        help="Worker count for run_eval.py, default: 4",
    )
    parser.add_argument(
        "--timeout",
        type=int,
        default=300,
        help="Per-question timeout in seconds for run_eval.py, default: 300",
    )
    parser.add_argument(
        "--judge-parallel",
        type=int,
        default=5,
        help="Parallel request count for judge.py, default: 5",
    )
    parser.add_argument(
        "--answer-mode",
        choices=["single-search-context"],
        default="single-search-context",
        help="Answer generation mode passed through to run_eval.py, default: single-search-context",
    )
    return parser.parse_args()


def run_cmd(cmd: list[str]) -> None:
    print("+", " ".join(cmd), flush=True)
    subprocess.run(cmd, check=True)


def _safe_float(value: Any) -> float:
    try:
        return float(value or 0)
    except (TypeError, ValueError):
        return 0.0


def _safe_int(value: Any) -> int:
    try:
        return int(float(value or 0))
    except (TypeError, ValueError):
        return 0


def summarize_output_csv(output_csv: str | Path, run_index: int) -> dict[str, Any]:
    rows = 0
    correct = 0
    wrong = 0
    total_time = 0.0
    total_iteration = 0
    total_prompt_tokens = 0
    total_memory_prompt_tokens = 0
    total_completion_tokens = 0
    total_tokens = 0

    with open(output_csv, "r", encoding="utf-8", newline="") as f:
        reader = csv.DictReader(f)
        for row in reader:
            rows += 1
            result = (row.get("result") or "").strip().upper()
            if result == "CORRECT":
                correct += 1
            elif result == "WRONG":
                wrong += 1

            total_time += _safe_float(row.get("time_cost"))
            total_iteration += _safe_int(row.get("iteration"))

            token_usage = row.get("token_usage") or ""
            if token_usage.strip():
                try:
                    token_data = json.loads(token_usage)
                except json.JSONDecodeError:
                    token_data = {}
                total_prompt_tokens += _safe_int(token_data.get("prompt_tokens"))
                total_memory_prompt_tokens += _safe_int(
                    token_data.get("memory_prompt_tokens")
                )
                total_completion_tokens += _safe_int(token_data.get("completion_tokens"))
                total_tokens += _safe_int(token_data.get("total_tokens"))

    graded = correct + wrong
    return {
        "run": run_index,
        "rows": rows,
        "graded": graded,
        "correct": correct,
        "wrong": wrong,
        "accuracy": correct / graded if graded else 0.0,
        "avg_time": total_time / rows if rows else 0.0,
        "avg_iteration": total_iteration / rows if rows else 0.0,
        "total_prompt_tokens": total_prompt_tokens,
        "total_memory_prompt_tokens": total_memory_prompt_tokens,
        "total_completion_tokens": total_completion_tokens,
        "total_tokens": total_tokens,
        "avg_prompt_tokens_per_row": total_prompt_tokens / rows if rows else 0.0,
        "avg_memory_prompt_tokens_per_row": total_memory_prompt_tokens / rows
        if rows
        else 0.0,
        "avg_completion_tokens_per_row": total_completion_tokens / rows if rows else 0.0,
        "avg_total_tokens_per_row": total_tokens / rows if rows else 0.0,
    }


def print_run_summary(summary: dict[str, Any]) -> None:
    print(
        "Run {run} summary: rows={rows}, graded={graded}, "
        "accuracy={accuracy:.2%}, avg_time={avg_time:.2f}s, "
        "avg_iteration={avg_iteration:.2f}, total_tokens={total_tokens}, "
        "avg_total_tokens/row={avg_total_tokens_per_row:.1f}, "
        "avg_prompt_tokens/row={avg_prompt_tokens_per_row:.1f}, "
        "avg_memory_prompt_tokens/row={avg_memory_prompt_tokens_per_row:.1f}".format(
            **summary
        ),
        flush=True,
    )


def average_summaries(summaries: list[dict[str, Any]]) -> dict[str, Any]:
    if not summaries:
        return {}

    count = len(summaries)
    numeric_keys = [
        "rows",
        "graded",
        "correct",
        "wrong",
        "accuracy",
        "avg_time",
        "avg_iteration",
        "total_prompt_tokens",
        "total_memory_prompt_tokens",
        "total_completion_tokens",
        "total_tokens",
        "avg_prompt_tokens_per_row",
        "avg_memory_prompt_tokens_per_row",
        "avg_completion_tokens_per_row",
        "avg_total_tokens_per_row",
    ]
    return {
        key: sum(float(summary.get(key, 0) or 0) for summary in summaries) / count
        for key in numeric_keys
    }


def print_repeat_summary(summaries: list[dict[str, Any]]) -> None:
    if not summaries:
        return

    avg = average_summaries(summaries)
    print("\n=== Repeat Eval Summary ===", flush=True)
    print(f"Runs: {len(summaries)}", flush=True)
    print(f"Average accuracy: {avg['accuracy']:.2%}", flush=True)
    print(f"Average correct/run: {avg['correct']:.1f}", flush=True)
    print(f"Average wrong/run: {avg['wrong']:.1f}", flush=True)
    print(f"Average time/row: {avg['avg_time']:.2f}s", flush=True)
    print(f"Average iteration/row: {avg['avg_iteration']:.2f}", flush=True)
    print(f"Average total tokens/run: {avg['total_tokens']:.1f}", flush=True)
    print(f"Average prompt tokens/run: {avg['total_prompt_tokens']:.1f}", flush=True)
    print(
        f"Average memory prompt tokens/run: {avg['total_memory_prompt_tokens']:.1f}",
        flush=True,
    )
    print(f"Average completion tokens/run: {avg['total_completion_tokens']:.1f}", flush=True)
    print(f"Average total tokens/row: {avg['avg_total_tokens_per_row']:.1f}", flush=True)
    print(f"Average prompt tokens/row: {avg['avg_prompt_tokens_per_row']:.1f}", flush=True)
    print(
        "Average memory prompt tokens/row: "
        f"{avg['avg_memory_prompt_tokens_per_row']:.1f}",
        flush=True,
    )
    print(
        f"Average completion tokens/row: {avg['avg_completion_tokens_per_row']:.1f}",
        flush=True,
    )


def main() -> int:
    args = parse_args()
    input_path = str(Path(args.input).expanduser())
    output_prefix = str(Path(args.output_prefix))
    summaries: list[dict[str, Any]] = []

    for index in range(1, args.runs + 1):
        output_csv = f"{output_prefix}.run{index}.csv"
        run_cmd(
            [
                sys.executable,
                "benchmark/longmemeval/vikingbot/run_eval.py",
                input_path,
                "--output",
                output_csv,
                "--threads",
                str(args.threads),
                "--timeout",
                str(args.timeout),
                "--answer-mode",
                args.answer_mode,
            ]
        )
        run_cmd(
            [
                sys.executable,
                "benchmark/longmemeval/vikingbot/judge.py",
                "--input",
                output_csv,
                "--parallel",
                str(args.judge_parallel),
            ]
        )
        run_cmd(
            [
                sys.executable,
                "benchmark/longmemeval/vikingbot/stat_judge_result.py",
                "--input",
                output_csv,
            ]
        )
        summary = summarize_output_csv(output_csv, run_index=index)
        summaries.append(summary)
        print_run_summary(summary)

    print_repeat_summary(summaries)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
