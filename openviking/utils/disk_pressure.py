# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0
"""Disk pressure monitoring for proactive space protection."""

from __future__ import annotations

import asyncio
import shutil
import threading
import time
from enum import Enum
from pathlib import Path
from typing import Optional

from openviking_cli.utils.logger import get_logger

logger = get_logger(__name__)


class DiskPressureState(str, Enum):
    NORMAL = "normal"
    WARNING = "warning"
    CRITICAL = "critical"


class DiskPressureError(Exception):
    """Raised when disk pressure is CRITICAL and operation is blocked."""


class DiskPressureMonitor:
    """Monitor disk usage and block operations when critically low.

    Thread-safe singleton that periodically checks disk usage and
    maintains a pressure state (NORMAL, WARNING, CRITICAL).
    """

    _instance: Optional[DiskPressureMonitor] = None
    _lock = threading.Lock()

    def __init__(
        self,
        workspace_path: str,
        *,
        check_interval_seconds: float = 30.0,
        warning_threshold_percent: float = 85.0,
        critical_threshold_percent: float = 95.0,
        min_free_bytes: int = 1073741824,  # 1 GB
    ):
        self._workspace = Path(workspace_path)
        self._check_interval = check_interval_seconds
        self._warning_threshold = warning_threshold_percent
        self._critical_threshold = critical_threshold_percent
        self._min_free_bytes = min_free_bytes

        self._state = DiskPressureState.NORMAL
        self._state_lock = threading.Lock()
        self._last_check: float = 0
        self._last_usage_percent: float = 0
        self._last_free_bytes: int = 0

        self._monitor_task: Optional[asyncio.Task] = None
        self._stop_event = asyncio.Event()

    @classmethod
    def get_instance(cls) -> Optional[DiskPressureMonitor]:
        """Get the singleton instance if initialized."""
        return cls._instance

    @classmethod
    def initialize(cls, workspace_path: str, **kwargs) -> DiskPressureMonitor:
        """Initialize the singleton monitor."""
        with cls._lock:
            if cls._instance is None:
                cls._instance = cls(workspace_path, **kwargs)
            return cls._instance

    @classmethod
    def reset_instance(cls) -> None:
        """Reset the singleton instance (for testing)."""
        with cls._lock:
            cls._instance = None

    @property
    def state(self) -> DiskPressureState:
        """Current disk pressure state."""
        with self._state_lock:
            return self._state

    @property
    def usage_percent(self) -> float:
        """Last recorded disk usage percentage."""
        with self._state_lock:
            return self._last_usage_percent

    @property
    def free_bytes(self) -> int:
        """Last recorded free bytes."""
        with self._state_lock:
            return self._last_free_bytes

    def check(self) -> DiskPressureState:
        """Check disk usage and update state. Returns new state."""
        try:
            usage = shutil.disk_usage(self._workspace)
            usage_percent = (usage.used / usage.total) * 100
            free_bytes = usage.free
        except OSError as e:
            logger.error(f"Failed to check disk usage: {e}")
            with self._state_lock:
                self._state = DiskPressureState.CRITICAL
                return self._state

        with self._state_lock:
            self._last_check = time.monotonic()
            self._last_usage_percent = usage_percent
            self._last_free_bytes = free_bytes

            old_state = self._state

            if usage_percent >= self._critical_threshold or free_bytes < self._min_free_bytes:
                self._state = DiskPressureState.CRITICAL
            elif usage_percent >= self._warning_threshold:
                self._state = DiskPressureState.WARNING
            else:
                self._state = DiskPressureState.NORMAL

            if self._state != old_state:
                logger.warning(
                    f"Disk pressure state changed: {old_state.value} -> {self._state.value} "
                    f"(usage={usage_percent:.1f}%, free={free_bytes / 1073741824:.2f}GB)"
                )
            elif self._state == DiskPressureState.WARNING:
                logger.warning(
                    f"Disk pressure WARNING: {usage_percent:.1f}% used, "
                    f"{free_bytes / 1073741824:.2f}GB free"
                )
            elif self._state == DiskPressureState.CRITICAL:
                logger.error(
                    f"Disk pressure CRITICAL: {usage_percent:.1f}% used, "
                    f"{free_bytes / 1073741824:.2f}GB free - blocking writes"
                )

            return self._state

    def check_write_allowed(self) -> None:
        """Raise DiskPressureError if writes are blocked.

        Call this before write operations to ensure disk has space.
        """
        if self.state == DiskPressureState.CRITICAL:
            raise DiskPressureError(
                f"Disk pressure is CRITICAL: {self._last_usage_percent:.1f}% used, "
                f"{self._last_free_bytes / 1073741824:.2f}GB free. "
                "Write operations are blocked until disk space is freed."
            )

    async def start(self) -> None:
        """Start the background monitoring loop."""
        if self._monitor_task is not None:
            return

        self._stop_event.clear()
        self._monitor_task = asyncio.create_task(self._monitor_loop())
        logger.info(
            f"Disk pressure monitor started (interval={self._check_interval}s, "
            f"warning={self._warning_threshold}%, critical={self._critical_threshold}%)"
        )

    async def stop(self) -> None:
        """Stop the background monitoring loop."""
        if self._monitor_task is None:
            return

        self._stop_event.set()
        self._monitor_task.cancel()
        try:
            await self._monitor_task
        except asyncio.CancelledError:
            pass
        self._monitor_task = None
        logger.info("Disk pressure monitor stopped")

    async def _monitor_loop(self) -> None:
        """Background loop that periodically checks disk usage."""
        while not self._stop_event.is_set():
            self.check()
            try:
                await asyncio.wait_for(
                    self._stop_event.wait(),
                    timeout=self._check_interval,
                )
            except asyncio.TimeoutError:
                pass

    def get_status(self) -> dict:
        """Get current disk status for health endpoint."""
        with self._state_lock:
            return {
                "state": self._state.value,
                "usage_percent": round(self._last_usage_percent, 2),
                "free_bytes": self._last_free_bytes,
                "free_gb": round(self._last_free_bytes / 1073741824, 2),
                "warning_threshold_percent": self._warning_threshold,
                "critical_threshold_percent": self._critical_threshold,
                "min_free_bytes": self._min_free_bytes,
            }
