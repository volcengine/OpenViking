#!/usr/bin/env python3
"""HTTP service exposing deterministic smoke cases and rollout execution."""

# ruff: noqa: E402

from __future__ import annotations

import argparse
import asyncio
import sys
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import Any

import uvicorn

DEFAULT_NATIVE_THREAD_WORKERS = 8
DEFAULT_MAX_ROLLOUT_CONCURRENCY = 32
DEFAULT_ROLLOUT_THREAD_WORKERS = 8

REPO_ROOT = Path(__file__).resolve().parents[3]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from benchmark.smoke.train.case_loader import SmokeCaseLoader
from benchmark.smoke.train.rollout_executor import SmokeRolloutExecutor
from openviking.session.train.components.dataset_service import create_dataset_service_app


def create_app(
    *,
    max_rollout_concurrency: int | None = None,
    rollout_thread_workers: int | None = None,
):
    def make_case_loader(
        dataset: str,
        domain: str,
        split: str,
        filters: dict[str, Any],
    ) -> SmokeCaseLoader:
        if dataset != "smoke":
            raise ValueError(f"Unsupported dataset: {dataset}")
        return SmokeCaseLoader(
            domain=domain,
            split=split,
            task_indices=_task_indices_from_filters(filters),
        )

    def make_rollout_executor(options: dict[str, Any]) -> SmokeRolloutExecutor:
        return SmokeRolloutExecutor(
            concurrency=int(options.get("env_concurrency") or options.get("concurrency") or 8),
            direct_experience_content=_optional_str(options.get("direct_experience_content")),
            show_progress=_bool_option(options.get("show_progress"), default=False),
            progress_label=str(options.get("progress_label") or "smoke"),
        )

    return create_dataset_service_app(
        service_name="smoke",
        make_case_loader=make_case_loader,
        make_rollout_executor=make_rollout_executor,
        max_rollout_concurrency=max_rollout_concurrency,
        rollout_thread_workers=rollout_thread_workers,
    )


def _task_indices_from_filters(filters: dict[str, Any]) -> list[int] | None:
    raw = filters.get("task_indices")
    if raw is None:
        return None
    if not isinstance(raw, list):
        raise ValueError("task_indices filter must be a list")
    indices: list[int] = []
    for value in raw:
        index = int(value)
        if index < 0:
            raise ValueError("task index must be >= 0")
        indices.append(index)
    return indices


def _bool_option(value: Any, *, default: bool) -> bool:
    if value is None:
        return default
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        text = value.strip().lower()
        if text in {"1", "true", "yes", "on"}:
            return True
        if text in {"0", "false", "no", "off"}:
            return False
        raise ValueError(f"Invalid boolean option: {value!r}")
    return bool(value)


def _optional_str(value: Any) -> str | None:
    if value is None:
        return None
    text = str(value)
    return text if text.strip() else None


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Start smoke rollout HTTP service")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=1964)
    parser.add_argument(
        "--native-thread-workers",
        type=int,
        default=DEFAULT_NATIVE_THREAD_WORKERS,
        help="Default thread pool workers for deterministic smoke rollouts.",
    )
    parser.add_argument(
        "--max-rollout-concurrency",
        type=int,
        default=DEFAULT_MAX_ROLLOUT_CONCURRENCY,
        help="Maximum concurrent rollout executions hosted by this service.",
    )
    parser.add_argument(
        "--rollout-thread-workers",
        type=int,
        default=DEFAULT_ROLLOUT_THREAD_WORKERS,
        help="Worker threads used to host rollouts. Set to 0 to disable.",
    )
    return parser.parse_args()


class SmokeServiceServer(uvicorn.Server):
    def __init__(self, config: uvicorn.Config, *, native_thread_workers: int) -> None:
        super().__init__(config)
        self._native_thread_workers = native_thread_workers
        self._default_executor: ThreadPoolExecutor | None = None

    async def serve(self, sockets=None) -> None:
        if self._native_thread_workers <= 0:
            raise ValueError("native_thread_workers must be > 0")
        loop = asyncio.get_running_loop()
        self._default_executor = ThreadPoolExecutor(
            max_workers=self._native_thread_workers,
            thread_name_prefix="smoke-native",
        )
        loop.set_default_executor(self._default_executor)
        try:
            await super().serve(sockets=sockets)
        finally:
            self._default_executor.shutdown(wait=False, cancel_futures=True)


def main() -> None:
    args = parse_args()
    app = create_app(
        max_rollout_concurrency=args.max_rollout_concurrency,
        rollout_thread_workers=(
            None if args.rollout_thread_workers == 0 else args.rollout_thread_workers
        ),
    )
    config = uvicorn.Config(
        app,
        host=args.host,
        port=args.port,
        access_log=False,
        log_level="warning",
    )
    server = SmokeServiceServer(config, native_thread_workers=args.native_thread_workers)
    server.run()


if __name__ == "__main__":
    main()
