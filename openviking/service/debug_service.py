# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: Apache-2.0
"""
Debug Service - provides system status query and health check.
"""

from dataclasses import dataclass
from typing import Dict, List, Optional

from openviking.storage import VikingDBManager
from openviking.storage.observers import (
    QueueObserver,
    TransactionObserver,
    VikingDBObserver,
    VLMObserver,
)
from openviking.storage.queuefs import get_queue_manager
from openviking.storage.transaction import get_transaction_manager
from openviking_cli.utils.config import OpenVikingConfig


@dataclass
class ComponentStatus:
    """Component status."""

    name: str
    is_healthy: bool
    has_errors: bool
    status: str

    def __str__(self) -> str:
        health = "healthy" if self.is_healthy else "unhealthy"
        return f"[{self.name}] ({health})\n{self.status}"


@dataclass
class SystemStatus:
    """System overall status."""

    is_healthy: bool
    components: Dict[str, ComponentStatus]
    errors: List[str]

    def __str__(self) -> str:
        lines = []
        for component in self.components.values():
            lines.append(str(component))
            lines.append("")
        health = "healthy" if self.is_healthy else "unhealthy"
        lines.append(f"[system] ({health})")
        if self.errors:
            lines.append(f"Errors: {', '.join(self.errors)}")
        return "\n".join(lines)


class ObserverService:
    """Observer service - provides component status observation."""

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

    @property
    def queue(self) -> ComponentStatus:
        """Get queue status."""
        observer = QueueObserver(get_queue_manager())
        return ComponentStatus(
            name="queue",
            is_healthy=observer.is_healthy(),
            has_errors=observer.has_errors(),
            status=observer.get_status_table(),
        )

    @property
    def vikingdb(self) -> ComponentStatus:
        """Get VikingDB status."""
        observer = VikingDBObserver(self._vikingdb)
        return ComponentStatus(
            name="vikingdb",
            is_healthy=observer.is_healthy(),
            has_errors=observer.has_errors(),
            status=observer.get_status_table(),
        )

    @property
    def vlm(self) -> ComponentStatus:
        """Get VLM status."""
        observer = VLMObserver(self._config.vlm.get_vlm_instance())
        return ComponentStatus(
            name="vlm",
            is_healthy=observer.is_healthy(),
            has_errors=observer.has_errors(),
            status=observer.get_status_table(),
        )

    @property
    def transaction(self) -> ComponentStatus:
        """Get transaction status."""
        transaction_manager = get_transaction_manager()
        if transaction_manager is None:
            return ComponentStatus(
                name="transaction",
                is_healthy=False,
                has_errors=True,
                status="Transaction manager not initialized.",
            )
        observer = TransactionObserver(transaction_manager)
        return ComponentStatus(
            name="transaction",
            is_healthy=observer.is_healthy(),
            has_errors=observer.has_errors(),
            status=observer.get_status_table(),
        )

    @property
    def system(self) -> SystemStatus:
        """Get system overall status."""
        components = {
            "queue": self.queue,
            "vikingdb": self.vikingdb,
            "vlm": self.vlm,
            "transaction": self.transaction,
        }
        errors = [f"{c.name} has errors" for c in components.values() if c.has_errors]
        return SystemStatus(
            is_healthy=all(c.is_healthy for c in components.values()),
            components=components,
            errors=errors,
        )

    def is_healthy(self) -> bool:
        """Quick health check."""
        return self.system.is_healthy


class DebugService:
    """Debug service - provides system status query and health check."""

    def __init__(
        self,
        vikingdb: Optional[VikingDBManager] = None,
        config: Optional[OpenVikingConfig] = None,
    ):
        self._observer = ObserverService(vikingdb, config)

    def set_dependencies(
        self,
        vikingdb: VikingDBManager,
        config: OpenVikingConfig,
    ) -> None:
        """Set dependencies after initialization."""
        self._observer.set_dependencies(vikingdb, config)

    @property
    def observer(self) -> ObserverService:
        """Get observer service."""
        return self._observer

    def is_healthy(self) -> bool:
        """Quick health check."""
        return self._observer.is_healthy()
