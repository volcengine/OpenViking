#!/usr/bin/env python3
"""HTTP service exposing tau2 cases and rollout execution."""

# ruff: noqa: E402

from __future__ import annotations

import argparse
import asyncio
import logging
import os
import sys
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import Any

import uvicorn

DEFAULT_NATIVE_THREAD_WORKERS = 128
DEFAULT_MAX_ROLLOUT_CONCURRENCY = 200
DEFAULT_ROLLOUT_THREAD_WORKERS = 200
TAU2_SERVICE_LOG_LEVEL = "WARNING"

REPO_ROOT = Path(__file__).resolve().parents[3]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from benchmark.tau2.train.case_loader import Tau2CaseLoader
from benchmark.tau2.train.rollout_executor import (
    DEFAULT_TAU2_ROLLOUT_BACKEND,
    make_tau2_rollout_executor,
    normalize_tau2_rollout_backend,
)
from openviking.session.train.components.dataset_service import create_dataset_service_app


def configure_tau2_service_logging() -> None:
    """Keep third-party tau2/loguru service output at warning and above."""
    logging.getLogger("tau2").setLevel(logging.WARNING)
    try:
        from loguru import logger as loguru_logger
    except Exception:
        return

    loguru_logger.remove()
    loguru_logger.add(sys.stderr, level=TAU2_SERVICE_LOG_LEVEL)


def create_app(
    *,
    data_root: str | None = None,
    config_path: str | None = None,
    rollout_language: str = "default",
    rollout_backend: str | None = None,
    max_rollout_concurrency: int | None = None,
    rollout_thread_workers: int | None = None,
):
    if rollout_language not in {"default", "zh"}:
        raise ValueError("rollout_language must be 'default' or 'zh'")
    default_backend = normalize_tau2_rollout_backend(
        rollout_backend or os.getenv("TAU2_ROLLOUT_BACKEND") or DEFAULT_TAU2_ROLLOUT_BACKEND
    )

    def make_case_loader(
        dataset: str,
        domain: str,
        split: str,
        filters: dict[str, Any],
    ) -> Tau2CaseLoader:
        if dataset != "tau2":
            raise ValueError(f"Unsupported dataset: {dataset}")
        return Tau2CaseLoader(
            domain=domain,
            split=split,
            data_root=data_root,
            task_indices=_task_indices_from_filters(filters),
        )

    def make_rollout_executor(options: dict[str, Any]):
        backend = normalize_tau2_rollout_backend(
            options.get("rollout_backend") or options.get("backend") or default_backend
        )
        return make_tau2_rollout_executor(
            backend=backend,
            options={
                **options,
                "show_progress": options.get("show_progress", False),
                "progress_label": options.get("progress_label") or "tau2",
            },
            config_path=config_path,
            concurrency=1,
            rollout_language=rollout_language,
        )

    return create_dataset_service_app(
        service_name="tau2",
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


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Start tau2 rollout HTTP service")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=1944)
    parser.add_argument("--data-root", default=os.getenv("TAU2_DATA_ROOT"))
    parser.add_argument("--config", default=os.getenv("OPENVIKING_CONFIG_FILE"))
    parser.add_argument("--rollout-language", choices=["default", "zh"], default="default")
    parser.add_argument(
        "--rollout-backend",
        choices=["native", "vikingbot"],
        default=os.getenv("TAU2_ROLLOUT_BACKEND", DEFAULT_TAU2_ROLLOUT_BACKEND),
        help="Rollout implementation backend (default: native).",
    )
    parser.add_argument(
        "--native-thread-workers",
        type=int,
        default=int(os.getenv("TAU2_NATIVE_THREAD_WORKERS", str(DEFAULT_NATIVE_THREAD_WORKERS))),
        help="Default thread pool workers for native tau2 rollout execution (default: 128).",
    )
    parser.add_argument(
        "--max-rollout-concurrency",
        type=int,
        default=int(
            os.getenv(
                "TAU2_MAX_ROLLOUT_CONCURRENCY",
                str(DEFAULT_MAX_ROLLOUT_CONCURRENCY),
            )
        ),
        help=(
            "Maximum concurrent rollout executions hosted by this service. "
            f"Default: {DEFAULT_MAX_ROLLOUT_CONCURRENCY}."
        ),
    )
    parser.add_argument(
        "--rollout-thread-workers",
        type=int,
        default=int(
            os.getenv(
                "TAU2_ROLLOUT_THREAD_WORKERS",
                str(DEFAULT_ROLLOUT_THREAD_WORKERS),
            )
        ),
        help=(
            "Worker threads used to host rollout executions off the uvicorn event loop. "
            "Set to 0 to disable threaded hosting. "
            f"Default: {DEFAULT_ROLLOUT_THREAD_WORKERS}."
        ),
    )
    return parser.parse_args()


class Tau2ServiceServer(uvicorn.Server):
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
            thread_name_prefix="tau2-native",
        )
        loop.set_default_executor(self._default_executor)
        try:
            await super().serve(sockets=sockets)
        finally:
            self._default_executor.shutdown(wait=False, cancel_futures=True)


def main() -> None:
    args = parse_args()

    configure_tau2_service_logging()
    app = create_app(
        data_root=args.data_root,
        config_path=args.config,
        rollout_language=args.rollout_language,
        rollout_backend=args.rollout_backend,
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
    server = Tau2ServiceServer(config, native_thread_workers=args.native_thread_workers)
    server.run()


if __name__ == "__main__":
    main()
