# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0
"""
Debug Service - provides system status query and health check.
"""

from dataclasses import dataclass
from typing import Any, Dict, List, Optional

from openviking.server.identity import RequestContext
from openviking.storage.vikingdb_manager import VikingDBManager
from openviking.storage.observers import (
    FilesystemObserver,
    ModelsObserver,
    QueueObserver,
    RetrievalObserver,
    VikingDBObserver,
)
from openviking.storage.queuefs import get_queue_manager
from openviking.storage.viking_fs import get_viking_fs
from openviking_cli.utils import run_async
from openviking_cli.utils.config import OpenVikingConfig
from openviking_cli.utils.logger import get_logger

logger = get_logger(__name__)


def _queue_not_initialized_status(format: str) -> Any:
    if format == "json":
        return {
            "queues": [],
            "summary": {
                "pending": 0,
                "in_progress": 0,
                "processed": 0,
                "requeued": 0,
                "errors": 0,
                "total": 0,
            },
            "error": "Not initialized",
        }
    return "Not initialized"


def _vikingdb_not_initialized_status(format: str) -> Any:
    if format == "json":
        return {
            "collections": [],
            "summary": {
                "index_count": 0,
                "vector_count": 0,
                "collection_count": 0,
            },
            "error": "Not initialized",
        }
    return "Not initialized"


def _models_not_initialized_status(format: str) -> Any:
    if format == "json":
        return {
            "vlm": [],
            "embedding": [],
            "rerank": [],
            "error": "Not initialized",
        }
    return "Not initialized"


def _lock_not_initialized_status(format: str) -> Any:
    if format == "json":
        return {
            "active_locks": 0,
            "waiting_locks": 0,
            "stale_locks_removed": 0,
            "conflict_count": 0,
            "error": "Not initialized",
        }
    return "Not initialized"


@dataclass
class ComponentStatus:
    """Component status."""

    name: str
    is_healthy: bool
    has_errors: bool
    status: Any

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
        agfs_client: Optional[Any] = None,
    ):
        self._vikingdb = vikingdb
        self._config = config
        self._agfs_client = agfs_client

    def set_dependencies(
        self,
        vikingdb: VikingDBManager,
        config: OpenVikingConfig,
        agfs_client: Optional[Any] = None,
    ) -> None:
        """Set dependencies after initialization."""
        self._vikingdb = vikingdb
        self._config = config
        if agfs_client is not None:
            self._agfs_client = agfs_client

    @property
    def _dependencies_ready(self) -> bool:
        """Check if both vikingdb and config dependencies are set."""
        return self._vikingdb is not None and self._config is not None

    def get_queue_status(self, *, format: str = "table") -> ComponentStatus:
        """Get queue status."""
        try:
            qm = get_queue_manager()
        except Exception:
            return ComponentStatus(
                name="queue",
                is_healthy=False,
                has_errors=True,
                status=_queue_not_initialized_status(format),
            )
        observer = QueueObserver(qm)
        try:
            status = observer.get_status_json() if format == "json" else observer.get_status_table()
            is_healthy = observer.is_healthy()
            has_errors = observer.has_errors()
        except Exception as exc:
            logger.warning("Queue observer status unavailable: %s", exc)
            if format == "json":
                status = {
                    "queues": [],
                    "summary": {
                        "pending": 0,
                        "in_progress": 0,
                        "processed": 0,
                        "requeued": 0,
                        "errors": 0,
                        "total": 0,
                    },
                    "error": str(exc),
                }
            else:
                status = f"Status unavailable: {exc}"
            is_healthy = False
            has_errors = True
        return ComponentStatus(
            name="queue",
            is_healthy=is_healthy,
            has_errors=has_errors,
            status=status,
        )

    @property
    def queue(self) -> ComponentStatus:
        """Get queue status."""
        return self.get_queue_status()

    def get_vikingdb_status(
        self, ctx: Optional[RequestContext] = None, *, format: str = "table"
    ) -> ComponentStatus:
        """Get VikingDB status."""
        if self._vikingdb is None:
            return ComponentStatus(
                name="vikingdb",
                is_healthy=False,
                has_errors=True,
                status=_vikingdb_not_initialized_status(format),
            )
        observer = VikingDBObserver(self._vikingdb)
        return ComponentStatus(
            name="vikingdb",
            is_healthy=observer.is_healthy(),
            has_errors=observer.has_errors(),
            status=observer.get_status_json(ctx=ctx)
            if format == "json"
            else observer.get_status_table(ctx=ctx),
        )

    def vikingdb(self, ctx: Optional[RequestContext] = None) -> ComponentStatus:
        """Get VikingDB status."""
        return self.get_vikingdb_status(ctx=ctx)

    @property
    def models(self) -> ComponentStatus:
        """Get Models status (VLM, Embedding, Rerank)."""
        return self.get_models_status()

    def get_models_status(self, *, format: str = "table") -> ComponentStatus:
        """Get Models status (VLM, Embedding, Rerank) with a specific status format."""
        if self._config is None:
            return ComponentStatus(
                name="models",
                is_healthy=False,
                has_errors=True,
                status=_models_not_initialized_status(format),
            )

        vlm_instance = self._config.vlm.get_vlm_instance()
        embedding_instance = None
        rerank_instance = None
        embedding_config = getattr(self._config, "embedding", None)
        rerank_config = getattr(self._config, "rerank", None)

        if embedding_config:
            embedding_instance = embedding_config.get_embedder()

        if rerank_config and rerank_config.is_available():
            from openviking.models.rerank import RerankClient

            rerank_instance = RerankClient.from_config(rerank_config)

        observer = ModelsObserver(
            vlm_instance=vlm_instance,
            embedding_instance=embedding_instance,
            rerank_instance=rerank_instance,
        )
        return ComponentStatus(
            name="models",
            is_healthy=observer.is_healthy(),
            has_errors=observer.has_errors(),
            status=observer.get_status_json() if format == "json" else observer.get_status_table(),
        )

    @property
    def lock(self) -> ComponentStatus:
        """Get lock system status via pathlock_observe snapshot."""
        try:
            viking_fs = get_viking_fs()
            snapshot = run_async(viking_fs._async_agfs.pathlock_observe())
        except Exception:
            return ComponentStatus(
                name="lock",
                is_healthy=False,
                has_errors=True,
                status=_lock_not_initialized_status(format),
            )
        active = snapshot.get("active_locks", 0)
        waiting = snapshot.get("waiting_locks", 0)
        stale = snapshot.get("stale_locks_removed", 0)
        conflicts = snapshot.get("conflicts", [])
        lines = [
            f"Active locks: {active}",
            f"Waiting locks: {waiting}",
            f"Stale locks removed: {stale}",
            f"Conflicts: {len(conflicts)}",
        ]
        # Conflicts and stale removals are retained diagnostics, not current failures.
        return ComponentStatus(
            name="lock",
            is_healthy=True,
            has_errors=False,
            status="\n".join(lines),
        )

    def get_lock_status(self, *, format: str = "table") -> ComponentStatus:
        """Get lock system status via pathlock_observe snapshot."""
        try:
            viking_fs = get_viking_fs()
            snapshot = run_async(viking_fs._async_agfs.pathlock_observe())
        except Exception:
            return ComponentStatus(
                name="lock",
                is_healthy=False,
                has_errors=True,
                status=_lock_not_initialized_status(format),
            )
        active = snapshot.get("active_locks", 0)
        waiting = snapshot.get("waiting_locks", 0)
        stale = snapshot.get("stale_locks_removed", 0)
        conflicts = snapshot.get("conflicts", [])
        if format == "json":
            status: Any = {
                "active_locks": active,
                "waiting_locks": waiting,
                "stale_locks_removed": stale,
                "conflict_count": len(conflicts),
            }
        else:
            status = "\n".join(
                [
                    f"Active locks: {active}",
                    f"Waiting locks: {waiting}",
                    f"Stale locks removed: {stale}",
                    f"Conflicts: {len(conflicts)}",
                ]
            )
        return ComponentStatus(
            name="lock",
            is_healthy=True,
            has_errors=False,
            status=status,
        )

    @property
    def retrieval(self) -> ComponentStatus:
        """Get retrieval quality status."""
        observer = RetrievalObserver()
        return ComponentStatus(
            name="retrieval",
            is_healthy=observer.is_healthy(),
            has_errors=observer.has_errors(),
            status=observer.get_status_table(),
        )

    def get_retrieval_status(self, *, format: str = "table") -> ComponentStatus:
        """Get retrieval quality status."""
        observer = RetrievalObserver()
        return ComponentStatus(
            name="retrieval",
            is_healthy=observer.is_healthy(),
            has_errors=observer.has_errors(),
            status=observer.get_status_json() if format == "json" else observer.get_status_table(),
        )

    @property
    def filesystem(self) -> ComponentStatus:
        """Get filesystem operation status."""
        observer = FilesystemObserver()
        return ComponentStatus(
            name="filesystem",
            is_healthy=observer.is_healthy(),
            has_errors=observer.has_errors(),
            status=observer.get_status_table(),
        )

    def get_filesystem_status(self, *, format: str = "table") -> ComponentStatus:
        """Get filesystem operation status."""
        observer = FilesystemObserver()
        return ComponentStatus(
            name="filesystem",
            is_healthy=observer.is_healthy(),
            has_errors=observer.has_errors(),
            status=observer.get_status_json() if format == "json" else observer.get_status_table(),
        )

    async def get_filesystem_stats(self, mount_path: Optional[str] = None) -> dict:
        """
        Get filesystem statistics from RAGFS.

        Args:
            mount_path: Optional specific mount path.

        Returns:
            Statistics data.
        """
        try:
            if self._agfs_client is None:
                logger.debug("RAGFS client not available, returning empty stats")
                return {}

            # Call get_stats on the RAGFS client
            import asyncio

            stats = await asyncio.to_thread(self._agfs_client.get_stats, mount_path)
            return stats
        except Exception as e:
            logger.error(f"Error getting filesystem stats: {e}")
            return {}

    def system(self, ctx: Optional[RequestContext] = None, *, format: str = "table") -> SystemStatus:
        """Get system overall status."""
        components = {
            "queue": self.get_queue_status(format=format),
            "vikingdb": self.get_vikingdb_status(ctx=ctx, format=format),
            "models": self.get_models_status(format=format),
            "lock": self.get_lock_status(format=format),
            "retrieval": self.get_retrieval_status(format=format),
            "filesystem": self.get_filesystem_status(format=format),
        }
        errors = [f"{c.name} has errors" for c in components.values() if c.has_errors]
        return SystemStatus(
            is_healthy=all(c.is_healthy for c in components.values()),
            components=components,
            errors=errors,
        )

    def is_healthy(self) -> bool:
        """Quick health check."""
        if not self._dependencies_ready:
            return False
        return self.system().is_healthy


class DebugService:
    """Debug service - provides system status query and health check."""

    def __init__(
        self,
        vikingdb: Optional[VikingDBManager] = None,
        config: Optional[OpenVikingConfig] = None,
        agfs_client: Optional[Any] = None,
    ):
        self._observer = ObserverService(vikingdb, config, agfs_client)

    def set_dependencies(
        self,
        vikingdb: VikingDBManager,
        config: OpenVikingConfig,
        agfs_client: Optional[Any] = None,
    ) -> None:
        """Set dependencies after initialization."""
        self._observer.set_dependencies(vikingdb, config, agfs_client)

    @property
    def observer(self) -> ObserverService:
        """Get observer service."""
        return self._observer

    def is_healthy(self) -> bool:
        """Quick health check."""
        return self._observer.is_healthy()
