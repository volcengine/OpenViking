#!/usr/bin/env python3
"""CLI for remote benchmark batch policy train/eval."""

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
    parser.add_argument("--dataset", required=True, help="Remote benchmark dataset")
    parser.add_argument("--domain", required=True, help="Benchmark domain")
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
        default=200,
        help="Concurrent rollout executions for train and eval (default: 200)",
    )
    parser.add_argument(
        "--commit-concurrency",
        type=int,
        default=200,
        help="Concurrent OpenViking session.commit submissions during train (default: 200)",
    )
    parser.add_argument(
        "--commit-timeout-seconds",
        type=float,
        default=None,
        help="Max seconds to wait for each OpenViking session.commit task. Default waits indefinitely.",
    )
    parser.add_argument("--config", default=None, help="ov.conf path (optional)")
    parser.add_argument("--server-url", default=None, help="OpenViking server URL. Defaults to ov.conf/ovcli.conf")
    parser.add_argument("--api-key", default=None, help="OpenViking API key. Defaults to ov.conf/ovcli.conf")
    parser.add_argument("--account-id", default="default", help="OpenViking trusted account id. Default: default")
    parser.add_argument("--user-id", default="default", help="OpenViking trusted user id. Default: default")
    parser.add_argument("--output", default=None, help="JSON report output path")
    parser.add_argument(
        "--events-output",
        default=None,
        help="Streaming JSONL event output path. Defaults to report directory/events.jsonl.",
    )
    parser.add_argument(
        "--result-dir-name",
        default="train",
        help="Result subdirectory under result/{dataset}/ (default: train).",
    )
    parser.add_argument(
        "--benchmark-service-url",
        default=None,
        help="Benchmark runtime service URL, e.g. http://127.0.0.1:1944",
    )
    parser.add_argument(
        "--max-iterations",
        type=int,
        default=30,
        help="Max steps/iterations per rollout (default: 30)",
    )
    parser.add_argument(
        "--train-index",
        default=None,
        help=(
            "Run train sample(s) at 0-based split index/indices. "
            "Accepts one index or comma-separated indices, e.g. 7 or 1,5,6."
        ),
    )
    parser.add_argument(
        "--eval-index",
        default=None,
        help=(
            "Run eval/test sample(s) at 0-based split index/indices. "
            "Accepts one index or comma-separated indices, e.g. 3 or 10,14,18."
        ),
    )
    parser.add_argument(
        "--force-baseline-recompute",
        action="store_true",
        help=(
            "Recompute the cached pre-training baseline instead of reusing an "
            "existing cache file."
        ),
    )
    parser.add_argument(
        "--skip-baseline-eval",
        action="store_true",
        help="Skip the pre-training baseline eval/cache step.",
    )
    parser.add_argument(
        "--eval-split",
        choices=("train", "test", "none"),
        default="test",
        help="Split used for baseline, per-epoch, and final eval (default: test; none disables eval).",
    )
    parser.add_argument(
        "--eval-each-epoch",
        action="store_true",
        help="Run held-out eval after every training epoch. Disabled by default.",
    )
    parser.add_argument(
        "--skip-final-eval",
        action="store_true",
        help=(
            "Skip the final held-out eval pass. When --eval-each-epoch is enabled, "
            "the last epoch eval is reused as final_eval in the report."
        ),
    )
    parser.add_argument(
        "--trials",
        type=int,
        default=8,
        help="Run each eval split N times and aggregate (default: 8).",
    )
    parser.add_argument(
        "--train-trials",
        type=int,
        default=1,
        help="Run each train case N times per epoch (default: 1).",
    )
    parser.add_argument(
        "--reuse-train-rollout-cache",
        action="store_true",
        help=(
            "Reuse cached epoch-0 train rollouts when available. Default is off; "
            "cache misses still execute rollouts and populate the cache."
        ),
    )
    parser.add_argument(
        "--keep-recent-results",
        type=int,
        default=5,
        help=(
            "When --clean-result is enabled, keep the most recent N default run_ "
            "directories for the same domain while preserving cache/ and non-run_ "
            "directories (default: 5)."
        ),
    )
    clean_group = parser.add_mutually_exclusive_group()
    clean_group.add_argument(
        "--clean-result",
        dest="clean_result",
        action="store_true",
        default=True,
        help="Clean previous default result/{dataset}/train artifacts before running (default).",
    )
    clean_group.add_argument(
        "--no-clean-result",
        dest="clean_result",
        action="store_false",
        help="Keep previous result artifacts.",
    )
    return parser.parse_args()


def _parse_indices_arg(value: str | None) -> list[int] | None:
    if value is None or not str(value).strip():
        return None
    indices: list[int] = []
    for part in str(value).split(","):
        item = part.strip()
        if not item:
            continue
        index = int(item)
        if index < 0:
            raise ValueError("indices must be >= 0")
        indices.append(index)
    return indices or None


async def main_async() -> int:
    args = parse_args()
    from openviking.session.train.batch_runner import (
        BatchTrainEvalConfig,
        run_batch_train_eval,
    )

    report = await run_batch_train_eval(
        BatchTrainEvalConfig(
            dataset=args.dataset,
            domain=args.domain,
            epochs=args.epochs,
            batch_size=args.batch_size,
            concurrency=args.concurrency,
            commit_concurrency=args.commit_concurrency,
            commit_timeout_seconds=args.commit_timeout_seconds,
            config_path=str(Path(args.config).expanduser()) if args.config else None,
            server_url=args.server_url,
            api_key=args.api_key,
            account_id=args.account_id,
            user_id=args.user_id,
            output_path=args.output,
            events_path=args.events_output,
            result_dir_name=args.result_dir_name,
            keep_default_tools=True,
            max_iterations=args.max_iterations,
            train_index=_parse_indices_arg(args.train_index),
            eval_index=_parse_indices_arg(args.eval_index),
            benchmark_service_url=args.benchmark_service_url,
            baseline_force_recompute=args.force_baseline_recompute,
            eval_each_epoch=args.eval_each_epoch,
            skip_final_eval=args.skip_final_eval,
            skip_baseline_eval=args.skip_baseline_eval,
            eval_split=args.eval_split,
            trials=args.trials,
            train_trials=args.train_trials,
            reuse_train_rollout_cache=args.reuse_train_rollout_cache,
            clean_result=args.clean_result,
            keep_recent_results=args.keep_recent_results,
        )
    )
    return 1 if any(epoch.get("errors") for epoch in report.train_epochs) else 0


def main() -> None:
    raise SystemExit(asyncio.run(main_async()))


if __name__ == "__main__":
    main()
