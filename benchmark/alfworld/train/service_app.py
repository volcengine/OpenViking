#!/usr/bin/env python3
"""HTTP service exposing ALFWorld cases and rollout execution."""

# ruff: noqa: E402,I001

from __future__ import annotations

import argparse
import asyncio
import os
import sys
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import Any

import uvicorn

DEFAULT_NATIVE_THREAD_WORKERS = 32
DEFAULT_MAX_ROLLOUT_CONCURRENCY = 32
DEFAULT_ROLLOUT_THREAD_WORKERS = 32

REPO_ROOT = Path(__file__).resolve().parents[3]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from benchmark.alfworld.train.case_loader import AlfworldCaseLoader, TASKS
from benchmark.alfworld.train.rollout_executor import AlfworldRolloutExecutor
from openviking.session.train.components.dataset_service import create_dataset_service_app


def create_app(
    *,
    data_root: str | None = None,
    max_rollout_concurrency: int | None = None,
    rollout_thread_workers: int | None = None,
    default_case_count: int = 1,
):
    if default_case_count <= 0:
        raise ValueError("default_case_count must be > 0")
    if data_root:
        os.environ["ALFWORLD_DATA"] = str(Path(data_root).expanduser())

    def make_case_loader(
        dataset: str,
        domain: str,
        split: str,
        filters: dict[str, Any],
    ) -> AlfworldCaseLoader:
        if dataset != "alfworld":
            raise ValueError(f"Unsupported dataset: {dataset}")
        return AlfworldCaseLoader(
            domain=domain,
            split=split,
            data_root=data_root,
            task_indices=_task_indices_from_filters(filters),
            gamefiles=_gamefiles_from_filters(filters),
            case_count=int(
                filters.get("case_count") or filters.get("env_num") or default_case_count
            ),
            allow_pseudo_cases=_bool_option(
                filters.get("allow_pseudo_cases") or os.getenv("ALFWORLD_ALLOW_PSEUDO_CASES"),
                default=False,
            ),
            metadata=dict(filters or {}),
        )

    def make_rollout_executor(options: dict[str, Any]) -> AlfworldRolloutExecutor:
        return AlfworldRolloutExecutor(
            max_steps=int(options.get("max_steps") or options.get("max_iterations") or 50),
            max_api_workers=int(options.get("max_api_workers") or 8),
            max_completion_tokens=int(options.get("max_completion_tokens") or 16384),
            seed=int(options.get("seed") or 42),
            eval_dataset=_optional_str(options.get("eval_dataset")),
            is_train=_optional_bool(options.get("is_train")),
            concurrency=int(options.get("env_concurrency") or 1),
            show_progress=_bool_option(options.get("show_progress"), default=False),
            progress_label=str(options.get("progress_label") or "alfworld"),
        )

    return create_dataset_service_app(
        service_name="alfworld",
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


def _gamefiles_from_filters(filters: dict[str, Any]) -> list[str] | None:
    raw = filters.get("gamefiles") or filters.get("gamefile")
    if raw is None:
        return None
    if isinstance(raw, str):
        return [item.strip() for item in raw.split(",") if item.strip()]
    if isinstance(raw, list):
        return [str(item) for item in raw]
    raise ValueError("gamefiles filter must be a string or list")


def _bool_option(value: Any, *, default: bool) -> bool:
    parsed = _optional_bool(value)
    return default if parsed is None else parsed


def _optional_bool(value: Any) -> bool | None:
    if value is None or value == "":
        return None
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
    parser = argparse.ArgumentParser(description="Start ALFWorld rollout HTTP service")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=1954)
    parser.add_argument("--data-root", default=os.getenv("ALFWORLD_DATA"))
    parser.add_argument(
        "--default-case-count",
        type=int,
        default=int(os.getenv("ALFWORLD_DEFAULT_CASE_COUNT", "1")),
        help="Number of pseudo env-slot cases to expose when no gamefiles are discoverable.",
    )
    parser.add_argument(
        "--native-thread-workers",
        type=int,
        default=int(
            os.getenv("ALFWORLD_NATIVE_THREAD_WORKERS", str(DEFAULT_NATIVE_THREAD_WORKERS))
        ),
    )
    parser.add_argument(
        "--max-rollout-concurrency",
        type=int,
        default=int(
            os.getenv("ALFWORLD_MAX_ROLLOUT_CONCURRENCY", str(DEFAULT_MAX_ROLLOUT_CONCURRENCY))
        ),
    )
    parser.add_argument(
        "--rollout-thread-workers",
        type=int,
        default=int(
            os.getenv("ALFWORLD_ROLLOUT_THREAD_WORKERS", str(DEFAULT_ROLLOUT_THREAD_WORKERS))
        ),
        help="Worker threads used to host rollout executions. Set to 0 to disable.",
    )
    parser.add_argument(
        "--list-domains",
        action="store_true",
        help="Print supported ALFWorld domain/task filters and exit.",
    )
    return parser.parse_args()


class AlfworldServiceServer(uvicorn.Server):
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
            thread_name_prefix="alfworld-native",
        )
        loop.set_default_executor(self._default_executor)
        try:
            await super().serve(sockets=sockets)
        finally:
            self._default_executor.shutdown(wait=False, cancel_futures=True)


def main() -> None:
    args = parse_args()
    if args.list_domains:
        print("all")
        for task in TASKS:
            print(task)
        return
    app = create_app(
        data_root=args.data_root,
        max_rollout_concurrency=args.max_rollout_concurrency,
        rollout_thread_workers=(
            None if args.rollout_thread_workers == 0 else args.rollout_thread_workers
        ),
        default_case_count=args.default_case_count,
    )
    config = uvicorn.Config(
        app,
        host=args.host,
        port=args.port,
        access_log=False,
        log_level="warning",
    )
    server = AlfworldServiceServer(config, native_thread_workers=args.native_thread_workers)
    server.run()


if __name__ == "__main__":
    main()
