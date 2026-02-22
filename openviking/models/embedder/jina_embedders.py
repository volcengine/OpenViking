# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: Apache-2.0
"""Jina AI Embedder Implementation"""

from typing import Any, Dict, List, Optional

import openai

from openviking.models.embedder.base import (
    DenseEmbedderBase,
    EmbedResult,
)

# Default dimensions for Jina embedding models
JINA_MODEL_DIMENSIONS = {
    "jina-embeddings-v5-text-small": 1024,  # 677M params, max seq 32768
    "jina-embeddings-v5-text-nano": 768,  # 239M params, max seq 8192
}


class JinaDenseEmbedder(DenseEmbedderBase):
    """Jina AI Dense Embedder Implementation

    Uses Jina AI embedding API via OpenAI-compatible client.
    Supports task-specific embeddings and Matryoshka dimension reduction.

    Example:
        >>> embedder = JinaDenseEmbedder(
        ...     model_name="jina-embeddings-v5-text-small",
        ...     api_key="jina_xxx",
        ...     dimension=512,
        ...     task="retrieval.query"
        ... )
        >>> result = embedder.embed("Hello world")
        >>> print(len(result.dense_vector))
        512
    """

    def __init__(
        self,
        model_name: str = "jina-embeddings-v5-text-small",
        api_key: Optional[str] = None,
        api_base: Optional[str] = None,
        dimension: Optional[int] = None,
        task: Optional[str] = None,
        late_chunking: Optional[bool] = None,
        config: Optional[Dict[str, Any]] = None,
    ):
        """Initialize Jina AI Dense Embedder

        Args:
            model_name: Jina model name, defaults to jina-embeddings-v5-text-small
            api_key: API key, required
            api_base: API base URL, defaults to https://api.jina.ai/v1
            dimension: Dimension for Matryoshka reduction, optional
            task: Task type for task-specific embeddings, optional.
                  Valid values: retrieval.query, retrieval.passage,
                  text-matching, classification, separation
            late_chunking: Enable late chunking via extra_body, optional
            config: Additional configuration dict

        Raises:
            ValueError: If api_key is not provided
        """
        super().__init__(model_name, config)

        self.api_key = api_key
        self.api_base = api_base or "https://api.jina.ai/v1"
        self.dimension = dimension
        self.task = task
        self.late_chunking = late_chunking

        if not self.api_key:
            raise ValueError("api_key is required")

        # Initialize OpenAI-compatible client with Jina base URL
        self.client = openai.OpenAI(
            api_key=self.api_key,
            base_url=self.api_base,
        )

        # Determine dimension
        max_dim = JINA_MODEL_DIMENSIONS.get(model_name, 1024)
        if dimension is not None and dimension > max_dim:
            raise ValueError(
                f"Requested dimension {dimension} exceeds maximum {max_dim} for model '{model_name}'. "
                f"Jina models support Matryoshka dimension reduction up to {max_dim}."
            )
        self._dimension = dimension if dimension is not None else max_dim

    def _build_extra_body(self) -> Optional[Dict[str, Any]]:
        """Build extra_body dict for Jina-specific parameters"""
        extra_body = {}
        if self.task is not None:
            extra_body["task"] = self.task
        if self.late_chunking is not None:
            extra_body["late_chunking"] = self.late_chunking
        return extra_body if extra_body else None

    def embed(self, text: str) -> EmbedResult:
        """Perform dense embedding on text

        Args:
            text: Input text

        Returns:
            EmbedResult: Result containing only dense_vector

        Raises:
            RuntimeError: When API call fails
        """
        try:
            kwargs: Dict[str, Any] = {"input": text, "model": self.model_name}
            if self.dimension:
                kwargs["dimensions"] = self.dimension

            extra_body = self._build_extra_body()
            if extra_body:
                kwargs["extra_body"] = extra_body

            response = self.client.embeddings.create(**kwargs)
            vector = response.data[0].embedding

            return EmbedResult(dense_vector=vector)
        except openai.APIError as e:
            raise RuntimeError(f"Jina API error: {e.message}") from e
        except Exception as e:
            raise RuntimeError(f"Embedding failed: {str(e)}") from e

    def embed_batch(self, texts: List[str]) -> List[EmbedResult]:
        """Batch embedding (Jina native support)

        Args:
            texts: List of texts

        Returns:
            List[EmbedResult]: List of embedding results

        Raises:
            RuntimeError: When API call fails
        """
        if not texts:
            return []

        try:
            kwargs: Dict[str, Any] = {"input": texts, "model": self.model_name}
            if self.dimension:
                kwargs["dimensions"] = self.dimension

            extra_body = self._build_extra_body()
            if extra_body:
                kwargs["extra_body"] = extra_body

            response = self.client.embeddings.create(**kwargs)

            return [EmbedResult(dense_vector=item.embedding) for item in response.data]
        except openai.APIError as e:
            raise RuntimeError(f"Jina API error: {e.message}") from e
        except Exception as e:
            raise RuntimeError(f"Batch embedding failed: {str(e)}") from e

    def get_dimension(self) -> int:
        """Get embedding dimension

        Returns:
            int: Vector dimension
        """
        return self._dimension

