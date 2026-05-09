#!/usr/bin/env python3
"""Import one LongMemEval sample into OpenViking and run one evaluation question."""

from __future__ import annotations

import argparse
import subprocess
import sys
import time
from pathlib import Path

DEFAULT_INPUT_FILE = "data/longmemeval_s_cleaned.json"


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Import one LongMemEval sample and evaluate it.",
    )
    parser.add_argument(
        "sample",
        type=int,
        help="Sample index (0,1,2...).",
    )
    parser.add_argument(
        "--input",
        default=str(DEFAULT_INPUT_FILE),
        help=f"Input dataset JSON path, default: {DEFAULT_INPUT_FILE}",
    )
    parser.add_argument(
        "--wait-seconds",
        default=3,
        type=float,
        help="Seconds to wait after import before evaluation, default 3.",
    )
    parser.add_argument(
        "--force-ingest",
        action="store_true",
        help="Force re-import even if the sample was already ingested.",
    )
    return parser.parse_args()


def run_command(cmd: list[str]) -> None:
    subprocess.run(cmd, check=True)


def main() -> int:
    args = parse_args()
    input_file = Path(args.input).expanduser()
    if not input_file.exists():
        print(f"Error: input file not found: {input_file}", file=sys.stderr)
        return 1

    import_cmd = [
        sys.executable,
        "benchmark/longmemeval/vikingbot/import_to_ov.py",
        "--input",
        str(input_file),
        "--sample",
        str(args.sample),
    ]
    if args.force_ingest:
        import_cmd.append("--force-ingest")

    print(f"[1/2] Importing sample {args.sample}...")
    run_command(import_cmd)

    print("Waiting for data processing...")
    time.sleep(args.wait_seconds)

    print("[2/2] Running evaluation...")
    run_command(
        [
            sys.executable,
            "benchmark/longmemeval/vikingbot/run_eval.py",
            str(input_file),
            "--sample",
            str(args.sample),
            "--count",
            "1",
        ]
    )

    print("Done!")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
