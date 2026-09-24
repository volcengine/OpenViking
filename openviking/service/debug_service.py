# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0
"""
Debug Service - provides system status query and health check.
"""
import asyncio
from dataclasses import dataclass
from typing import Any, Dict, List, Optional

from openviking.server.identity import RequestContext
from openviking.storage.observers import (
    FilesystemObserver,
    ModelsObserver,
    QueueObserver,
    RetrievalObserver,
    VikingDBObserver,
)
from openviking.storage.queuefs import get_queue_manager
from openviking.storage.viking_fs import get_viking_fs
from openviking.storage.vikingdb_manager import VikingDBManager
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
        embedding_provider: Optional[Any] = None,
        vlm_resolver: Optional[Any] = None,
    ):
        self._vikingdb = vikingdb
        self._config = config
        self._agfs_client = agfs_client
        self._embedding_provider = embedding_provider
        self._vlm_resolver = vlm_resolver

    def set_dependencies(
        self,
        vikingdb: VikingDBManager,
        config: OpenVikingConfig,
        agfs_client: Optional[Any] = None,
        embedding_provider: Optional[Any] = None,
        vlm_resolver: Optional[Any] = None,
    ) -> None:
        """Set dependencies after initialization."""
        self._vikingdb = vikingdb
        self._config = config
        if agfs_client is not None:
            self._agfs_client = agfs_client
        self._embedding_provider = embedding_provider
        self._vlm_resolver = vlm_resolver

    @property
    def _dependencies_ready(self) -> bool:
        """Check if both vikingdb and config dependencies are set."""
        return self._vikingdb is not None and self._config is not None

    async def get_queue_status_async(self, *, format: str = "table") -> ComponentStatus:
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
            status = (
                await observer.get_status_json_async()
                if format == "json"
                else await observer.get_status_table_async()
            )
            has_errors = await observer.has_active_errors_async()
            is_healthy = not has_errors
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

    def get_queue_status(self, *, format: str = "table") -> ComponentStatus:
        """Synchronous compatibility wrapper for non-async callers."""
        return run_async(self.get_queue_status_async(format=format))

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
        if ctx is not None:
            return run_async(self.account_vikingdb(ctx))
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

        from types import SimpleNamespace

        vlm_instance = None
        embedding_instance = None
        scope = "Node aggregate"
        if self._vlm_resolver is not None and hasattr(
            self._vlm_resolver, "get_node_token_usage"
        ):
            vlm_instance = SimpleNamespace(
                model="node-aggregate",
                provider="aggregate",
                get_token_usage=self._vlm_resolver.get_node_token_usage,
            )
        if self._embedding_provider is not None:
            embedding_instance = SimpleNamespace(
                get_token_usage=self._embedding_provider.get_total_token_usage,
            )

        # Standalone ObserverService instances have no Account providers.
        if vlm_instance is None:
            scope = "Cluster"
            vlm_instance = self._config.vlm.get_vlm_instance()
        if embedding_instance is None and self._embedding_provider is None:
            from openviking.models.embedder.base import _get_token_tracker

            embedding_instance = SimpleNamespace(get_token_usage=_get_token_tracker().to_dict)

        rerank_instance = None
        rerank_config = getattr(self._config, "rerank", None)

        if rerank_config and rerank_config.is_available():
            from openviking.models.rerank import RerankClient

            rerank_instance = RerankClient.from_config(rerank_config)

        observer = ModelsObserver(
            vlm_instance=vlm_instance,
            embedding_instance=embedding_instance,
            rerank_instance=rerank_instance,
        )
        status = observer.get_status_json() if format == "json" else observer.get_status_table()
        if format == "json":
            status = {"scope": scope, **status}
        else:
            status = f"Scope: {scope}\n{status}"
        return ComponentStatus(
            name="models",
            is_healthy=observer.is_healthy(),
            has_errors=observer.has_errors(),
            status=status,
        )

    async def account_models(
        self, ctx: RequestContext, *, format: str = "table"
    ) -> ComponentStatus:
        embedding_dimension = None
        try:
            if self._embedding_provider is None:
                raise RuntimeError("Account embedding provider is not initialized")
            embedding_status = await self._embedding_provider.get_status(ctx.account_id)
            embedding_dimension = embedding_status.dimension
            if self._vlm_resolver is None:
                raise RuntimeError("Account VLM resolver is not initialized")
            vlm = await self._vlm_resolver.get_vlm(ctx.account_id)
            observer = ModelsObserver(
                vlm_instance=vlm,
                embedding_instance=self._embedding_provider.bind(ctx.account_id),
            )
            status = observer.get_status_json() if format == "json" else observer.get_status_table()
            if format == "json":
                status = {
                    "account_id": ctx.account_id,
                    "embedding_dimension": embedding_status.dimension,
                    **status,
                }
            else:
                status = (
                    f"Account: {ctx.account_id}\n"
                    f"Embedding dimension: {embedding_status.dimension}\n{status}"
                )
            return ComponentStatus(
                name="models",
                is_healthy=True,
                has_errors=False,
                status=status,
            )
        except Exception as exc:
            logger.warning(
                "Account model observer failed for account %s: %s",
                ctx.account_id,
                exc,
                exc_info=True,
            )
            return ComponentStatus(
                name="models",
                is_healthy=False,
                has_errors=True,
                status=(
                    {
                        "account_id": ctx.account_id,
                        "embedding_dimension": embedding_dimension,
                        "vlm": [],
                        "embedding": [],
                        "rerank": [],
                        "error": str(exc),
                    }
                    if format == "json"
                    else f"Account: {ctx.account_id}\nModels unavailable: {exc}"
                ),
            )

    async def account_vikingdb(
        self, ctx: RequestContext, *, format: str = "table"
    ) -> ComponentStatus:
        try:
            backend = await self._vikingdb.get_account_backend(ctx.account_id)
            healthy = await backend.health_check()
            count = await backend.count() if healthy else 0
            status = (
                {
                    "backend": backend._mode,
                    "collection": backend._collection_name,
                    "index": backend._index_name,
                    "dimension": backend.vector_dim,
                    "vector_count": count,
                }
                if format == "json"
                else f"Account: {ctx.account_id}\nBackend: {backend._mode}\n"
                f"Collection: {backend._collection_name}\nIndex: {backend._index_name}\n"
                f"Dimension: {backend.vector_dim}\nVector count: {count}"
            )
            return ComponentStatus(
                name="vikingdb",
                is_healthy=healthy,
                has_errors=not healthy,
                status=status,
            )
        except Exception as exc:
            logger.warning(
                "Account VectorDB observer failed for account %s: %s",
                ctx.account_id,
                exc,
                exc_info=True,
            )
            return ComponentStatus(
                name="vikingdb",
                is_healthy=False,
                has_errors=True,
                status=(
                    {
                        "account_id": ctx.account_id,
                        "backend": None,
                        "collection": None,
                        "index": None,
                        "dimension": None,
                        "vector_count": 0,
                        "error": str(exc),
                    }
                    if format == "json"
                    else f"Account: {ctx.account_id}\nVectorDB unavailable: {exc}"
                ),
            )

    async def account_system(
        self, ctx: RequestContext, *, format: str = "table"
    ) -> SystemStatus:
        queue, vikingdb, models, lock, filesystem = await asyncio.gather(
            self.get_queue_status_async(format=format),
            self.account_vikingdb(ctx, format=format),
            self.account_models(ctx, format=format),
            self.get_lock_status_async(format=format),
            self.get_filesystem_status_async(format=format),
        )
        components = {
            "queue": queue,
            "vikingdb": vikingdb,
            "models": models,
            "lock": lock,
            "retrieval": self.get_retrieval_status(format=format),
            "filesystem": filesystem,
        }
        return SystemStatus(
            is_healthy=all(component.is_healthy for component in components.values()),
            components=components,
            errors=[
                f"{component.name} has errors"
                for component in components.values()
                if component.has_errors
            ],
        )

    @property
    def lock(self) -> ComponentStatus:
        """Get lock system status via pathlock_observe snapshot."""
        return self.get_lock_status()

    async def get_lock_status_async(self, *, format: str = "table") -> ComponentStatus:
        """Get lock system status via pathlock_observe snapshot."""
        try:
            viking_fs = get_viking_fs()
            snapshot = await viking_fs._async_agfs.pathlock_observe()
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

    def get_lock_status(self, *, format: str = "table") -> ComponentStatus:
        """Synchronous compatibility wrapper for non-async callers."""
        return run_async(self.get_lock_status_async(format=format))

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
        return self.get_filesystem_status()

    async def get_filesystem_status_async(self, *, format: str = "table") -> ComponentStatus:
        """Get filesystem operation status."""
        observer = FilesystemObserver()
        status = (
            await observer.get_status_json_async()
            if format == "json"
            else await observer.get_status_table_async()
        )
        return ComponentStatus(
            name="filesystem",
            is_healthy=observer.is_healthy(),
            has_errors=observer.has_errors(),
            status=status,
        )

    def get_filesystem_status(self, *, format: str = "table") -> ComponentStatus:
        """Synchronous compatibility wrapper for non-async callers."""
        return run_async(self.get_filesystem_status_async(format=format))

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
        if ctx is not None:
            return run_async(self.account_system(ctx, format=format))
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
        embedding_provider: Optional[Any] = None,
        vlm_resolver: Optional[Any] = None,
    ):
        self._observer = ObserverService(
            vikingdb,
            config,
            agfs_client,
            embedding_provider,
            vlm_resolver,
        )

    def set_dependencies(
        self,
        vikingdb: VikingDBManager,
        config: OpenVikingConfig,
        agfs_client: Optional[Any] = None,
        embedding_provider: Optional[Any] = None,
        vlm_resolver: Optional[Any] = None,
    ) -> None:
        """Set dependencies after initialization."""
        self._observer.set_dependencies(
            vikingdb,
            config,
            agfs_client,
            embedding_provider,
            vlm_resolver,
        )

    @property
    def observer(self) -> ObserverService:
        """Get observer service."""
        return self._observer

    def is_healthy(self) -> bool:
        """Quick health check."""
        return self._observer.is_healthy()
