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
import re
from functools import lru_cache
from typing import Any, Dict, Optional

from openviking.models.embedder.base import EmbedResult
from openviking.models.embedder.volcengine_embedders import is_429_error
from openviking.server.identity import RequestContext, Role
from openviking.storage.errors import CollectionNotFoundError
from openviking.storage.queuefs.embedding_msg import EmbeddingMsg
from openviking.storage.queuefs.named_queue import DequeueHandlerBase
from openviking.storage.viking_vector_index_backend import VikingVectorIndexBackend
from openviking_cli.session.user_id import UserIdentifier
from openviking_cli.utils import get_logger
from openviking_cli.utils.config.open_viking_config import OpenVikingConfig

logger = get_logger(__name__)

_TOKEN_LIMIT_RE = re.compile(
    r"passed\s+(?P<input_tokens>\d+)\s+input tokens.*?maximum input length of\s+"
    r"(?P<max_tokens>\d+)\s+tokens",
    re.IGNORECASE | re.DOTALL,
)
_EMBEDDING_TRUNCATION_HEADROOM = 512


def _parse_input_token_limit_error(error: Exception) -> Optional[tuple[int, int]]:
    """Extract input-token and max-token values from provider errors."""
    match = _TOKEN_LIMIT_RE.search(str(error))
    if not match:
        return None
    return int(match.group("input_tokens")), int(match.group("max_tokens"))


@lru_cache(maxsize=16)
def _get_token_encoder(model_name: str):
    """Best-effort tokenizer lookup for provider-compatible embedding models."""
    try:
        import tiktoken
    except ImportError:
        return None

    if model_name:
        try:
            return tiktoken.encoding_for_model(model_name)
        except KeyError:
            pass

    try:
        return tiktoken.get_encoding("cl100k_base")
    except Exception:
        return None


def _truncate_text_to_token_limit(
    text: str,
    model_name: str,
    max_tokens: int,
    *,
    observed_input_tokens: Optional[int] = None,
) -> str:
    """Trim text to the requested token budget."""
    if max_tokens <= 0 or not text:
        return text

    if observed_input_tokens and observed_input_tokens > max_tokens:
        shrink_ratio = max_tokens / observed_input_tokens
        target_chars = max(1, int(len(text) * shrink_ratio * 0.9))
        if target_chars < len(text):
            return text[:target_chars]

    encoder = _get_token_encoder(model_name)
    if encoder is not None:
        token_ids = encoder.encode(text)
        if len(token_ids) <= max_tokens:
            if observed_input_tokens and observed_input_tokens > max_tokens:
                estimated_tokens = max(1, len(token_ids))
                shrink_ratio = max_tokens / estimated_tokens
                target_chars = max(1, int(len(text) * shrink_ratio * 0.9))
                return text[:target_chars] if target_chars < len(text) else text
            return text
        return encoder.decode(token_ids[:max_tokens])

    estimated_tokens = max(1, len(text.encode("utf-8")) // 2)
    if estimated_tokens <= max_tokens:
        return text

    shrink_ratio = max_tokens / estimated_tokens
    target_chars = max(1, int(len(text) * shrink_ratio * 0.9))
    return text[:target_chars]


def _resolve_embedder_dimension(
    embedder: Any, configured_dimension: int, *, warn_prefix: str
) -> int:
    """Prefer the embedder-reported dimension over config defaults."""
    if embedder and hasattr(embedder, "get_dimension"):
        try:
            actual_dimension = int(embedder.get_dimension())
            if actual_dimension > 0:
                if configured_dimension and configured_dimension != actual_dimension:
                    logger.warning(
                        "%s embedding dimension mismatch: config=%s, embedder=%s. "
                        "Using embedder dimension.",
                        warn_prefix,
                        configured_dimension,
                        actual_dimension,
                    )
                return actual_dimension
        except Exception as exc:
            logger.warning(
                "%s failed to resolve embedding dimension from embedder, "
                "falling back to config=%s: %s",
                warn_prefix,
                configured_dimension,
                exc,
            )

    return configured_dimension


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
    try:
        embedder = config.embedding.get_embedder()
        vector_dim = _resolve_embedder_dimension(
            embedder, vector_dim, warn_prefix="init_context_collection"
        )
    except Exception as exc:
        logger.warning(
            "init_context_collection failed to initialize embedder for dimension "
            "detection, using config dimension=%s: %s",
            vector_dim,
            exc,
        )
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
        self._vector_dim = _resolve_embedder_dimension(
            self._embedder, self._vector_dim, warn_prefix="TextEmbeddingHandler"
        )

    def _embed_with_retry(
        self,
        text: str,
        uri: str = "",
        fallback_text: str = "",
    ) -> EmbedResult:
        """Retry with progressively smaller text when the provider rejects overlong input."""
        current_text = text
        model_name = getattr(self._embedder, "model_name", "")
        last_error: Optional[Exception] = None

        for attempt in range(5):
            try:
                return self._embedder.embed(current_text)
            except Exception as exc:
                last_error = exc
                limit_info = _parse_input_token_limit_error(exc)
                if not limit_info:
                    raise

                input_tokens, max_tokens = limit_info
                retry_budget = max(
                    1,
                    int((max_tokens - _EMBEDDING_TRUNCATION_HEADROOM) * (0.85**attempt)),
                )
                truncated_text = _truncate_text_to_token_limit(
                    current_text,
                    model_name,
                    retry_budget,
                    observed_input_tokens=input_tokens,
                )
                if len(truncated_text) >= len(current_text):
                    fallback_chars = max(1, int(len(current_text) * 0.5))
                    truncated_text = current_text[:fallback_chars]
                if len(truncated_text) >= len(current_text):
                    raise exc

                logger.warning(
                    "Embedding input too long for uri=%s model=%s (%s > %s tokens). "
                    "Attempt %s/5: truncating to ~%s tokens and retrying.",
                    uri or "<unknown>",
                    model_name or "<unknown>",
                    input_tokens,
                    max_tokens,
                    attempt + 1,
                    retry_budget,
                )
                current_text = truncated_text

        if fallback_text and fallback_text.strip() and fallback_text.strip() != text.strip():
            logger.warning(
                "Embedding retries exhausted for uri=%s model=%s. Falling back to abstract/summary text.",
                uri or "<unknown>",
                model_name or "<unknown>",
            )
            return self._embedder.embed(fallback_text)

        raise RuntimeError("Failed to embed text after token-limit retries") from last_error

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
                try:
                    # embed() is a blocking HTTP call; offload to thread pool to avoid
                    # blocking the event loop and allow real concurrency.
                    result: EmbedResult = await asyncio.to_thread(
                        self._embed_with_retry,
                        embedding_msg.message,
                        inserted_data.get("uri", ""),
                        inserted_data.get("abstract", ""),
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
                        logger.debug(
                            f"Generated sparse vector with {len(result.sparse_vector)} terms"
                        )
                except Exception as e:
                    error_msg = f"Failed to generate embedding: {e}"
                    logger.error(error_msg)

                    if is_429_error(e) and self._vikingdb.has_queue_manager:
                        try:
                            await self._vikingdb.enqueue_embedding_msg(embedding_msg)
                            logger.info(f"Re-enqueued embedding message: {embedding_msg.id}")
                            self.report_success()
                            return None
                        except Exception as requeue_err:
                            logger.error(f"Failed to re-enqueue message: {requeue_err}")

                    self.report_error(error_msg, data)
                    return None
            else:
                error_msg = "Embedder not initialized, skipping vector generation"
                logger.warning(error_msg)
                self.report_error(error_msg, data)
                return None

            # Write to vector database
            try:
                # Ensure vector DB has deterministic IDs per semantic layer.
                uri = inserted_data.get("uri")
                account_id = inserted_data.get("account_id", "default")
                logger.debug(
                    f"[TextEmbeddingHandler] Preparing to upsert, uri={uri}, account_id={account_id}, inserted_data={inserted_data}"
                )

                if uri:
                    seed_uri = self._seed_uri_for_id(uri, inserted_data.get("level", 2))
                    id_seed = f"{account_id}:{seed_uri}"
                    inserted_data["id"] = hashlib.md5(id_seed.encode("utf-8")).hexdigest()

                # Create RequestContext from account_id
                user = UserIdentifier(account_id=account_id, user_id="default", agent_id="default")
                ctx = RequestContext(user=user, role=Role.ROOT)
                logger.debug(
                    f"[TextEmbeddingHandler] Created ctx for upsert, ctx.account_id={ctx.account_id}, ctx.user={ctx.user}"
                )

                record_id = await self._vikingdb.upsert(inserted_data, ctx=ctx)
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
