# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: Apache-2.0
import logging
import random
import re
import time
from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import Any, Callable, Dict, List, Optional, TypeVar

T = TypeVar("T")


_tiktoken_encoder = None


def _get_tiktoken_encoder():
    """Get cached tiktoken encoder (module-level singleton, downloaded once)."""
    global _tiktoken_encoder
    if _tiktoken_encoder is None:
        try:
            import tiktoken

            _tiktoken_encoder = tiktoken.get_encoding("cl100k_base")
        except Exception:
            pass
    return _tiktoken_encoder


def truncate_text_by_tokens(text: str, max_tokens: int) -> str:
    """Truncate text to at most max_tokens tokens. Returns original text if within limit.

    Guarantees: len(tiktoken.encode(result)) <= max_tokens for cl100k_base.
    Falls back to UTF-8 byte truncation if tiktoken is unavailable.

    Args:
        text: Input text to truncate
        max_tokens: Maximum number of tokens allowed

    Returns:
        Truncated text (or original text if already within limit)
    """
    # Fast path: tiktoken never produces more tokens than UTF-8 bytes,
    # so byte count <= max_tokens guarantees token count <= max_tokens.
    if len(text.encode("utf-8")) <= max_tokens:
        return text

    enc = _get_tiktoken_encoder()
    if enc is not None:
        try:
            # disallowed_special=() avoids ValueError when text contains special token strings
            tokens = enc.encode(text, disallowed_special=())
            if len(tokens) <= max_tokens:
                return text
            return enc.decode(tokens[:max_tokens])
        except Exception:
            pass  # Fall through to byte-based truncation

    # Fallback: UTF-8 byte truncation (tiktoken unavailable).
    # Guaranteed safe: token_count <= utf8_bytes, truncating to max_tokens bytes
    # ensures token_count <= max_tokens.
    encoded = text.encode("utf-8")
    truncated = encoded[:max_tokens].decode("utf-8", errors="ignore")
    return truncated



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

    @property
    def max_input_tokens(self) -> int:
        """Maximum number of tokens allowed as input. Subclasses can override."""
        return 8000

    def _truncate_input(self, text: str) -> str:
        """Truncate input text to max_input_tokens. Logs a warning if truncation occurs."""
        truncated = truncate_text_by_tokens(text, self.max_input_tokens)
        if truncated != text:
            logging.getLogger(__name__).warning(
                f"[{self.__class__.__name__}] Input truncated to {self.max_input_tokens} tokens"
            )
        return truncated

    def close(self):
        """Release resources, subclasses can override as needed"""
        pass

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
            for d, s in zip(dense_results, sparse_results)
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
