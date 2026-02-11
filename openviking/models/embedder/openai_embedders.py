# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: Apache-2.0
"""OpenAI Embedder Implementation"""

from typing import Any, Dict, List, Optional

import openai

from openviking.models.embedder.base import (
    DenseEmbedderBase,
    EmbedResult,
    HybridEmbedderBase,
    SparseEmbedderBase,
)


class OpenAIDenseEmbedder(DenseEmbedderBase):
    """OpenAI Dense Embedder Implementation

    Supports OpenAI embedding models such as text-embedding-3-small, text-embedding-3-large, etc.

    Example:
        >>> embedder = OpenAIDenseEmbedder(
        ...     model_name="text-embedding-3-small",
        ...     api_key="sk-xxx",
        ...     dimension=1536
        ... )
        >>> result = embedder.embed("Hello world")
        >>> print(len(result.dense_vector))
        1536
    """

    def __init__(
        self,
        model_name: str = "text-embedding-3-small",
        api_key: Optional[str] = None,
        api_base: Optional[str] = None,
        dimension: Optional[int] = None,
        config: Optional[Dict[str, Any]] = None,
    ):
        """Initialize OpenAI Dense Embedder

        Args:
            model_name: OpenAI model name, defaults to text-embedding-3-small
            api_key: API key, if None will read from env vars (OPENVIKING_EMBEDDING_API_KEY or OPENAI_API_KEY)
            api_base: API base URL, optional
            dimension: Dimension (if model supports), optional
            config: Additional configuration dict

        Raises:
            ValueError: If api_key is not provided and env vars are not set
        """
        super().__init__(model_name, config)

        self.api_key = api_key
        self.api_base = api_base
        self.dimension = dimension

        if not self.api_key:
            raise ValueError("api_key is required")

        # Initialize OpenAI client
        client_kwargs = {"api_key": self.api_key}
        if self.api_base:
            client_kwargs["base_url"] = self.api_base
        self.client = openai.OpenAI(**client_kwargs)

        # Auto-detect dimension
        self._dimension = dimension
        if self._dimension is None:
            self._dimension = self._detect_dimension()

    def _detect_dimension(self) -> int:
        """Detect dimension by making an actual API call"""
        try:
            result = self.embed("test")
            return len(result.dense_vector) if result.dense_vector else 1536
        except Exception:
            # Use default value, text-embedding-3-small defaults to 1536
            return 1536

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
            kwargs = {"input": text, "model": self.model_name}
            if self.dimension:
                kwargs["dimensions"] = self.dimension

            response = self.client.embeddings.create(**kwargs)
            vector = response.data[0].embedding

            return EmbedResult(dense_vector=vector)
        except openai.APIError as e:
            raise RuntimeError(f"OpenAI API error: {e.message}") from e
        except Exception as e:
            raise RuntimeError(f"Embedding failed: {str(e)}") from e

    def embed_batch(self, texts: List[str]) -> List[EmbedResult]:
        """Batch embedding (OpenAI native support)

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
            kwargs = {"input": texts, "model": self.model_name}
            if self.dimension:
                kwargs["dimensions"] = self.dimension

            response = self.client.embeddings.create(**kwargs)

            return [EmbedResult(dense_vector=item.embedding) for item in response.data]
        except openai.APIError as e:
            raise RuntimeError(f"OpenAI API error: {e.message}") from e
        except Exception as e:
            raise RuntimeError(f"Batch embedding failed: {str(e)}") from e

    def get_dimension(self) -> int:
        """Get embedding dimension

        Returns:
            int: Vector dimension
        """
        return self._dimension


class OpenAISparseEmbedder(SparseEmbedderBase):
    """OpenAI does not support sparse embedding

    This class is a placeholder for error messaging. For sparse embedding, use Volcengine or other providers.
    """

    def __init__(self, *args, **kwargs):
        raise NotImplementedError(
            "OpenAI does not support sparse embeddings. "
            "Consider using VolcengineSparseEmbedder or other providers."
        )

    def embed(self, text: str) -> EmbedResult:
        raise NotImplementedError()


class OpenAIHybridEmbedder(HybridEmbedderBase):
    """OpenAI does not support hybrid embedding

    This class is a placeholder for error messaging. For hybrid embedding, use Volcengine or other providers.
    """

    def __init__(self, *args, **kwargs):
        raise NotImplementedError(
            "OpenAI does not support hybrid embeddings. "
            "Consider using VolcengineHybridEmbedder or other providers."
        )

    def embed(self, text: str) -> EmbedResult:
        raise NotImplementedError()

    def get_dimension(self) -> int:
        raise NotImplementedError()
