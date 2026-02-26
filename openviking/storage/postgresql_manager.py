# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: Apache-2.0
"""
PostgreSQL Manager - extends PostgreSQLBackend with queue management,
mirroring the interface of VikingDBManager so OpenVikingService can use
either backend transparently.
"""

from typing import TYPE_CHECKING, Optional

from openviking.storage.postgresql_backend import PostgreSQLBackend
from openviking_cli.utils import get_logger
from openviking_cli.utils.config.vectordb_config import VectorDBBackendConfig

if TYPE_CHECKING:
    from openviking.storage.queuefs.embedding_msg import EmbeddingMsg
    from openviking.storage.queuefs.embedding_queue import EmbeddingQueue
    from openviking.storage.queuefs.queue_manager import QueueManager

logger = get_logger(__name__)


class PostgreSQLManager(PostgreSQLBackend):
    """
    PostgreSQL-backed VikingDB manager.

    Wraps PostgreSQLBackend with the same queue-management interface
    as VikingDBManager so OpenVikingService can swap them transparently.
    """

    def __init__(
        self,
        vectordb_config: VectorDBBackendConfig,
        queue_manager: Optional["QueueManager"] = None,
    ):
        """
        Initialize PostgreSQL Manager.

        Args:
            vectordb_config: VectorDB configuration (must have backend='postgresql').
            queue_manager: Shared QueueManager instance for embedding tasks.
        """
        pg_cfg = vectordb_config.postgresql
        if pg_cfg is None:
            raise ValueError("PostgreSQL backend requires 'vectordb.postgresql' config section")

        # Build DSN from config fields or use explicit dsn
        if pg_cfg.dsn:
            dsn = pg_cfg.dsn
        else:
            dsn = (
                f"postgresql://{pg_cfg.user}:{pg_cfg.password}"
                f"@{pg_cfg.host}:{pg_cfg.port}/{pg_cfg.database}"
            )

        dim = vectordb_config.dimension or 1024
        super().__init__(dsn=dsn, vector_dim=dim)

        self._queue_manager = queue_manager
        self._closing = False
        logger.info(
            f"PostgreSQLManager initialized (host={pg_cfg.host}, db={pg_cfg.database}, dim={dim})"
        )

    # =========================================================================
    # Lifecycle
    # =========================================================================

    async def close(self) -> None:
        self._closing = True
        await super().close()

    # =========================================================================
    # Queue management (mirrors VikingDBManager)
    # =========================================================================

    @property
    def is_closing(self) -> bool:
        return self._closing

    @property
    def queue_manager(self) -> Optional["QueueManager"]:
        return self._queue_manager

    @property
    def embedding_queue(self) -> Optional["EmbeddingQueue"]:
        if not self._queue_manager:
            return None
        queue = self._queue_manager.get_queue(self._queue_manager.EMBEDDING)
        from openviking.storage.queuefs.embedding_queue import EmbeddingQueue

        return queue if isinstance(queue, EmbeddingQueue) else None

    @property
    def has_queue_manager(self) -> bool:
        return self._queue_manager is not None

    async def enqueue_embedding_msg(self, embedding_msg: "EmbeddingMsg") -> bool:
        if not embedding_msg:
            logger.warning("Embedding message is None, skipping")
            return False
        if not self._queue_manager:
            raise RuntimeError("Queue manager not initialized")
        try:
            eq = self.embedding_queue
            if not eq:
                raise RuntimeError("Embedding queue not initialized")
            await eq.enqueue(embedding_msg)
            logger.debug(f"Enqueued embedding message: {embedding_msg.id}")
            return True
        except Exception as e:
            logger.error(f"Error enqueuing embedding message: {e}")
            return False

    async def get_embedding_queue_size(self) -> int:
        if not self._queue_manager:
            return 0
        try:
            eq = self._queue_manager.get_queue("embedding")
            return await eq.size()
        except Exception as e:
            logger.error(f"Error getting embedding queue size: {e}")
            return 0

    def get_embedder(self):
        """Get configured embedder (matches VikingDBManager interface)."""
        try:
            from openviking_cli.utils.config import get_openviking_config

            return get_openviking_config().embedding.get_embedder()
        except Exception as e:
            logger.warning(f"Failed to get embedder: {e}")
            return None
