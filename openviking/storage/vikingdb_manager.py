# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0
"""
VikingDB Manager class that extends VikingVectorIndexBackend with queue management functionality.
"""

from typing import Any, Dict, List, Optional, Tuple

from openviking.server.identity import RequestContext
from openviking.storage.expr import FilterExpr
from openviking.storage.queuefs.embedding_msg import EmbeddingMsg
from openviking.storage.queuefs.embedding_queue import EmbeddingQueue
from openviking.storage.queuefs.queue_manager import QueueManager
from openviking.storage.viking_vector_index_backend import VikingVectorIndexBackend
from openviking_cli.utils import get_logger
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

    def mark_closing(self) -> None:
        """Mark the manager as entering shutdown flow.

        Queue workers may still be draining messages before the backend is
        finally closed. Handlers should check ``is_closing`` and stop writing
        into vector storage to avoid lock contention during rapid restart.
        """
        self._closing = True

    async def close(self) -> None:
        """Close storage connection and release resources."""
        self.mark_closing()
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


class VikingDBManagerProxy:
    """
    租户绑定的 VikingDBManager 代理。

    使用 RequestContext 初始化后，所有方法调用自动携带 ctx，
    无需在每次调用时显式传入。API 与 VikingDBManager 完全兼容。

    示例:
        ```python
        # 初始化
        manager = VikingDBManager(...)
        proxy = VikingDBManagerProxy(manager, ctx)

        # 使用（无需传 ctx，API 完全兼容）
        await proxy.upsert(data)
        results = await proxy.search_similar_memories(...)
        ```
    """

    def __init__(
        self,
        manager: VikingDBManager,
        ctx: RequestContext,
    ):
        """
        初始化租户绑定的 VikingDBManager 代理。

        Args:
            manager: 底层的 VikingDBManager 实例
            ctx: 请求上下文，包含租户信息
        """
        self._manager = manager
        self._ctx = ctx

    @property
    def ctx(self) -> RequestContext:
        """获取绑定的请求上下文。"""
        return self._ctx

    @property
    def manager(self) -> VikingDBManager:
        """获取底层的 VikingDBManager 实例。"""
        return self._manager

    @property
    def collection_name(self) -> str:
        return self._manager.collection_name

    @property
    def mode(self) -> str:
        return self._manager.mode

    # =========================================================================
    # Queue Management Properties（透传）
    # =========================================================================

    @property
    def queue_manager(self):
        return self._manager.queue_manager

    @property
    def embedding_queue(self) -> Optional["EmbeddingQueue"]:
        return self._manager.embedding_queue

    @property
    def has_queue_manager(self) -> bool:
        return self._manager.has_queue_manager

    def mark_closing(self) -> None:
        return self._manager.mark_closing()

    @property
    def is_closing(self) -> bool:
        return self._manager.is_closing

    # =========================================================================
    # Queue Operations（透传）
    # =========================================================================

    async def enqueue_embedding_msg(self, embedding_msg: "EmbeddingMsg") -> bool:
        return await self._manager.enqueue_embedding_msg(embedding_msg)

    async def get_embedding_queue_size(self) -> int:
        return await self._manager.get_embedding_queue_size()

    def get_embedder(self):
        return self._manager.get_embedder()

    # =========================================================================
    # Collection Management（透传）
    # =========================================================================

    async def create_collection(self, name: str, schema: Dict[str, Any]) -> bool:
        return await self._manager.create_collection(name, schema)

    async def drop_collection(self) -> bool:
        return await self._manager.drop_collection()

    async def collection_exists(self) -> bool:
        return await self._manager.collection_exists()

    async def collection_exists_bound(self) -> bool:
        return await self._manager.collection_exists_bound()

    async def get_collection_info(self) -> Optional[Dict[str, Any]]:
        return await self._manager.get_collection_info()

    async def get_collection_meta(self) -> Optional[Dict[str, Any]]:
        return await self._manager.get_collection_meta()

    async def update_collection_description(self, description: str) -> bool:
        return await self._manager.update_collection_description(description)

    # =========================================================================
    # 数据操作 API（自动携带 ctx）
    # =========================================================================

    async def upsert(self, data: Dict[str, Any]) -> str:
        return await self._manager.upsert(data, ctx=self._ctx)

    async def get(self, ids: List[str]) -> List[Dict[str, Any]]:
        return await self._manager.get(ids, ctx=self._ctx)

    async def delete(self, ids: List[str]) -> int:
        return await self._manager.delete(ids, ctx=self._ctx)

    async def exists(self, id: str) -> bool:
        return await self._manager.exists(id, ctx=self._ctx)

    async def fetch_by_uri(self, uri: str) -> Optional[Dict[str, Any]]:
        return await self._manager.fetch_by_uri(uri, ctx=self._ctx)

    async def query(
        self,
        query_vector: Optional[List[float]] = None,
        sparse_query_vector: Optional[Dict[str, float]] = None,
        filter: Optional[Dict[str, Any] | FilterExpr] = None,
        limit: int = 10,
        offset: int = 0,
        output_fields: Optional[List[str]] = None,
        order_by: Optional[str] = None,
        order_desc: bool = False,
    ) -> List[Dict[str, Any]]:
        return await self._manager.query(
            query_vector=query_vector,
            sparse_query_vector=sparse_query_vector,
            filter=filter,
            limit=limit,
            offset=offset,
            output_fields=output_fields,
            order_by=order_by,
            order_desc=order_desc,
            ctx=self._ctx,
        )

    async def search(
        self,
        query_vector: Optional[List[float]] = None,
        sparse_query_vector: Optional[Dict[str, float]] = None,
        filter: Optional[Dict[str, Any] | FilterExpr] = None,
        limit: int = 10,
        offset: int = 0,
        output_fields: Optional[List[str]] = None,
    ) -> List[Dict[str, Any]]:
        return await self._manager.search(
            query_vector=query_vector,
            sparse_query_vector=sparse_query_vector,
            filter=filter,
            limit=limit,
            offset=offset,
            output_fields=output_fields,
            ctx=self._ctx,
        )

    async def filter(
        self,
        filter: Dict[str, Any] | FilterExpr,
        limit: int = 10,
        offset: int = 0,
        output_fields: Optional[List[str]] = None,
        order_by: Optional[str] = None,
        order_desc: bool = False,
    ) -> List[Dict[str, Any]]:
        return await self._manager.filter(
            filter=filter,
            limit=limit,
            offset=offset,
            output_fields=output_fields,
            order_by=order_by,
            order_desc=order_desc,
            ctx=self._ctx,
        )

    async def remove_by_uri(self, uri: str) -> int:
        return await self._manager.remove_by_uri(uri, ctx=self._ctx)

    async def scroll(
        self,
        filter: Optional[Dict[str, Any] | FilterExpr] = None,
        limit: int = 100,
        cursor: Optional[str] = None,
        output_fields: Optional[List[str]] = None,
    ) -> Tuple[List[Dict[str, Any]], Optional[str]]:
        return await self._manager.scroll(
            filter=filter,
            limit=limit,
            cursor=cursor,
            output_fields=output_fields,
            ctx=self._ctx,
        )

    async def count(
        self,
        filter: Optional[Dict[str, Any] | FilterExpr] = None,
    ) -> int:
        return await self._manager.count(filter=filter, ctx=self._ctx)

    async def clear(self) -> bool:
        return await self._manager.clear(ctx=self._ctx)

    async def optimize(self) -> bool:
        return await self._manager.optimize()

    async def close(self) -> None:
        return await self._manager.close()

    async def health_check(self) -> bool:
        return await self._manager.health_check()

    async def get_stats(self) -> Dict[str, Any]:
        return await self._manager.get_stats()

    # =========================================================================
    # Tenant-Aware 方法（自动携带 ctx）
    # =========================================================================

    async def search_in_tenant(
        self,
        query_vector: Optional[List[float]],
        sparse_query_vector: Optional[Dict[str, float]] = None,
        context_type: Optional[str] = None,
        target_directories: Optional[List[str]] = None,
        extra_filter: Optional[FilterExpr | Dict[str, Any]] = None,
        limit: int = 10,
        offset: int = 0,
    ) -> List[Dict[str, Any]]:
        return await self._manager.search_in_tenant(
            self._ctx,
            query_vector=query_vector,
            sparse_query_vector=sparse_query_vector,
            context_type=context_type,
            target_directories=target_directories,
            extra_filter=extra_filter,
            limit=limit,
            offset=offset,
        )

    async def search_global_roots_in_tenant(
        self,
        query_vector: Optional[List[float]],
        sparse_query_vector: Optional[Dict[str, float]] = None,
        context_type: Optional[str] = None,
        target_directories: Optional[List[str]] = None,
        extra_filter: Optional[FilterExpr | Dict[str, Any]] = None,
        limit: int = 10,
    ) -> List[Dict[str, Any]]:
        return await self._manager.search_global_roots_in_tenant(
            self._ctx,
            query_vector=query_vector,
            sparse_query_vector=sparse_query_vector,
            context_type=context_type,
            target_directories=target_directories,
            extra_filter=extra_filter,
            limit=limit,
        )

    async def search_children_in_tenant(
        self,
        parent_uri: str,
        query_vector: Optional[List[float]],
        sparse_query_vector: Optional[Dict[str, float]] = None,
        context_type: Optional[str] = None,
        target_directories: Optional[List[str]] = None,
        extra_filter: Optional[FilterExpr | Dict[str, Any]] = None,
        limit: int = 10,
    ) -> List[Dict[str, Any]]:
        return await self._manager.search_children_in_tenant(
            self._ctx,
            parent_uri=parent_uri,
            query_vector=query_vector,
            sparse_query_vector=sparse_query_vector,
            context_type=context_type,
            target_directories=target_directories,
            extra_filter=extra_filter,
            limit=limit,
        )

    async def search_similar_memories(
        self,
        owner_space: Optional[str],
        category_uri_prefix: str,
        query_vector: List[float],
        limit: int = 5,
    ) -> List[Dict[str, Any]]:
        return await self._manager.search_similar_memories(
            owner_space=owner_space,
            category_uri_prefix=category_uri_prefix,
            query_vector=query_vector,
            limit=limit,
            ctx=self._ctx,
        )

    async def get_context_by_uri(
        self,
        uri: str,
        owner_space: Optional[str] = None,
        level: Optional[int] = None,
        limit: int = 1,
    ) -> List[Dict[str, Any]]:
        return await self._manager.get_context_by_uri(
            uri=uri,
            owner_space=owner_space,
            level=level,
            limit=limit,
            ctx=self._ctx,
        )

    async def delete_account_data(self, account_id: str) -> int:
        return await self._manager.delete_account_data(account_id, ctx=self._ctx)

    async def delete_uris(self, uris: List[str]) -> None:
        return await self._manager.delete_uris(self._ctx, uris)

    async def update_uri_mapping(
        self,
        uri: str,
        new_uri: str,
    ) -> bool:
        return await self._manager.update_uri_mapping(
            self._ctx,
            uri=uri,
            new_uri=new_uri,
        )

    async def increment_active_count(self, uris: List[str]) -> int:
        return await self._manager.increment_active_count(self._ctx, uris)
