#!/usr/bin/env python3
"""CLI for tau2 batch policy train/eval."""

from __future__ import annotations

import argparse
import asyncio
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[3]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Run tau2 batch policy train/eval")
    parser.add_argument("--domain", default="airline", help="Tau2 domain. Default: airline")
    parser.add_argument("--epochs", type=int, default=1, help="Training epochs (default: 1)")
    parser.add_argument(
        "--batch-size",
        type=int,
        default=None,
        help="Train/eval batch size. Default uses the whole split as one batch.",
    )
    parser.add_argument(
        "--concurrency",
        type=int,
        default=20,
        help="Concurrent tau2 rollouts for train and eval (default: 20)",
    )
    parser.add_argument("--config", default=None, help="ov.conf path (optional)")
    parser.add_argument("--output", default=None, help="JSON report output path")
    parser.add_argument(
        "--data-root",
        default=None,
        help="Tau2 data root. Defaults to TAU2_DATA_ROOT",
    )
    parser.add_argument(
        "--max-iterations",
        type=int,
        default=30,
        help="VikingBot max tool iterations per rollout (default: 30)",
    )
    return parser.parse_args()


async def main_async() -> int:
    args = parse_args()
    from benchmark.tau2.train.runner import Tau2BatchRunConfig, run_tau2_batch_train_eval

    report = await run_tau2_batch_train_eval(
        Tau2BatchRunConfig(
            domain=args.domain,
            epochs=args.epochs,
            batch_size=args.batch_size,
            concurrency=args.concurrency,
            config_path=str(Path(args.config).expanduser()) if args.config else None,
            output_path=args.output,
            data_root=args.data_root,
            keep_default_tools=True,
            max_iterations=args.max_iterations,
        )
    )
    return 1 if any(epoch.get("errors") for epoch in report.train_epochs) else 0


def main() -> None:
    raise SystemExit(asyncio.run(main_async()))


if __name__ == "__main__":
    main()
