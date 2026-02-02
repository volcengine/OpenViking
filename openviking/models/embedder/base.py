# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: Apache-2.0
from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import Any, Dict, List, Optional


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
