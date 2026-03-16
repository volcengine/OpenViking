# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: Apache-2.0
import logging
import math
import random
import re
import time
from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import Any, Callable, Dict, List, Optional, TypeVar

logger = logging.getLogger(__name__)

T = TypeVar("T")


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

    def __init__(
        self,
        model_name: str,
        config: Optional[Dict[str, Any]] = None,
        max_tokens: Optional[int] = None,
    ):
        """Initialize embedder

        Args:
            model_name: Model name
            config: Configuration dict containing api_key, api_base, etc.
            max_tokens: Maximum token count per embedding request, None to use default (8000)
        """
        self.model_name = model_name
        self.config = config or {}
        self._max_tokens = max_tokens

    @property
    def max_tokens(self) -> int:
        """Maximum token count per embedding request. Subclasses can override."""
        if self._max_tokens is not None:
            return self._max_tokens
        return 8000

    def _estimate_tokens(self, text: str) -> int:
        """Estimate token count for the given text.

        Tries tiktoken (for OpenAI models) first, falls back to a character-based
        heuristic that accounts for multi-byte (CJK) characters.

        Args:
            text: Input text

        Returns:
            Estimated token count
        """
        try:
            import tiktoken

            enc = tiktoken.encoding_for_model(self.model_name)
            return len(enc.encode(text))
        except Exception:
            logger.info(
                "tiktoken unavailable for model '%s', using character-based estimation",
                self.model_name,
            )
            # Use the higher of char-based and byte-based estimates so that
            # CJK text (3 bytes per char in UTF-8) is not underestimated.
            return max(len(text) // 3, len(text.encode("utf-8")) // 4)

    def _chunk_text(self, text: str) -> List[str]:
        """Split text into chunks, each within max_tokens.

        Splitting priority: paragraphs (\\n\\n) > sentences (。.!?\\n) > fixed length.

        Args:
            text: Input text

        Returns:
            List of text chunks
        """
        max_tok = self.max_tokens
        if self._estimate_tokens(text) <= max_tok:
            return [text]

        # Try paragraph split first
        paragraphs = text.split("\n\n")
        if len(paragraphs) > 1:
            chunks = self._merge_segments(paragraphs, max_tok, "\n\n")
            if all(self._estimate_tokens(c) <= max_tok for c in chunks):
                return chunks

        # Try sentence split
        sentences = re.split(r"(?<=[。.!?\n])", text)
        sentences = [s for s in sentences if s]
        if len(sentences) > 1:
            chunks = self._merge_segments(sentences, max_tok, "")
            if all(self._estimate_tokens(c) <= max_tok for c in chunks):
                return chunks

        # Fixed-length split as last resort
        return self._fixed_length_split(text, max_tok)

    def _merge_segments(self, segments: List[str], max_tok: int, separator: str) -> List[str]:
        """Merge small segments into chunks that fit within max_tokens."""
        chunks: List[str] = []
        current = ""
        for seg in segments:
            candidate = (current + separator + seg) if current else seg
            if self._estimate_tokens(candidate) <= max_tok:
                current = candidate
            else:
                if current:
                    chunks.append(current)
                # If a single segment exceeds max_tok, it will be handled later
                current = seg
        if current:
            chunks.append(current)
        return chunks

    def _fixed_length_split(self, text: str, max_tok: int) -> List[str]:
        """Split text into fixed-length chunks based on estimated char-per-token ratio."""
        # Estimate chars per token
        total_tokens = self._estimate_tokens(text)
        chars_per_token = len(text) / max(total_tokens, 1)
        chunk_size = max(int(max_tok * chars_per_token * 0.9), 100)

        chunks: List[str] = []
        start = 0
        while start < len(text):
            end = start + chunk_size
            if end < len(text):
                # Try to break at a whitespace boundary
                boundary = text.rfind(" ", start, end)
                if boundary > start:
                    end = boundary + 1
            chunks.append(text[start:end])
            start = end
        return chunks

    def _chunk_and_embed(self, text: str) -> EmbedResult:
        """Chunk text if it exceeds max_tokens, embed each chunk, and merge results.

        For text within limits, delegates to _embed_single directly.
        For oversized text, splits into chunks, embeds each, then computes a
        token-weighted average of dense vectors with L2 normalization.

        Args:
            text: Input text

        Returns:
            EmbedResult with merged embedding
        """
        estimated = self._estimate_tokens(text)
        if estimated <= self.max_tokens:
            return self._embed_single(text)

        chunks = self._chunk_text(text)
        logger.debug(
            "Chunking text: original ~%d tokens -> %d chunks",
            estimated,
            len(chunks),
        )

        results: List[EmbedResult] = []
        weights: List[int] = []
        for chunk in chunks:
            result = self._embed_single(chunk)
            results.append(result)
            weights.append(self._estimate_tokens(chunk))

        # Merge dense vectors via weighted average + L2 normalization
        merged_dense = None
        if results and results[0].dense_vector is not None:
            dim = len(results[0].dense_vector)
            total_weight = sum(weights)
            merged = [0.0] * dim
            for result, w in zip(results, weights):
                if result.dense_vector:
                    for i in range(dim):
                        merged[i] += result.dense_vector[i] * w
            if total_weight > 0:
                merged = [v / total_weight for v in merged]
            # L2 normalization
            norm = math.sqrt(sum(v * v for v in merged))
            if norm > 0:
                merged = [v / norm for v in merged]
            merged_dense = merged

        return EmbedResult(dense_vector=merged_dense)

    def _embed_single(self, text: str) -> EmbedResult:
        """Embed a single text without chunking logic. Defaults to self.embed().

        Subclasses that override embed() with chunking should also override
        this method to provide the raw embedding call.
        """
        return self.embed(text)

    @abstractmethod
    def embed(self, text: str) -> EmbedResult:
        """Embed single text

        Args:
            text: Input text

        Returns:
            EmbedResult: Embedding result containing dense_vector, sparse_vector, or both
        """
        pass

    def embed_batch(self, texts: List[str]) -> List[EmbedResult]:
        """Batch embedding (default implementation loops, subclasses can override for optimization)

        Args:
            texts: List of texts

        Returns:
            List[EmbedResult]: List of embedding results
        """
        return [self.embed(text) for text in texts]

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
    def embed(self, text: str) -> EmbedResult:
        """Perform dense embedding on text

        Args:
            text: Input text

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

    @property
    def supports_multimodal(self) -> bool:
        """True when this embedder natively embeds image/audio bytes."""
        return False

    def embed_multimodal(self, vectorize: "Vectorize") -> "EmbedResult":
        """Default implementation falls back to text — no breaking change."""
        return self.embed(getattr(vectorize, "text", str(vectorize)))


class SparseEmbedderBase(EmbedderBase):
    """Sparse embedder base class that returns sparse vectors

    Sparse vector format is Dict[str, float], mapping terms to weights.
    Example: {'information': 0.8, 'retrieval': 0.6, 'system': 0.4}

    Subclasses must implement:
    - embed(): Return EmbedResult containing only sparse_vector
    """

    @abstractmethod
    def embed(self, text: str) -> EmbedResult:
        """Perform sparse embedding on text

        Args:
            text: Input text

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
    def embed(self, text: str) -> EmbedResult:
        """Perform hybrid embedding on text

        Args:
            text: Input text

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

    def embed(self, text: str) -> EmbedResult:
        """Combine results from both embedders"""
        dense_res = self.dense_embedder.embed(text)
        sparse_res = self.sparse_embedder.embed(text)

        return EmbedResult(
            dense_vector=dense_res.dense_vector, sparse_vector=sparse_res.sparse_vector
        )

    def embed_batch(self, texts: List[str]) -> List[EmbedResult]:
        """Combine batch results"""
        dense_results = self.dense_embedder.embed_batch(texts)
        sparse_results = self.sparse_embedder.embed_batch(texts)

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
