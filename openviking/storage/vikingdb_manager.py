# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: Apache-2.0
"""
VikingDB Manager class that extends VikingVectorIndexBackend with queue management functionality.
"""

from typing import Optional

from openviking.storage.collection_schemas import TextEmbeddingHandler
from openviking.storage.queuefs.embedding_msg import EmbeddingMsg
from openviking.storage.queuefs.embedding_queue import EmbeddingQueue
from openviking.storage.queuefs.queue_manager import init_queue_manager
from openviking.storage.viking_vector_index_backend import VikingVectorIndexBackend
from openviking.utils import get_logger
from openviking.utils.config.agfs_config import AGFSConfig
from openviking.utils.config.vectordb_config import VectorDBBackendConfig

logger = get_logger(__name__)


class VikingDBManager(VikingVectorIndexBackend):
    """
    VikingDB Manager that extends VikingVectorIndexBackend with queue management capabilities.

    This class provides all the functionality of VikingVectorIndexBackend plus:
    - Queue manager initialization and management
    - Embedding queue integration
    - Background processing capabilities

    Usage:
        # In-memory mode with queue management
        manager = VikingDBManager()

        # Local persistent storage with queue management
        manager = VikingDBManager(path="./data/vikingdb", agfs_url="http://localhost:8080")
    """

    def __init__(
        self,
        vectordb_config: VectorDBBackendConfig,
        agfs_config: AGFSConfig,
    ):
        """
        Initialize VikingDB Manager.

        Args:
            vectordb_config: Configuration object for VectorDB backend.
            agfs_config: Configuration object for AGFS (Agent Global File System).
        """
        # Initialize the base VikingVectorIndexBackend without queue management
        super().__init__(
            config=vectordb_config,
        )

        # Queue management specific attributes
        self.agfs_url = agfs_config.url
        self.agfs_timeout = agfs_config.timeout
        self._queue_manager = None
        self._embedding_handler = None
        self._semantic_processor = None

        # Initialize queue manager (but don't start yet — collections must be created first)
        self._init_queue_manager()
        if self._queue_manager:
            self._init_embedding_queue()
            self._init_semantic_queue()

    def _init_queue_manager(self):
        """Initialize queue manager for background processing."""
        if not self.agfs_url:
            logger.warning("AGFS URL not configured, skipping queue manager initialization")
            return
        self._queue_manager = init_queue_manager(
            agfs_url=self.agfs_url,
            timeout=self.agfs_timeout,
        )

    def _init_embedding_queue(self):
        """Initialize embedding queue with TextEmbeddingHandler."""
        if not self._queue_manager:
            logger.warning("Queue manager not initialized, skipping embedding queue setup")
            return

        # Create TextEmbeddingHandler instance with self (VikingDBInterface)
        self._embedding_handler = TextEmbeddingHandler(self)

        # Get embedding queue with the handler, allow creation if not exists
        self._queue_manager.get_queue(
            self._queue_manager.EMBEDDING,
            dequeue_handler=self._embedding_handler,
            allow_create=True,
        )
        logger.info("Embedding queue initialized with TextEmbeddingHandler")

    def _init_semantic_queue(self):
        """Initialize semantic queue with SemanticProcessor, semantic queue is used to get abstract and summary of context data."""
        if not self._queue_manager:
            logger.warning("Queue manager not initialized, skipping semantic queue setup")
            return

        from openviking.storage.queuefs import SemanticProcessor

        # Create SemanticProcessor instance
        self._semantic_processor = SemanticProcessor()

        # Get semantic queue with the handler, allow creation if not exists
        self._queue_manager.get_queue(
            self._queue_manager.SEMANTIC,
            dequeue_handler=self._semantic_processor,
            allow_create=True,
        )
        logger.info("Semantic queue initialized with SemanticProcessor")

    def start_queues(self):
        """Start the queue manager. Call after collections are created."""
        if self._queue_manager:
            self._queue_manager.start()
            logger.info("Queue manager started")

    async def close(self) -> None:
        """Close storage connection and release resources, including queue manager."""
        try:
            # First stop the queue manager to prevent new operations
            if self._queue_manager:
                self._queue_manager.stop()
                self._queue_manager = None
                logger.info("Queue manager stopped")

            # Then close the base backend
            await super().close()

        except Exception as e:
            logger.error(f"Error closing VikingDB manager: {e}")

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
            from openviking.utils.config import get_openviking_config

            config = get_openviking_config()
            return config.embedding.get_embedder()
        except Exception as e:
            logger.warning(f"Failed to get embedder from configuration: {e}")
            return None
