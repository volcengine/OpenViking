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

DEFAULT_JINA_QUERY_TASK = "retrieval.query"
DEFAULT_JINA_DOCUMENT_TASK = "retrieval.passage"
DEFAULT_JINA_CODE_QUERY_TASK = "nl2code.query"
DEFAULT_JINA_CODE_DOCUMENT_TASK = "nl2code.passage"
_UNSET = object()


def _get_default_task_params(model_name: str) -> tuple[str, str]:
    """Return the default Jina task names for the selected model."""
    if model_name.startswith("jina-code-embeddings-"):
        return DEFAULT_JINA_CODE_QUERY_TASK, DEFAULT_JINA_CODE_DOCUMENT_TASK
    return DEFAULT_JINA_QUERY_TASK, DEFAULT_JINA_DOCUMENT_TASK


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
        query_param: Any = _UNSET,
        document_param: Any = _UNSET,
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
        default_query_param, default_document_param = _get_default_task_params(model_name)
        if query_param is _UNSET:
            query_param = default_query_param
        if document_param is _UNSET:
            document_param = default_document_param
        self.query_param = query_param
        self.document_param = document_param
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

    def _build_extra_body(self, is_query: bool = False) -> Optional[Dict[str, Any]]:
        """Build extra_body dict for Jina-specific parameters"""
        extra_body = {}
        task = None
        if is_query and self.query_param is not None:
            task = self.query_param
        elif not is_query and self.document_param is not None:
            task = self.document_param

        if task is not None:
            extra_body["task"] = task
        if self.late_chunking is not None:
            extra_body["late_chunking"] = self.late_chunking
        return extra_body if extra_body else None

    def embed(self, text: str, is_query: bool = False) -> EmbedResult:
        """Perform dense embedding on text

        Args:
            text: Input text
            is_query: Flag to indicate if this is a query embedding

        Returns:
            EmbedResult: Result containing only dense_vector

        Raises:
            RuntimeError: When API call fails
        """
        try:
            kwargs: Dict[str, Any] = {"input": text, "model": self.model_name}
            if self.dimension:
                kwargs["dimensions"] = self.dimension

            extra_body = self._build_extra_body(is_query=is_query)
            if extra_body:
                kwargs["extra_body"] = extra_body

            response = self.client.embeddings.create(**kwargs)
            vector = response.data[0].embedding

            return EmbedResult(dense_vector=vector)
        except openai.APIError as e:
            raise RuntimeError(f"Jina API error: {e.message}") from e
        except Exception as e:
            raise RuntimeError(f"Embedding failed: {str(e)}") from e

    def embed_batch(self, texts: List[str], is_query: bool = False) -> List[EmbedResult]:
        """Batch embedding (Jina native support)

        Args:
            texts: List of texts
            is_query: Flag to indicate if these are query embeddings

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

            extra_body = self._build_extra_body(is_query=is_query)
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
