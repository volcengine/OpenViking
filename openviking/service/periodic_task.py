# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0
"""Shared service lifecycle for periodic producers of asynchronous queue work."""

import asyncio

from openviking_cli.utils.logger import get_logger

logger = get_logger(__name__)


class PeriodicTask:
    def __init__(self, *, interval: float, sleep=asyncio.sleep):
        self._check_interval = float(interval)
        self._sleep = sleep
        self._running = False
        self._task = None

    async def start(self):
        if not self._running:
            self._running = True
            self._task = asyncio.create_task(self._run_loop())

    async def stop(self):
        self._running = False
        if self._task is not None:
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass
            self._task = None

    async def _run_loop(self):
        while self._running:
            try:
                await self._sleep(self._next_interval())
                await self._scan_once()
            except asyncio.CancelledError:
                break
            except Exception:
                logger.exception("%s scan failed", type(self).__name__)

    async def _scan_once(self):
        raise NotImplementedError

    def _next_interval(self) -> float:
        return self._check_interval
