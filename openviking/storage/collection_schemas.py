# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0
"""
Collection schema definitions for OpenViking.

Provides centralized schema definitions and factory functions for creating collections,
similar to how init_viking_fs encapsulates VikingFS initialization.
"""

import asyncio
import hashlib
import json
import threading
import time
from contextlib import nullcontext
from dataclasses import dataclass
from typing import Any, Dict, List, Optional

from openviking.models.embedder.base import EmbedResult, embed_compat
from openviking.server.identity import RequestContext, Role
from openviking.storage.errors import CollectionNotFoundError
from openviking.storage.queuefs.embedding_msg import EmbeddingMsg
from openviking.storage.queuefs.named_queue import DequeueHandlerBase
from openviking.storage.viking_vector_index_backend import VikingVectorIndexBackend
from openviking.telemetry import bind_telemetry, resolve_telemetry
from openviking.telemetry.request_wait_tracker import get_request_wait_tracker
from openviking.utils.circuit_breaker import (
    CircuitBreaker,
    CircuitBreakerOpen,
    classify_api_error,
)
from openviking_cli.session.user_id import UserIdentifier
from openviking_cli.utils import get_logger
from openviking_cli.utils.config.open_viking_config import OpenVikingConfig

logger = get_logger(__name__)


@dataclass
class RequestQueueStats:
    processed: int = 0
    error_count: int = 0


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
        fields = [
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
        ]
        fields.extend(
            [
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
            ]
        )
        scalar_index = [
            "uri",
            "type",
            "context_type",
            "created_at",
            "updated_at",
            "active_count",
        ]
        scalar_index.extend(
            [
                "level",
                "name",
                "tags",
                "account_id",
                "owner_space",
            ]
        )
        return {
            "CollectionName": name,
            "Description": "Unified context collection",
            "Fields": fields,
            "ScalarIndex": scalar_index,
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
    if not name:
        raise ValueError("Vector DB collection name is required")
    collection_name = name
    schema = CollectionSchemas.context_collection(collection_name, vector_dim)
    return await storage.create_collection(collection_name, schema)


class TextEmbeddingHandler(DequeueHandlerBase):
    """
    Text embedding handler that converts text messages to embedding vectors
    and writes results to vector database.

    This handler processes EmbeddingMsg objects where message is a string,
    converts the text to embedding vectors using the configured embedder,
    and writes the complete data including vector to the vector database.

    Supports both dense and sparse embeddings based on configuration.
    """

    _request_stats_lock = threading.Lock()
    _request_stats_by_telemetry_id: Dict[str, RequestQueueStats] = {}
    _request_stats_order: List[str] = []
    _max_cached_stats = 1024

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
        breaker_cfg = config.embedding.circuit_breaker
        self._circuit_breaker = CircuitBreaker(
            failure_threshold=breaker_cfg.failure_threshold,
            reset_timeout=breaker_cfg.reset_timeout,
            max_reset_timeout=breaker_cfg.max_reset_timeout,
        )
        self._breaker_open_last_log_at = 0.0
        self._breaker_open_suppressed_count = 0
        self._breaker_open_log_interval = 30.0

    def _initialize_embedder(self, config: "OpenVikingConfig"):
        """Initialize the embedder instance from config."""
        self._embedder = config.embedding.get_embedder()

    def _log_breaker_open_reenqueue_summary(self) -> None:
        """Log a throttled warning when embeddings are re-enqueued due to an open circuit breaker."""
        now = time.monotonic()
        if self._breaker_open_last_log_at == 0.0:
            logger.warning("Embedding circuit breaker is open; re-enqueueing messages")
            self._breaker_open_last_log_at = now
            self._breaker_open_suppressed_count = 0
            return

        self._breaker_open_suppressed_count += 1
        if now - self._breaker_open_last_log_at >= self._breaker_open_log_interval:
            logger.warning("Embedding circuit breaker is open; re-enqueueing messages")
            self._breaker_open_last_log_at = now
            self._breaker_open_suppressed_count = 0

    @classmethod
    def _merge_request_stats(
        cls, telemetry_id: str, processed: int = 0, error_count: int = 0
    ) -> None:
        if not telemetry_id:
            return
        with cls._request_stats_lock:
            stats = cls._request_stats_by_telemetry_id.setdefault(telemetry_id, RequestQueueStats())
            stats.processed += processed
            stats.error_count += error_count
            cls._request_stats_order.append(telemetry_id)
            if len(cls._request_stats_order) > cls._max_cached_stats:
                old_telemetry_id = cls._request_stats_order.pop(0)
                if (
                    old_telemetry_id != telemetry_id
                    and old_telemetry_id in cls._request_stats_by_telemetry_id
                ):
                    cls._request_stats_by_telemetry_id.pop(old_telemetry_id, None)

    @classmethod
    def consume_request_stats(cls, telemetry_id: str) -> Optional[RequestQueueStats]:
        if not telemetry_id:
            return None
        with cls._request_stats_lock:
            return cls._request_stats_by_telemetry_id.pop(telemetry_id, None)

    @staticmethod
    def _seed_uri_for_id(uri: str, level: Any) -> str:
        """Build deterministic id seed URI from canonical uri + hierarchy level."""
        try:
            level_int = int(level)
        except (TypeError, ValueError):
            level_int = 2

        if level_int == 0:
            return uri if uri.endswith("/.abstract.md") else f"{uri}/.abstract.md"
        if level_int == 1:
            return uri if uri.endswith("/.overview.md") else f"{uri}/.overview.md"
        return uri

    async def on_dequeue(self, data: Optional[Dict[str, Any]]) -> Optional[Dict[str, Any]]:
        """Process dequeued message and add embedding vector(s)."""
        if not data:
            return None

        embedding_msg: Optional[EmbeddingMsg] = None
        collector = None
        report_success = False
        report_error_args: Optional[tuple[str, Optional[Dict[str, Any]]]] = None
        request_failed_message: Optional[str] = None
        try:
            queue_data = json.loads(data["data"])
            # Parse EmbeddingMsg from data
            embedding_msg = EmbeddingMsg.from_dict(queue_data)
            inserted_data = embedding_msg.context_data
            collector = resolve_telemetry(embedding_msg.telemetry_id)
            telemetry_ctx = bind_telemetry(collector) if collector is not None else nullcontext()

            with telemetry_ctx:
                if self._vikingdb.is_closing:
                    logger.debug("Skip embedding dequeue during shutdown")
                    self._merge_request_stats(embedding_msg.telemetry_id, processed=1)
                    self._record_request_success(embedding_msg)
                    report_success = True
                    return None

                # Only process string messages
                if not isinstance(embedding_msg.message, str):
                    logger.debug(f"Skipping non-string message type: {type(embedding_msg.message)}")
                    self._merge_request_stats(embedding_msg.telemetry_id, processed=1)
                    self._record_request_success(embedding_msg)
                    report_success = True
                    return data

                # Circuit breaker: if API is known-broken, re-enqueue and wait
                try:
                    self._circuit_breaker.check()
                    self._breaker_open_last_log_at = 0.0
                    self._breaker_open_suppressed_count = 0
                except CircuitBreakerOpen:
                    self._log_breaker_open_reenqueue_summary()
                    if self._vikingdb.has_queue_manager:
                        wait = self._circuit_breaker.retry_after
                        if wait > 0:
                            await asyncio.sleep(wait)
                        await self._vikingdb.enqueue_embedding_msg(embedding_msg)
                        report_success = True
                        return None
                    # No queue manager — cannot re-enqueue, drop with error
                    request_failed_message = "Circuit breaker open and no queue manager"
                    report_error_args = ("Circuit breaker open and no queue manager", data)
                    return None

                # Initialize embedder if not already initialized
                if not self._embedder:
                    from openviking_cli.utils.config import get_openviking_config

                    config = get_openviking_config()
                    self._initialize_embedder(config)

                # Generate embedding vector(s)
                if self._embedder:
                    try:
                        import time as _time

                        _embed_t0 = _time.monotonic()
                        result: EmbedResult = await embed_compat(
                            self._embedder, embedding_msg.message
                        )
                        _embed_elapsed = _time.monotonic() - _embed_t0
                        try:
                            from openviking.storage.observers.prometheus_observer import (
                                get_prometheus_observer,
                            )

                            _prom = get_prometheus_observer()
                            if _prom is not None:
                                _prom.record_embedding(_embed_elapsed)
                        except Exception:
                            pass
                    except Exception as embed_err:
                        error_msg = f"Failed to generate embedding: {embed_err}"
                        error_class = classify_api_error(embed_err)

                        if error_class == "permanent":
                            logger.critical(error_msg)
                            self._circuit_breaker.record_failure(embed_err)
                            self._merge_request_stats(embedding_msg.telemetry_id, error_count=1)
                            request_failed_message = error_msg
                            report_error_args = (error_msg, data)
                            return None

                        # Transient or unknown — re-enqueue for retry
                        logger.warning(error_msg)
                        self._circuit_breaker.record_failure(embed_err)
                        if self._vikingdb.has_queue_manager:
                            try:
                                await self._vikingdb.enqueue_embedding_msg(embedding_msg)
                                logger.info(
                                    f"Re-enqueued embedding message after transient error: {embedding_msg.id}"
                                )
                                report_success = True
                                return None
                            except Exception as requeue_err:
                                logger.error(f"Failed to re-enqueue message: {requeue_err}")

                        self._merge_request_stats(embedding_msg.telemetry_id, error_count=1)
                        request_failed_message = error_msg
                        report_error_args = (error_msg, data)
                        return None

                    # Add dense vector
                    if result.dense_vector:
                        inserted_data["vector"] = result.dense_vector
                        # Validate vector dimension
                        if len(result.dense_vector) != self._vector_dim:
                            error_msg = f"Dense vector dimension mismatch: expected {self._vector_dim}, got {len(result.dense_vector)}"
                            logger.error(error_msg)
                            self._merge_request_stats(embedding_msg.telemetry_id, error_count=1)
                            request_failed_message = error_msg
                            report_error_args = (error_msg, data)
                            return None

                    # Add sparse vector if present
                    if result.sparse_vector:
                        inserted_data["sparse_vector"] = result.sparse_vector
                        logger.debug(
                            f"Generated sparse vector with {len(result.sparse_vector)} terms"
                        )
                else:
                    error_msg = "Embedder not initialized, skipping vector generation"
                    logger.warning(error_msg)
                    self._merge_request_stats(embedding_msg.telemetry_id, error_count=1)
                    request_failed_message = error_msg
                    report_error_args = (error_msg, data)
                    return None

                # Write to vector database
                try:
                    # Ensure vector DB has deterministic IDs per semantic layer.
                    uri = inserted_data.get("uri")
                    account_id = inserted_data.get("account_id", "default")
                    if uri:
                        seed_uri = self._seed_uri_for_id(uri, inserted_data.get("level", 2))
                        id_seed = f"{account_id}:{seed_uri}"
                        inserted_data["id"] = hashlib.md5(id_seed.encode("utf-8")).hexdigest()

                    user = UserIdentifier(
                        account_id=account_id,
                        user_id="default",
                        agent_id="default",
                    )
                    ctx = RequestContext(user=user, role=Role.ROOT)
                    record_id = await self._vikingdb.upsert(inserted_data, ctx=ctx)
                    if record_id:
                        logger.debug(
                            f"Successfully wrote embedding to database: {record_id} abstract {inserted_data['abstract']} vector {inserted_data['vector'][:5]}"
                        )
                except CollectionNotFoundError as db_err:
                    # During shutdown, queue workers may finish one dequeued item.
                    if self._vikingdb.is_closing:
                        logger.debug(f"Skip embedding write during shutdown: {db_err}")
                        self._merge_request_stats(embedding_msg.telemetry_id, processed=1)
                        self._record_request_success(embedding_msg)
                        report_success = True
                        return None
                    logger.error(f"Failed to write to vector database: {db_err}")
                    self._merge_request_stats(embedding_msg.telemetry_id, error_count=1)
                    request_failed_message = str(db_err)
                    report_error_args = (str(db_err), data)
                    return None
                except Exception as db_err:
                    if self._vikingdb.is_closing:
                        logger.debug(f"Skip embedding write during shutdown: {db_err}")
                        self._merge_request_stats(embedding_msg.telemetry_id, processed=1)
                        self._record_request_success(embedding_msg)
                        report_success = True
                        return None
                    logger.error(f"Failed to write to vector database: {db_err}")
                    import traceback

                    traceback.print_exc()
                    self._merge_request_stats(embedding_msg.telemetry_id, error_count=1)
                    request_failed_message = str(db_err)
                    report_error_args = (str(db_err), data)
                    return None

                self._merge_request_stats(embedding_msg.telemetry_id, processed=1)
                self._record_request_success(embedding_msg)
                report_success = True
                self._circuit_breaker.record_success()
                return inserted_data

        except Exception as e:
            logger.error(f"Error processing embedding message: {e}")
            import traceback

            traceback.print_exc()
            if embedding_msg is not None:
                self._merge_request_stats(embedding_msg.telemetry_id, error_count=1)
                request_failed_message = str(e)
            report_error_args = (str(e), data)
            return None
        finally:
            if embedding_msg is not None and request_failed_message is not None:
                self._record_request_failure(embedding_msg, request_failed_message)
            if embedding_msg and embedding_msg.semantic_msg_id:
                from openviking.storage.queuefs.embedding_tracker import EmbeddingTaskTracker

                tracker = EmbeddingTaskTracker.get_instance()
                try:
                    await tracker.decrement(embedding_msg.semantic_msg_id)
                except Exception as tracker_err:
                    logger.warning(f"Failed to decrement embedding tracker: {tracker_err}")
            if report_error_args is not None:
                self.report_error(*report_error_args)
            elif report_success:
                self.report_success()

    @staticmethod
    def _record_request_success(embedding_msg: EmbeddingMsg) -> None:
        tracker = get_request_wait_tracker()
        if embedding_msg.semantic_msg_id:
            tracker.record_embedding_processed(embedding_msg.telemetry_id)
        else:
            tracker.mark_embedding_done(embedding_msg.telemetry_id, embedding_msg.id)

    @staticmethod
    def _record_request_failure(embedding_msg: EmbeddingMsg, message: str) -> None:
        tracker = get_request_wait_tracker()
        if embedding_msg.semantic_msg_id:
            tracker.record_embedding_error(embedding_msg.telemetry_id, message)
        else:
            tracker.mark_embedding_failed(embedding_msg.telemetry_id, embedding_msg.id, message)
