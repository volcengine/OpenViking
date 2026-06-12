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
    parser = argparse.ArgumentParser(description="Run remote benchmark batch policy train/eval")
    parser.add_argument("--dataset", default="tau2", help="Remote benchmark dataset. Default: tau2")
    parser.add_argument("--domain", default="airline", help="Benchmark domain. Default: airline")
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
        help="Concurrent rollout executions for train and eval (default: 20)",
    )
    parser.add_argument(
        "--commit-concurrency",
        type=int,
        default=20,
        help="Concurrent OpenViking session.commit submissions during train (default: 20)",
    )
    parser.add_argument("--config", default=None, help="ov.conf path (optional)")
    parser.add_argument("--server-url", default=None, help="OpenViking server URL. Defaults to ov.conf/ovcli.conf")
    parser.add_argument("--api-key", default=None, help="OpenViking API key. Defaults to ov.conf/ovcli.conf")
    parser.add_argument("--account-id", default="default", help="OpenViking trusted account id. Default: default")
    parser.add_argument("--user-id", default="default", help="OpenViking trusted user id. Default: default")
    parser.add_argument("--output", default=None, help="JSON report output path")
    parser.add_argument(
        "--benchmark-service-url",
        default=None,
        help="Benchmark runtime service URL, e.g. http://127.0.0.1:1944",
    )
    parser.add_argument(
        "--max-iterations",
        type=int,
        default=30,
        help="VikingBot max tool iterations per rollout (default: 30)",
    )
    parser.add_argument(
        "--train-limit",
        type=int,
        default=None,
        help="Limit number of train cases for smoke tests.",
    )
    parser.add_argument(
        "--eval-limit",
        type=int,
        default=None,
        help="Limit number of eval cases for smoke tests.",
    )
    parser.add_argument(
        "--baseline-eval",
        action="store_true",
        help="Run pre-training baseline eval. Disabled by default.",
    )
    return parser.parse_args()


async def main_async() -> int:
    args = parse_args()
    from benchmark.tau2.train.runner import Tau2BatchRunConfig, run_tau2_batch_train_eval

    report = await run_tau2_batch_train_eval(
        Tau2BatchRunConfig(
            dataset=args.dataset,
            domain=args.domain,
            epochs=args.epochs,
            batch_size=args.batch_size,
            concurrency=args.concurrency,
            commit_concurrency=args.commit_concurrency,
            config_path=str(Path(args.config).expanduser()) if args.config else None,
            server_url=args.server_url,
            api_key=args.api_key,
            account_id=args.account_id,
            user_id=args.user_id,
            output_path=args.output,
            keep_default_tools=True,
            max_iterations=args.max_iterations,
            train_limit=args.train_limit,
            eval_limit=args.eval_limit,
            benchmark_service_url=args.benchmark_service_url,
            baseline_eval_enabled=args.baseline_eval,
        )
    )
    return 1 if any(epoch.get("errors") for epoch in report.train_epochs) else 0


def main() -> None:
    raise SystemExit(asyncio.run(main_async()))


if __name__ == "__main__":
    main()
