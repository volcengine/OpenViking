# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0
"""Configure asyncio's default executor for request-path blocking I/O."""

from __future__ import annotations

import asyncio
import os
import threading
from concurrent.futures import ThreadPoolExecutor
from typing import Optional

from openviking_cli.utils import get_logger

logger = get_logger(__name__)

_DEFAULT_MAX_WORKERS = 64
_executor: Optional[ThreadPoolExecutor] = None
_max_workers: Optional[int] = None
_lock = threading.Lock()


def _normalize_max_workers(max_workers: Optional[int]) -> int:
    if max_workers is None:
        env_value = os.environ.get("OPENVIKING_REQUEST_IO_THREADS", "")
        if env_value.strip().isdigit():
            max_workers = int(env_value)
        else:
            max_workers = _DEFAULT_MAX_WORKERS
    return max(1, int(max_workers))


def configure_request_io_executor(max_workers: Optional[int] = None) -> None:
    """Set the event loop default executor used by ``asyncio.to_thread``.

    This does not move any code into threads by itself. It only controls the
    concurrency of existing ``asyncio.to_thread`` and ``run_in_executor(None, ...)``
    call sites.
    """
    global _executor, _max_workers

    normalized = _normalize_max_workers(max_workers)
    loop = asyncio.get_running_loop()
    with _lock:
        if _executor is not None and _max_workers == normalized:
            loop.set_default_executor(_executor)
            return

        old_executor = _executor
        _executor = ThreadPoolExecutor(
            max_workers=normalized,
            thread_name_prefix="ov-request-io",
        )
        _max_workers = normalized
        loop.set_default_executor(_executor)

    if old_executor is not None:
        old_executor.shutdown(wait=False, cancel_futures=False)

    logger.info("Configured request I/O default executor with max_workers=%d", normalized)


__all__ = ["configure_request_io_executor"]
