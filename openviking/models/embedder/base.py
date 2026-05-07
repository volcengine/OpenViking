# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0
import asyncio
import logging
import random
import time
import weakref
from abc import ABC, abstractmethod
from dataclasses import dataclass
from threading import Lock
from typing import Any, Awaitable, Callable, Dict, List, Optional, TypeVar

from openviking.telemetry import get_current_telemetry
from openviking.utils.model_retry import retry_async, retry_sync

T = TypeVar("T")
logger = logging.getLogger(__name__)


_token_tracker_instance = None
_ASYNC_EMBED_SEMAPHORES: "weakref.WeakKeyDictionary[asyncio.AbstractEventLoop, Dict[int, asyncio.Semaphore]]" = weakref.WeakKeyDictionary()
_ASYNC_EMBED_LOCK = Lock()


def _get_async_embed_semaphore(limit: int) -> asyncio.Semaphore:
    loop = asyncio.get_running_loop()
    normalized_limit = max(1, limit)
    with _ASYNC_EMBED_LOCK:
        semaphores_by_limit = _ASYNC_EMBED_SEMAPHORES.setdefault(loop, {})
        semaphore = semaphores_by_limit.get(normalized_limit)
        if semaphore is None:
            semaphore = asyncio.Semaphore(normalized_limit)
            semaphores_by_limit[normalized_limit] = semaphore
        return semaphore


def _get_token_tracker():
    """Lazy import to avoid circular dependency."""
    global _token_tracker_instance
    if _token_tracker_instance is None:
        from openviking.models.vlm.token_usage import TokenUsageTracker

        _token_tracker_instance = TokenUsageTracker()
    return _token_tracker_instance


async def embed_compat(embedder: Any, text: str, *, is_query: bool = False) -> "EmbedResult":
    """Call async embedding when available, otherwise fall back to sync embed()."""
    from openviking.telemetry import bind_telemetry_stage

    stage = "embed_query" if is_query else "embed_resource"
    embed_async = getattr(embedder, "embed_async", None)
    with bind_telemetry_stage(stage):
        if callable(embed_async):
            return await embed_async(text, is_query=is_query)
        return embedder.embed(text, is_query=is_query)


async def embed_batch_compat(
    embedder: Any, texts: List[str], *, is_query: bool = False
) -> List["EmbedResult"]:
    """Call async batch embedding when available, otherwise fall back to sync embed_batch()."""
    from openviking.telemetry import bind_telemetry_stage

    stage = "embed_query" if is_query else "embed_resource"
    embed_batch_async = getattr(embedder, "embed_batch_async", None)
    with bind_telemetry_stage(stage):
        if callable(embed_batch_async):
            return await embed_batch_async(texts, is_query=is_query)
        return embedder.embed_batch(texts, is_query=is_query)


def truncate_and_normalize(embedding: List[float], dimension: Optional[int]) -> List[float]:
    """Truncate and L2 normalize embedding vector

    Args:
        embedding: The embedding vector to process
        dimension: Target dimension for truncation, None to skip truncation

    Returns:
        Processed embedding vector
    """
    if not dimension or len(embedding) <= dimension:
        return embedding

    import math

    embedding = embedding[:dimension]
    norm = math.sqrt(sum(x**2 for x in embedding))
    if norm > 0:
        embedding = [x / norm for x in embedding]
    return embedding


@dataclass
class EmbedResult:
    """Embedding result that supports dense, sparse, or hybrid vectors

    Attributes:
        dense_vector: Dense vector in List[float] format
        sparse_vector: Sparse vector in Dict[str, float] format, e.g. {'token1': 0.5, 'token2': 0.3}
    """

    dense_vector: Optional[List[float]] = None
    sparse_vector: Optional[Dict[str, float]] = None

    @property
    def is_dense(self) -> bool:
        """Check if result contains dense vector"""
        return self.dense_vector is not None

    @property
    def is_sparse(self) -> bool:
        """Check if result contains sparse vector"""
        return self.sparse_vector is not None

    @property
    def is_hybrid(self) -> bool:
        """Check if result is hybrid (contains both dense and sparse vectors)"""
        return self.dense_vector is not None and self.sparse_vector is not None


class EmbedderBase(ABC):
    """Base class for all embedders

    Provides unified embedding interface supporting dense, sparse, and hybrid modes.
    """

    def __init__(self, model_name: str, config: Optional[Dict[str, Any]] = None):
        """Initialize embedder

        Args:
            model_name: Model name
            config: Configuration dict containing api_key, api_base, etc.
        """
        self.model_name = model_name
        self.config = config or {}
        self.max_retries = int(self.config.get("max_retries", 3))
        self.max_concurrent = int(self.config.get("max_concurrent", 10))
        self.provider = self.config.get("provider", "unknown")

        # Token usage tracking
        self._token_tracker = _get_token_tracker()
        self._active_call_started_at: float | None = None

    @abstractmethod
    def embed(self, text: str, is_query: bool = False) -> EmbedResult:
        """Embed single text

        Args:
            text: Input text
            is_query: Flag to indicate if this is a query embedding

        Returns:
            EmbedResult: Embedding result containing dense_vector, sparse_vector, or both
        """
        pass

    def embed_batch(self, texts: List[str], is_query: bool = False) -> List[EmbedResult]:
        """Batch embedding (default implementation loops, subclasses can override for optimization)

        Args:
            texts: List of texts
            is_query: Flag to indicate if these are query embeddings

        Returns:
            List[EmbedResult]: List of embedding results
        """
        return [self.embed(text, is_query=is_query) for text in texts]

    def embed_query(self, text: str) -> EmbedResult:
        """Embed query text with explicit retrieval-side semantics."""
        return self.embed(text, is_query=True)

    def embed_document(self, text: str) -> EmbedResult:
        """Embed document text with explicit indexing-side semantics."""
        return self.embed(text, is_query=False)

    def embed_batch_query(self, texts: List[str]) -> List[EmbedResult]:
        """Batch embed query texts."""
        return self.embed_batch(texts, is_query=True)

    def embed_batch_document(self, texts: List[str]) -> List[EmbedResult]:
        """Batch embed document texts."""
        return self.embed_batch(texts, is_query=False)

    async def embed_async(self, text: str, is_query: bool = False) -> EmbedResult:
        """Async embed single text.

        Subclasses should override this with a non-blocking implementation.
        The default implementation preserves compatibility for test doubles and
        third-party embedders that only implement the sync interface.
        """
        return self.embed(text, is_query=is_query)

    async def embed_batch_async(
        self, texts: List[str], is_query: bool = False
    ) -> List[EmbedResult]:
        """Async batch embedding."""
        results: List[EmbedResult] = []
        for text in texts:
            results.append(await self.embed_async(text, is_query=is_query))
        return results

    async def embed_query_async(self, text: str) -> EmbedResult:
        return await self.embed_async(text, is_query=True)

    async def embed_document_async(self, text: str) -> EmbedResult:
        return await self.embed_async(text, is_query=False)

    async def embed_batch_query_async(self, texts: List[str]) -> List[EmbedResult]:
        return await self.embed_batch_async(texts, is_query=True)

    async def embed_batch_document_async(self, texts: List[str]) -> List[EmbedResult]:
        return await self.embed_batch_async(texts, is_query=False)

    def close(self):
        """Release resources, subclasses can override as needed"""
        pass

    def _run_with_retry(self, func: Callable[[], T], *, logger=None, operation_name: str) -> T:
        def _wrapped() -> T:
            previous_started_at = self._active_call_started_at
            self._active_call_started_at = time.monotonic()
            try:
                return func()
            finally:
                self._active_call_started_at = previous_started_at

        return retry_sync(
            _wrapped,
            max_retries=self.max_retries,
            logger=logger,
            operation_name=operation_name,
        )

    async def _run_with_async_retry(
        self,
        func: Callable[[], Awaitable[T]],
        *,
        logger=None,
        operation_name: str,
    ) -> T:
        async def _wrapped() -> T:
            semaphore = _get_async_embed_semaphore(self.max_concurrent)
            wait_started = time.monotonic()
            await semaphore.acquire()
            wait_elapsed = time.monotonic() - wait_started
            telemetry = get_current_telemetry()
            telemetry.set("embedding.async.max_concurrent", self.max_concurrent)
            telemetry.set("embedding.async.wait_ms", round(wait_elapsed * 1000, 3))

            started = time.monotonic()
            previous_started_at = self._active_call_started_at
            self._active_call_started_at = started
            try:
                return await func()
            finally:
                elapsed = time.monotonic() - started
                telemetry.set("embedding.async.duration_ms", round(elapsed * 1000, 3))
                if logger and elapsed >= 3.0:
                    logger.warning(
                        "%s slow call provider=%s model=%s wait_ms=%.2f duration_ms=%.2f",
                        operation_name,
                        self.provider,
                        self.model_name,
                        wait_elapsed * 1000,
                        elapsed * 1000,
                    )
                self._active_call_started_at = previous_started_at
                semaphore.release()

        return await retry_async(
            _wrapped,
            max_retries=self.max_retries,
            logger=logger,
            operation_name=operation_name,
        )

    @property
    def is_dense(self) -> bool:
        """Check if result contains dense vector"""
        return True

    @property
    def is_sparse(self) -> bool:
        """Check if result contains sparse vector"""
        return False

    @property
    def is_hybrid(self) -> bool:
        """Check if result is hybrid (contains both dense and sparse vectors)"""
        return False

    def _resolve_metrics_duration_seconds(self, duration_seconds: float = 0.0) -> float:
        """Resolve per-call metrics duration from an explicit value or the active call timer."""
        try:
            normalized_duration = max(float(duration_seconds), 0.0)
        except (TypeError, ValueError):
            normalized_duration = 0.0
        if normalized_duration > 0:
            return normalized_duration
        if self._active_call_started_at is None:
            return 0.0
        return max(time.monotonic() - self._active_call_started_at, 0.0)

    def update_token_usage(
        self,
        model_name: str,
        provider: str,
        prompt_tokens: int,
        completion_tokens: int,
        duration_seconds: float = 0.0,
    ) -> None:
        """Update token usage

        Args:
            model_name: Model name
            provider: Provider name (openai, volcengine, etc.)
            prompt_tokens: Number of input tokens
            completion_tokens: Number of output tokens
            duration_seconds: Wall-clock duration of the embedding provider call in seconds
        """
        self._token_tracker.update(
            model_name=model_name,
            provider=provider,
            prompt_tokens=prompt_tokens,
            completion_tokens=completion_tokens,
        )
        try:
            from openviking.metrics.datasources import EmbeddingEventDataSource
            from openviking.observability.context import get_root_observability_context

            root_context = get_root_observability_context()

            EmbeddingEventDataSource.record_call(
                provider=str(provider),
                model_name=str(model_name),
                duration_seconds=self._resolve_metrics_duration_seconds(duration_seconds),
                prompt_tokens=int(prompt_tokens),
                completion_tokens=int(completion_tokens),
                account_id=root_context.account_id if root_context is not None else None,
            )
        except Exception as e:
            # Metrics must never break embedding execution.
            if logger.isEnabledFor(logging.DEBUG):
                logger.debug(
                    "embedding.update_token_usage metrics emit failed provider=%s model_name=%s err=%s: %s",
                    provider,
                    model_name,
                    type(e).__name__,
                    e,
                )

    def get_token_usage(self) -> Dict[str, Any]:
        """Get token usage

        Returns:
            Dict[str, Any]: Token usage dictionary
        """
        return self._token_tracker.to_dict()

    def reset_token_usage(self) -> None:
        """Reset token usage"""
        self._token_tracker.reset()

    def _estimate_tokens(self, text: str) -> int:
        """Estimate token count from text (1 token ≈ 4 characters for English)

        Args:
            text: Input text to estimate tokens for

        Returns:
            Estimated token count
        """
        if not text:
            return 0
        # Approximate: 1 token ≈ 4 characters
        # For Chinese characters, 1 token ≈ 1-2 characters
        chinese_chars = sum(1 for c in text if "\u4e00" <= c <= "\u9fff")
        other_chars = len(text) - chinese_chars
        return max(1, (chinese_chars // 1) + (other_chars // 4))


class DenseEmbedderBase(EmbedderBase):
    """Dense embedder base class that returns dense vectors

    Subclasses must implement:
    - embed(): Return EmbedResult containing only dense_vector
    - get_dimension(): Return vector dimension
    """

    @abstractmethod
    def embed(self, text: str, is_query: bool = False) -> EmbedResult:
        """Perform dense embedding on text

        Args:
            text: Input text
            is_query: Flag to indicate if this is a query embedding

        Returns:
            EmbedResult: Result containing only dense_vector
        """
        pass

    @abstractmethod
    def get_dimension(self) -> int:
        """Get embedding dimension

        Returns:
            int: Vector dimension
        """
        pass


class SparseEmbedderBase(EmbedderBase):
    """Sparse embedder base class that returns sparse vectors

    Sparse vector format is Dict[str, float], mapping terms to weights.
    Example: {'information': 0.8, 'retrieval': 0.6, 'system': 0.4}

    Subclasses must implement:
    - embed(): Return EmbedResult containing only sparse_vector
    """

    @abstractmethod
    def embed(self, text: str, is_query: bool = False) -> EmbedResult:
        """Perform sparse embedding on text

        Args:
            text: Input text
            is_query: Flag to indicate if this is a query embedding

        Returns:
            EmbedResult: Result containing only sparse_vector
        """
        pass

    @property
    def is_sparse(self) -> bool:
        """Check if result contains sparse vector"""
        return True


class HybridEmbedderBase(EmbedderBase):
    """Hybrid embedder base class that returns both dense and sparse vectors

    Used for hybrid search, combining advantages of both dense and sparse vectors.

    Subclasses must implement:
    - embed(): Return EmbedResult containing both dense_vector and sparse_vector
    - get_dimension(): Return dense vector dimension
    """

    @abstractmethod
    def embed(self, text: str, is_query: bool = False) -> EmbedResult:
        """Perform hybrid embedding on text

        Args:
            text: Input text
            is_query: Flag to indicate if this is a query embedding

        Returns:
            EmbedResult: Result containing both dense_vector and sparse_vector
        """
        pass

    @abstractmethod
    def get_dimension(self) -> int:
        """Get dense embedding dimension

        Returns:
            int: Dense vector dimension
        """
        pass

    @property
    def is_sparse(self) -> bool:
        """Check if result contains sparse vector"""
        return True

    @property
    def is_hybrid(self) -> bool:
        """Check if result is hybrid (contains both dense and sparse vectors)"""
        return True


class CompositeHybridEmbedder(HybridEmbedderBase):
    """Composite Hybrid Embedder that combines a dense embedder and a sparse embedder

    Example:
        >>> dense = OpenAIDenseEmbedder(...)
        >>> sparse = VolcengineSparseEmbedder(...)
        >>> embedder = CompositeHybridEmbedder(dense, sparse)
        >>> result = embedder.embed("test")
    """

    def __init__(self, dense_embedder: DenseEmbedderBase, sparse_embedder: SparseEmbedderBase):
        """Initialize with two separate embedders"""
        super().__init__(model_name=f"{dense_embedder.model_name}+{sparse_embedder.model_name}")
        self.dense_embedder = dense_embedder
        self.sparse_embedder = sparse_embedder

    def embed(self, text: str, is_query: bool = False) -> EmbedResult:
        """Combine results from both embedders"""
        dense_res = self.dense_embedder.embed(text, is_query=is_query)
        sparse_res = self.sparse_embedder.embed(text, is_query=is_query)

        return EmbedResult(
            dense_vector=dense_res.dense_vector, sparse_vector=sparse_res.sparse_vector
        )

    def embed_batch(self, texts: List[str], is_query: bool = False) -> List[EmbedResult]:
        """Combine batch results"""
        dense_results = self.dense_embedder.embed_batch(texts, is_query=is_query)
        sparse_results = self.sparse_embedder.embed_batch(texts, is_query=is_query)

        return [
            EmbedResult(dense_vector=d.dense_vector, sparse_vector=s.sparse_vector)
            for d, s in zip(dense_results, sparse_results, strict=True)
        ]

    async def embed_async(self, text: str, is_query: bool = False) -> EmbedResult:
        dense_res, sparse_res = await asyncio.gather(
            self.dense_embedder.embed_async(text, is_query=is_query),
            self.sparse_embedder.embed_async(text, is_query=is_query),
        )
        return EmbedResult(
            dense_vector=dense_res.dense_vector, sparse_vector=sparse_res.sparse_vector
        )

    async def embed_batch_async(
        self, texts: List[str], is_query: bool = False
    ) -> List[EmbedResult]:
        dense_results, sparse_results = await asyncio.gather(
            self.dense_embedder.embed_batch_async(texts, is_query=is_query),
            self.sparse_embedder.embed_batch_async(texts, is_query=is_query),
        )
        return [
            EmbedResult(dense_vector=d.dense_vector, sparse_vector=s.sparse_vector)
            for d, s in zip(dense_results, sparse_results, strict=True)
        ]

    def get_dimension(self) -> int:
        return self.dense_embedder.get_dimension()

    def close(self):
        self.dense_embedder.close()
        self.sparse_embedder.close()


def exponential_backoff_retry(
    func: Callable[[], T],
    max_wait: float = 10.0,
    base_delay: float = 0.5,
    max_delay: float = 2.0,
    jitter: bool = True,
    is_retryable: Optional[Callable[[Exception], bool]] = None,
    logger=None,
) -> T:
    """
    指数退避重试函数

    Args:
        func: 要执行的函数
        max_wait: 最大总等待时间（秒）
        base_delay: 基础延迟时间（秒）
        max_delay: 单次最大延迟时间（秒）
        jitter: 是否添加随机抖动
        is_retryable: 判断异常是否可重试的函数
        logger: 日志记录器

    Returns:
        函数执行结果

    Raises:
        最后一次尝试的异常
    """
    start_time = time.time()
    attempt = 0

    while True:
        try:
            return func()
        except Exception as e:
            attempt += 1
            elapsed = time.time() - start_time

            if elapsed >= max_wait:
                if logger:
                    logger.error(
                        f"Exceeded max wait time ({max_wait}s) after {attempt} attempts, giving up"
                    )
                raise

            if is_retryable and not is_retryable(e):
                if logger:
                    logger.error(f"Non-retryable error after {attempt} attempts: {e}")
                raise

            delay = min(base_delay * (2 ** (attempt - 1)), max_delay)

            if jitter:
                delay = delay * (0.5 + random.random())

            remaining_time = max_wait - elapsed
            delay = min(delay, remaining_time)

            if logger:
                logger.info(
                    f"Retry attempt {attempt}, waiting {delay:.2f}s before next try (elapsed: {elapsed:.2f}s)"
                )

            time.sleep(delay)
