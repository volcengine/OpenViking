#!/usr/bin/env python3
"""Run the full LongMemEval VikingBot pipeline with Python only."""

from __future__ import annotations

import argparse
import subprocess
import sys
import time
from pathlib import Path

DEFAULT_INPUT_FILE = "/Users/bytedance/mempalace/data/longmemeval-data/longmemeval_s_cleaned.json"
DEFAULT_OUTPUT_FILE = "./result/longmemeval_result.csv"


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Run the full LongMemEval VikingBot benchmark")
    parser.add_argument(
        "--input",
        default=DEFAULT_INPUT_FILE,
        help=f"Path to LongMemEval JSON file, default: {DEFAULT_INPUT_FILE}",
    )
    parser.add_argument(
        "--output",
        default=DEFAULT_OUTPUT_FILE,
        help=f"Path to output CSV, default: {DEFAULT_OUTPUT_FILE}",
    )
    parser.add_argument(
        "--skip-import",
        action="store_true",
        help="Skip import step and start from evaluation.",
    )
    parser.add_argument(
        "--threads",
        type=int,
        default=20,
        help="Worker count for run_eval.py, default: 20",
    )
    parser.add_argument(
        "--judge-parallel",
        type=int,
        default=10,
        help="Parallel request count for judge.py, default: 10",
    )
    parser.add_argument(
        "--wait-seconds",
        type=float,
        default=180,
        help="Seconds to wait between major phases, default: 180",
    )
    parser.add_argument(
        "--eval-timeout",
        type=int,
        default=300,
        help="Per-question timeout in seconds for run_eval.py, default: 300",
    )
    return parser.parse_args()


def build_steps(
    *,
    python_executable: str,
    input_path: str,
    output_path: str,
    skip_import: bool,
    threads: int = 20,
    judge_parallel: int = 10,
    eval_timeout: int = 300,
) -> list[dict]:
    steps: list[dict] = []
    if not skip_import:
        steps.append(
            {
                "name": "import",
                "cmd": [
                    python_executable,
                    "benchmark/longmemeval/vikingbot/import_to_ov.py",
                    "--input",
                    input_path,
                    "--force-ingest",
                ],
            }
        )

    steps.extend(
        [
            {
                "name": "eval",
                "cmd": [
                    python_executable,
                    "benchmark/longmemeval/vikingbot/run_eval.py",
                    input_path,
                    "--output",
                    output_path,
                    "--threads",
                    str(threads),
                    "--timeout",
                    str(eval_timeout),
                ],
            },
            {
                "name": "judge",
                "cmd": [
                    python_executable,
                    "benchmark/longmemeval/vikingbot/judge.py",
                    "--input",
                    output_path,
                    "--parallel",
                    str(judge_parallel),
                ],
            },
            {
                "name": "stats",
                "cmd": [
                    python_executable,
                    "benchmark/longmemeval/vikingbot/stat_judge_result.py",
                    "--input",
                    output_path,
                ],
            },
        ]
    )
    return steps


def run_step(step: dict) -> None:
    subprocess.run(step["cmd"], check=True)


def main() -> int:
    args = parse_args()
    input_file = Path(args.input).expanduser()
    if not input_file.exists():
        print(f"Error: input file not found: {input_file}", file=sys.stderr)
        return 1

    steps = build_steps(
        python_executable=sys.executable,
        input_path=str(input_file),
        output_path=args.output,
        skip_import=args.skip_import,
        threads=args.threads,
        judge_parallel=args.judge_parallel,
        eval_timeout=args.eval_timeout,
    )

    total_steps = len(steps)
    for index, step in enumerate(steps, start=1):
        print(f"[{index}/{total_steps}] {step['name']}...")
        run_step(step)
        if index != total_steps and args.wait_seconds > 0:
            print(f"Waiting {args.wait_seconds:.0f}s...")
            time.sleep(args.wait_seconds)

    print("Done!")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
