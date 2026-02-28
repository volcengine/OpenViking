# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: Apache-2.0
"""
Collection schema definitions for OpenViking.

Provides centralized schema definitions and factory functions for creating collections,
similar to how init_viking_fs encapsulates VikingFS initialization.
"""

import asyncio
import hashlib
import json
from typing import Any, Dict, Optional

from openviking.models.embedder.base import EmbedResult
from openviking.storage.errors import CollectionNotFoundError
from openviking.storage.queuefs.embedding_msg import EmbeddingMsg
from openviking.storage.queuefs.named_queue import DequeueHandlerBase
from openviking.storage.viking_vector_index_backend import VikingVectorIndexBackend
from openviking_cli.utils import get_logger
from openviking_cli.utils.config.open_viking_config import OpenVikingConfig

logger = get_logger(__name__)


class CollectionSchemas:
    """
    Centralized collection schema definitions.
    """

    @staticmethod
    def context_collection(name: str, vector_dim: int) -> Dict[str, Any]:
        """
        Get the schema for the unified context collection.

        Args:
            name: Collection name
            vector_dim: Dimension of the dense vector field

        Returns:
            Schema definition for the context collection
        """
        return {
            "CollectionName": name,
            "Description": "Unified context collection",
            "Fields": [
                {"FieldName": "id", "FieldType": "string", "IsPrimaryKey": True},
                {"FieldName": "uri", "FieldType": "path"},
                # type 字段：当前版本未使用，保留用于未来扩展
                # 预留用于表示资源的具体类型，如 "file", "directory", "image", "video", "repository" 等
                {"FieldName": "type", "FieldType": "string"},
                # context_type 字段：区分上下文的大类
                # 枚举值："resource"（资源，默认）, "memory"（记忆）, "skill"（技能）
                # 推导规则：
                #   - URI 以 viking://agent/skills 开头 → "skill"
                #   - URI 包含 "memories" → "memory"
                #   - 其他情况 → "resource"
                {"FieldName": "context_type", "FieldType": "string"},
                {"FieldName": "vector", "FieldType": "vector", "Dim": vector_dim},
                {"FieldName": "sparse_vector", "FieldType": "sparse_vector"},
                {"FieldName": "created_at", "FieldType": "date_time"},
                {"FieldName": "updated_at", "FieldType": "date_time"},
                {"FieldName": "active_count", "FieldType": "int64"},
                {"FieldName": "parent_uri", "FieldType": "path"},
                # level 字段：区分 L0/L1/L2 层级
                # 枚举值：
                #   - 0 = L0（abstract，摘要）
                #   - 1 = L1（overview，概览）
                #   - 2 = L2（detail/content，详情/内容，默认）
                # URI 命名规则：
                #   - level=0: {目录}/.abstract.md
                #   - level=1: {目录}/.overview.md
                #   - level=2: {文件路径}
                {"FieldName": "level", "FieldType": "int64"},
                {"FieldName": "name", "FieldType": "string"},
                {"FieldName": "description", "FieldType": "string"},
                {"FieldName": "tags", "FieldType": "string"},
                {"FieldName": "abstract", "FieldType": "string"},
                {"FieldName": "account_id", "FieldType": "string"},
                {"FieldName": "owner_space", "FieldType": "string"},
            ],
            "ScalarIndex": [
                "uri",
                "type",
                "context_type",
                "created_at",
                "updated_at",
                "active_count",
                "parent_uri",
                "level",
                "name",
                "tags",
                "account_id",
                "owner_space",
            ],
        }


async def init_context_collection(storage) -> bool:
    """
    Initialize the context collection with proper schema.

    Args:
        storage: Storage interface instance

    Returns:
        True if collection was created, False if already exists
    """
    from openviking_cli.utils.config import get_openviking_config

    config = get_openviking_config()
    name = config.storage.vectordb.name
    vector_dim = config.embedding.dimension
    schema = CollectionSchemas.context_collection(name, vector_dim)
    return await storage.create_collection(name, schema)


class TextEmbeddingHandler(DequeueHandlerBase):
    """
    Text embedding handler that converts text messages to embedding vectors
    and writes results to vector database.

    This handler processes EmbeddingMsg objects where message is a string,
    converts the text to embedding vectors using the configured embedder,
    and writes the complete data including vector to the vector database.

    Supports both dense and sparse embeddings based on configuration.
    """

    def __init__(self, vikingdb: VikingVectorIndexBackend):
        """Initialize the text embedding handler.

        Args:
            vikingdb: VikingVectorIndexBackend instance for writing to vector database
        """
        from openviking_cli.utils.config import get_openviking_config

        self._vikingdb = vikingdb
        self._embedder = None
        config = get_openviking_config()
        self._collection_name = config.storage.vectordb.name
        self._vector_dim = config.embedding.dimension
        self._initialize_embedder(config)

    def _initialize_embedder(self, config: "OpenVikingConfig"):
        """Initialize the embedder instance from config."""
        self._embedder = config.embedding.get_embedder()

    async def on_dequeue(self, data: Optional[Dict[str, Any]]) -> Optional[Dict[str, Any]]:
        """Process dequeued message and add embedding vector(s)."""
        if not data:
            return None

        try:
            queue_data = json.loads(data["data"])
            # Parse EmbeddingMsg from data
            embedding_msg = EmbeddingMsg.from_dict(queue_data)
            inserted_data = embedding_msg.context_data

            if self._vikingdb.is_closing:
                logger.debug("Skip embedding dequeue during shutdown")
                self.report_success()
                return None

            # Only process string messages
            if not isinstance(embedding_msg.message, str):
                logger.debug(f"Skipping non-string message type: {type(embedding_msg.message)}")
                self.report_success()
                return data

            # Initialize embedder if not already initialized
            if not self._embedder:
                from openviking_cli.utils.config import get_openviking_config

                config = get_openviking_config()
                self._initialize_embedder(config)

            # Generate embedding vector(s)
            if self._embedder:
                # embed() is a blocking HTTP call; offload to thread pool to avoid
                # blocking the event loop and allow real concurrency.
                result: EmbedResult = await asyncio.to_thread(
                    self._embedder.embed, embedding_msg.message
                )

                # Add dense vector
                if result.dense_vector:
                    inserted_data["vector"] = result.dense_vector
                    # Validate vector dimension
                    if len(result.dense_vector) != self._vector_dim:
                        error_msg = f"Dense vector dimension mismatch: expected {self._vector_dim}, got {len(result.dense_vector)}"
                        logger.error(error_msg)
                        self.report_error(error_msg, data)
                        return None

                # Add sparse vector if present
                if result.sparse_vector:
                    inserted_data["sparse_vector"] = result.sparse_vector
                    logger.debug(f"Generated sparse vector with {len(result.sparse_vector)} terms")
            else:
                error_msg = "Embedder not initialized, skipping vector generation"
                logger.warning(error_msg)
                self.report_error(error_msg, data)
                return None

            # Write to vector database
            try:
                # Ensure vector DB has at most one record per URI.
                uri = inserted_data.get("uri")
                if uri:
                    account_id = inserted_data.get("account_id", "default")
                    id_seed = f"{account_id}:{uri}"
                    inserted_data["id"] = hashlib.md5(id_seed.encode("utf-8")).hexdigest()

                record_id = await self._vikingdb.upsert(inserted_data)
                if record_id:
                    logger.debug(
                        f"Successfully wrote embedding to database: {record_id} abstract {inserted_data['abstract']} vector {inserted_data['vector'][:5]}"
                    )
            except CollectionNotFoundError as db_err:
                # During shutdown, queue workers may finish one dequeued item.
                if self._vikingdb.is_closing:
                    logger.debug(f"Skip embedding write during shutdown: {db_err}")
                    self.report_success()
                    return None
                logger.error(f"Failed to write to vector database: {db_err}")
                self.report_error(str(db_err), data)
                return None
            except Exception as db_err:
                if self._vikingdb.is_closing:
                    logger.debug(f"Skip embedding write during shutdown: {db_err}")
                    self.report_success()
                    return None
                logger.error(f"Failed to write to vector database: {db_err}")
                import traceback

                traceback.print_exc()
                self.report_error(str(db_err), data)
                return None

            self.report_success()
            return inserted_data

        except Exception as e:
            logger.error(f"Error processing embedding message: {e}")
            import traceback

            traceback.print_exc()
            self.report_error(str(e), data)
            return None
