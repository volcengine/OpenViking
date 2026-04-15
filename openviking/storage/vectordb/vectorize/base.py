# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0
"""
Base interface definition for vectorization module
"""

from abc import ABC, abstractmethod
from typing import Any, Dict, List, Optional


class VectorizeResult:
    """Vectorization result"""

    def __init__(
        self,
        dense_vectors: Optional[List[List[float]]] = None,
        sparse_vectors: Optional[List[Dict[str, float]]] = None,
        request_id: str = "",
        token_usage: Optional[Dict[str, Any]] = None,
    ):
        self.dense_vectors = dense_vectors or []
        self.sparse_vectors = sparse_vectors or []
        self.request_id = request_id
        self.token_usage = token_usage

    def __repr__(self):
        return (
            f"VectorizeResult(dense={len(self.dense_vectors)}, sparse={len(self.sparse_vectors)}, "
            f"request_id='{self.request_id}')"
        )


class BaseVectorizer(ABC):
    """Base vectorizer class"""

    def __init__(self, config: Dict[str, Any]):
        """
        Initialize vectorizer

        Args:
            config: Configuration dictionary containing model-related settings
        """
        self.config = config
        self.model_name = config.get("ModelName", "")
        pass

    @abstractmethod
    def vectorize_query(self, texts: List[str]) -> VectorizeResult:
        """
        Vectorize query texts

        Args:
            texts: List of texts to vectorize

        Returns:
            VectorizeResult: Vectorization results
        """
        pass

    @abstractmethod
    def vectorize_document(
        self,
        data: List[Any],
        dense_model: Dict[str, Any],
        sparse_model: Optional[Dict[str, Any]] = None,
    ) -> VectorizeResult:
        """
        Vectorize documents

        Args:
            data: List of data items to vectorize
            dense_model: Configuration for dense model
            sparse_model: Configuration for sparse model (optional)

        Returns:
            VectorizeResult: Vectorization results
        """
        pass

    def close(self):
        """Close resources"""
        pass
