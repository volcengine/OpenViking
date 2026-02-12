# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: Apache-2.0
"""
VikingDB Manager class that extends VikingVectorIndexBackend with queue management functionality.
"""

from typing import Optional

from openviking.storage.queuefs.embedding_msg import EmbeddingMsg
from openviking.storage.queuefs.embedding_queue import EmbeddingQueue
from openviking.storage.queuefs.queue_manager import QueueManager
from openviking.storage.viking_vector_index_backend import VikingVectorIndexBackend
from openviking_cli.utils import get_logger
from openviking_cli.utils.config.agfs_config import AGFSConfig
from openviking_cli.utils.config.vectordb_config import VectorDBBackendConfig

logger = get_logger(__name__)


class VikingDBManager(VikingVectorIndexBackend):
    """
    VikingDB Manager that extends VikingVectorIndexBackend with queue management capabilities.

    This class provides all the functionality of VikingVectorIndexBackend plus:
    - Queue manager integration (via injection)
    - Embedding queue integration
    - Background processing capabilities

    Usage:
        # In-memory mode with queue management
        manager = VikingDBManager(vectordb_config=..., queue_manager=qm)
    """

    def __init__(
        self,
        vectordb_config: VectorDBBackendConfig,
        queue_manager: Optional[QueueManager] = None,
    ):
        """
        Initialize VikingDB Manager.

        Args:
            vectordb_config: Configuration object for VectorDB backend.
            queue_manager: QueueManager instance.
        """
        # Initialize the base VikingVectorIndexBackend without queue management
        super().__init__(
            config=vectordb_config,
        )

        # Queue management specific attributes
        self._queue_manager = queue_manager
        self._closing = False

    async def close(self) -> None:
        """Close storage connection and release resources."""
        self._closing = True
        try:
            # We do NOT stop the queue manager here as it is an injected dependency
            # and should be managed by the creator (OpenVikingService).

            # Then close the base backend
            await super().close()

        except Exception as e:
            logger.error(f"Error closing VikingDB manager: {e}")

    @property
    def is_closing(self) -> bool:
        """Whether the manager is in shutdown flow."""
        return self._closing

    # =========================================================================
    # Queue Management Properties
    # =========================================================================

    @property
    def queue_manager(self):
        """Get the queue manager instance."""
        return self._queue_manager

    @property
    def embedding_queue(self) -> Optional["EmbeddingQueue"]:
        """Get the embedding queue instance."""
        if not self._queue_manager:
            return None
        # get_queue returns EmbeddingQueue when name is QueueManager.EMBEDDING
        queue = self._queue_manager.get_queue(self._queue_manager.EMBEDDING)
        return queue if isinstance(queue, EmbeddingQueue) else None

    @property
    def has_queue_manager(self) -> bool:
        """Check if queue manager is initialized."""
        return self._queue_manager is not None

    # =========================================================================
    # Convenience Methods for Queue Operations
    # =========================================================================

    async def enqueue_embedding_msg(self, embedding_msg: "EmbeddingMsg") -> bool:
        """
        Enqueue an embedding message for processing.

        Args:
            embedding_msg: The EmbeddingMsg object to enqueue

        Returns:
            True if enqueued successfully, False otherwise
        """
        if not embedding_msg:
            logger.warning("Embedding message is None, skipping enqueuing")
            return False

        if not self._queue_manager:
            raise RuntimeError("Queue manager not initialized, cannot enqueue embedding")

        try:
            embedding_queue = self.embedding_queue
            if not embedding_queue:
                raise RuntimeError("Embedding queue not initialized")
            await embedding_queue.enqueue(embedding_msg)
            logger.debug(f"Enqueued embedding message: {embedding_msg.id}")
            return True
        except Exception as e:
            logger.error(f"Error enqueuing embedding message: {e}")
            return False

    async def get_embedding_queue_size(self) -> int:
        """
        Get the current size of the embedding queue.

        Returns:
            The number of messages in the embedding queue
        """
        if not self._queue_manager:
            return 0

        try:
            embedding_queue = self._queue_manager.get_queue("embedding")
            return await embedding_queue.size()
        except Exception as e:
            logger.error(f"Error getting embedding queue size: {e}")
            return 0

    def get_embedder(self):
        """
        Get the embedder instance from configuration.

        Returns:
            Embedder instance or None if not configured
        """
        try:
            from openviking_cli.utils.config import get_openviking_config

            config = get_openviking_config()
            return config.embedding.get_embedder()
        except Exception as e:
            logger.warning(f"Failed to get embedder from configuration: {e}")
            return None
