# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: Apache-2.0
"""
Debug Service - provides system status query and health check.
"""

from dataclasses import dataclass
from typing import Any, Dict, List, Optional

from openviking.storage import VikingDBManager
from openviking.storage.observers import QueueObserver, VikingDBObserver, VLMObserver
from openviking.storage.queuefs import get_queue_manager
from openviking.utils.config import OpenVikingConfig


@dataclass
class ComponentStatus:
    """Component status."""

    name: str
    is_healthy: bool
    has_errors: bool
    details: Dict[str, Any]


@dataclass
class SystemStatus:
    """System overall status."""

    is_healthy: bool
    components: Dict[str, ComponentStatus]
    errors: List[str]


class DebugService:
    """Debug service - provides system status query and health check."""

    def __init__(
        self,
        vikingdb: Optional[VikingDBManager] = None,
        config: Optional[OpenVikingConfig] = None,
    ):
        self._vikingdb = vikingdb
        self._config = config

    def set_dependencies(
        self,
        vikingdb: VikingDBManager,
        config: OpenVikingConfig,
    ) -> None:
        """Set dependencies after initialization."""
        self._vikingdb = vikingdb
        self._config = config

    def get_queue_status(self) -> ComponentStatus:
        """Get queue status."""
        observer = QueueObserver(get_queue_manager())
        return ComponentStatus(
            name="queue",
            is_healthy=observer.is_healthy(),
            has_errors=observer.has_errors(),
            details={"status_table": observer.get_status_table()},
        )

    def get_vikingdb_status(self) -> ComponentStatus:
        """Get VikingDB status."""
        observer = VikingDBObserver(self._vikingdb)
        return ComponentStatus(
            name="vikingdb",
            is_healthy=observer.is_healthy(),
            has_errors=observer.has_errors(),
            details={"status_table": observer.get_status_table()},
        )

    def get_vlm_status(self) -> ComponentStatus:
        """Get VLM status."""
        observer = VLMObserver(self._config.vlm.get_vlm_instance())
        return ComponentStatus(
            name="vlm",
            is_healthy=observer.is_healthy(),
            has_errors=observer.has_errors(),
            details={"status_table": observer.get_status_table()},
        )

    def get_system_status(self) -> SystemStatus:
        """Get system overall status."""
        components = {
            "queue": self.get_queue_status(),
            "vikingdb": self.get_vikingdb_status(),
            "vlm": self.get_vlm_status(),
        }
        errors = [f"{c.name} has errors" for c in components.values() if c.has_errors]
        return SystemStatus(
            is_healthy=all(c.is_healthy for c in components.values()),
            components=components,
            errors=errors,
        )

    def is_healthy(self) -> bool:
        """Quick health check."""
        return self.get_system_status().is_healthy
