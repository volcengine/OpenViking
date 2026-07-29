# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0
"""LockObserver: Lock system observability."""

import time
from typing import Any, Dict, List

from openviking.storage.observers.base_observer import BaseObserver
from openviking.storage.transaction.lock_manager import LockManager
from openviking_cli.utils.logger import get_logger

logger = get_logger(__name__)


class LockObserver(BaseObserver):
    """Observability tool for the lock system."""

    def __init__(self, lock_manager: LockManager):
        self._manager = lock_manager

    def get_active_locks(self) -> List[Dict[str, Any]]:
        """Return info about every active lock handle."""
        now = time.time()
        return [
            {
                "id": h.id,
                "lock_count": len(h.locks),
                "created_at": h.created_at,
                "last_active_at": h.last_active_at,
                "duration_seconds": round(now - h.created_at, 1),
                "idle_seconds": round(now - h.last_active_at, 1),
            }
            for h in self._manager.get_active_handles().values()
        ]

    def get_hanging_locks(self, threshold: float = 600) -> List[Dict[str, Any]]:
        """Return locks that have been idle longer than *threshold* seconds."""
        return [lock for lock in self.get_active_locks() if lock["idle_seconds"] > threshold]

    # ------ BaseObserver interface ------

    def get_status_table(self) -> str:
        locks = self.get_active_locks()
        if not locks:
            return "No active locks."

        from tabulate import tabulate

        data = [
            {
                "Handle ID": l["id"][:8] + "...",
                "Locks": l["lock_count"],
                "Duration": f"{l['duration_seconds']}s",
                "Idle": f"{l['idle_seconds']}s",
                "Created": time.strftime("%H:%M:%S", time.localtime(l["created_at"])),
            }
            for l in locks
        ]
        data.append(
            {
                "Handle ID": f"TOTAL ({len(locks)})",
                "Locks": sum(l["lock_count"] for l in locks),
                "Duration": "",
                "Idle": "",
                "Created": "",
            }
        )
        return tabulate(data, headers="keys", tablefmt="pretty")

    def is_healthy(self) -> bool:
        return not self.get_hanging_locks(600)

    def has_errors(self) -> bool:
        return bool(self.get_hanging_locks(600))
