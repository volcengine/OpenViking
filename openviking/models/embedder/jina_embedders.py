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
    Supports task-specific embeddings (non-symmetric) and Matryoshka dimension reduction.

    Jina models are non-symmetric by default and require the 'task' parameter to distinguish
    between query and document embeddings. This is different from official OpenAI models,
    which are symmetric and do not support the input_type parameter.

    Example:
        >>> # Query embedding
        >>> query_embedder = JinaDenseEmbedder(
        ...     model_name="jina-embeddings-v5-text-small",
        ...     api_key="jina_xxx",
        ...     dimension=512,
        ...     context="query"
        ... )
        >>> query_vector = query_embedder.embed("search query")
        >>> print(len(query_vector.dense_vector))
        512

        >>> # Document embedding
        >>> doc_embedder = JinaDenseEmbedder(
        ...     model_name="jina-embeddings-v5-text-small",
        ...     api_key="jina_xxx",
        ...     dimension=512,
        ...     context="document"
        ... )
        >>> doc_vector = doc_embedder.embed("document content")
    """

    def __init__(
        self,
        model_name: str = "jina-embeddings-v5-text-small",
        api_key: Optional[str] = None,
        api_base: Optional[str] = None,
        dimension: Optional[int] = None,
        context: Optional[str] = None,
        query_param: str = "retrieval.query",
        document_param: str = "retrieval.passage",
        late_chunking: Optional[bool] = None,
        config: Optional[Dict[str, Any]] = None,
        task: Optional[str] = None,
    ):
        """Initialize Jina AI Dense Embedder

        Args:
            model_name: Jina model name, defaults to jina-embeddings-v5-text-small
            api_key: API key, required
            api_base: API base URL, defaults to https://api.jina.ai/v1
            dimension: Dimension for Matryoshka reduction, optional
            context: Embedding context, either 'query' or 'document'. Jina models are
                     non-symmetric by default; task is always sent unless context is None.
                     Pass None to disable task (e.g. for symmetric deployments via OpenAI
                     compatible endpoint).
            query_param: Task value for query-side embeddings. Defaults to 'retrieval.query'.
                        Override for models with different task naming conventions.
            document_param: Task value for document-side embeddings. Defaults to
                           'retrieval.passage'. Override for models with different task
                           naming conventions.
            late_chunking: Enable late chunking via extra_body, optional
            config: Additional configuration dict

        Raises:
            ValueError: If api_key is not provided
        """
        super().__init__(model_name, config)

        self.api_key = api_key
        self.api_base = api_base or "https://api.jina.ai/v1"
        self.dimension = dimension
        # Direct task overrides context-based logic
        if task is not None:
            self.task: Optional[str] = task
        elif context == "query":
            self.task = query_param
        elif context == "document":
            self.task = document_param
        else:
            self.task = None
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
