# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: Apache-2.0
"""Google/Gemini AI Embedder Implementation"""

from typing import Any, Dict, List, Optional

import openai

from openviking.models.embedder.base import (
    DenseEmbedderBase,
    EmbedResult,
)

# Default dimensions for Google/Gemini embedding models
GOOGLE_MODEL_DIMENSIONS = {
    "gemini-embedding-2-preview": 3072,  # Gemini Embedding 2 with MRL support
    "gemini-embedding-001": 768,  # Legacy text-only model
}


class GoogleDenseEmbedder(DenseEmbedderBase):
    """Google/Gemini AI Dense Embedder Implementation

    Uses Google/Gemini embedding API via OpenAI-compatible client.
    Supports task-specific embeddings and Matryoshka dimension reduction.

    Example:
        >>> embedder = GoogleDenseEmbedder(
        ...     model_name="gemini-embedding-2-preview",
        ...     api_key="your-gemini-api-key",
        ...     dimension=1024,
        ...     task_type="RETRIEVAL_QUERY"
        ... )
        >>> result = embedder.embed("Hello world")
        >>> print(len(result.dense_vector))
        1024
    """

    def __init__(
        self,
        model_name: str = "gemini-embedding-2-preview",
        api_key: Optional[str] = None,
        api_base: Optional[str] = None,
        dimension: Optional[int] = None,
        task_type: Optional[str] = None,
        config: Optional[Dict[str, Any]] = None,
    ):
        """Initialize Google/Gemini AI Dense Embedder

        Args:
            model_name: Google/Gemini model name, defaults to gemini-embedding-2-preview
            api_key: API key, required
            api_base: API base URL, defaults to https://generativelanguage.googleapis.com/v1beta/openai/
            dimension: Dimension for Matryoshka reduction, optional
            task_type: Task type for task-specific embeddings, optional.
                      Valid values: RETRIEVAL_QUERY, RETRIEVAL_DOCUMENT,
                      SEMANTIC_SIMILARITY, CLASSIFICATION, CLUSTERING
            config: Additional configuration dict

        Raises:
            ValueError: If api_key is not provided
        """
        super().__init__(model_name, config)

        self.api_key = api_key
        self.api_base = api_base or "https://generativelanguage.googleapis.com/v1beta/openai/"
        self.dimension = dimension
        self.task_type = task_type

        if not self.api_key:
            raise ValueError("api_key is required")

        # Initialize OpenAI-compatible client with Google base URL
        self.client = openai.OpenAI(
            api_key=self.api_key,
            base_url=self.api_base,
        )

        # Determine dimension
        max_dim = GOOGLE_MODEL_DIMENSIONS.get(model_name, 3072)
        if dimension is not None and dimension > max_dim:
            raise ValueError(
                f"Requested dimension {dimension} exceeds maximum {max_dim} for model '{model_name}'. "
                f"Google/Gemini models support Matryoshka dimension reduction up to {max_dim}."
            )
        self._dimension = dimension if dimension is not None else max_dim

    def _build_extra_body(self) -> Optional[Dict[str, Any]]:
        """Build extra_body dict for Google-specific parameters"""
        extra_body = {}
        if self.task_type is not None:
            extra_body["task_type"] = self.task_type
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
            raise RuntimeError(f"Google/Gemini API error: {e.message}") from e
        except Exception as e:
            raise RuntimeError(f"Embedding failed: {str(e)}") from e

    def embed_batch(self, texts: List[str]) -> List[EmbedResult]:
        """Batch embedding (Google/Gemini native support)

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
            raise RuntimeError(f"Google/Gemini API error: {e.message}") from e
        except Exception as e:
            raise RuntimeError(f"Batch embedding failed: {str(e)}") from e

    def get_dimension(self) -> int:
        """Get embedding dimension

        Returns:
            int: Vector dimension
        """
        return self._dimension
