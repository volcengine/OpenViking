# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0
"""Voyage AI dense embedder implementation."""

import logging
from typing import Any, Dict, List, Optional

import openai

from openviking.models.embedder.base import DenseEmbedderBase, EmbedResult

logger = logging.getLogger(__name__)

VOYAGE_MODEL_DIMENSIONS = {
    "voyage-3": 1024,
    "voyage-3-large": 1024,
    "voyage-3.5": 1024,
    "voyage-3.5-lite": 1024,
    "voyage-4": 1024,
    "voyage-4-lite": 1024,
    "voyage-4-large": 1024,
    "voyage-code-3": 1024,
    "voyage-context-3": 1024,
    "voyage-finance-2": 1024,
    "voyage-law-2": 1024,
}

VOYAGE_MODEL_ALLOWED_DIMENSIONS = {
    "voyage-3": {256, 512, 1024, 2048},
    "voyage-3-large": {256, 512, 1024, 2048},
    "voyage-3.5": {256, 512, 1024, 2048},
    "voyage-3.5-lite": {256, 512, 1024, 2048},
    "voyage-4": {256, 512, 1024, 2048},
    "voyage-4-lite": {256, 512, 1024, 2048},
    "voyage-4-large": {256, 512, 1024, 2048},
    "voyage-code-3": {256, 512, 1024, 2048},
}


def get_voyage_model_default_dimension(model_name: Optional[str]) -> int:
    """Get the default output dimension for a Voyage text embedding model."""
    if not model_name:
        return 1024
    return VOYAGE_MODEL_DIMENSIONS.get(model_name.lower(), 1024)


class VoyageDenseEmbedder(DenseEmbedderBase):
    """Voyage AI dense embedder.

    Voyage uses an OpenAI-compatible embeddings endpoint, but dimension
    control is sent via ``output_dimension`` in ``extra_body``.
    """

    def __init__(
        self,
        model_name: str = "voyage-4-lite",
        api_key: Optional[str] = None,
        api_base: Optional[str] = None,
        dimension: Optional[int] = None,
        config: Optional[Dict[str, Any]] = None,
    ):
        super().__init__(model_name, config)

        self.api_key = api_key
        self.api_base = api_base or "https://api.voyageai.com/v1"
        self.dimension = dimension

        if not self.api_key:
            raise ValueError("api_key is required")

        normalized_model_name = model_name.lower()
        supported_dimensions = VOYAGE_MODEL_ALLOWED_DIMENSIONS.get(normalized_model_name)
        if supported_dimensions and dimension is not None and dimension not in supported_dimensions:
            supported = ", ".join(str(value) for value in sorted(supported_dimensions))
            raise ValueError(
                f"Requested dimension {dimension} is not supported for model '{model_name}'. "
                f"Supported dimensions: {supported}."
            )

        self.client = openai.OpenAI(
            api_key=self.api_key,
            base_url=self.api_base,
        )
        self._async_client = None

        self._dimension = dimension or get_voyage_model_default_dimension(normalized_model_name)

    def _build_kwargs(self, text_input: str | List[str]) -> Dict[str, Any]:
        kwargs: Dict[str, Any] = {"input": text_input, "model": self.model_name}
        if self.dimension is not None:
            kwargs["extra_body"] = {"output_dimension": self.dimension}
        return kwargs

    def _get_async_client(self):
        if self._async_client is None:
            self._async_client = openai.AsyncOpenAI(
                api_key=self.api_key,
                base_url=self.api_base,
            )
        return self._async_client

    def embed(self, text: str, is_query: bool = False) -> EmbedResult:
        """Perform dense embedding on text."""

        def _call() -> EmbedResult:
            response = self.client.embeddings.create(**self._build_kwargs(text))
            vector = response.data[0].embedding
            return EmbedResult(dense_vector=vector)

        try:
            result = self._run_with_retry(
                _call,
                logger=logger,
                operation_name="Voyage embedding",
            )
            # Estimate token usage
            estimated_tokens = self._estimate_tokens(text)
            self.update_token_usage(
                model_name=self.model_name,
                provider="voyage",
                prompt_tokens=estimated_tokens,
                completion_tokens=0,
            )
            return result
        except openai.APIError as e:
            raise RuntimeError(f"Voyage API error: {e.message}") from e
        except Exception as e:
            raise RuntimeError(f"Embedding failed: {str(e)}") from e

    async def embed_async(self, text: str, is_query: bool = False) -> EmbedResult:
        client = self._get_async_client()

        async def _call() -> EmbedResult:
            response = await client.embeddings.create(**self._build_kwargs(text))
            return EmbedResult(dense_vector=response.data[0].embedding)

        try:
            result = await self._run_with_async_retry(
                _call,
                logger=logger,
                operation_name="Voyage async embedding",
            )
            estimated_tokens = self._estimate_tokens(text)
            self.update_token_usage(
                model_name=self.model_name,
                provider="voyage",
                prompt_tokens=estimated_tokens,
                completion_tokens=0,
            )
            return result
        except openai.APIError as e:
            raise RuntimeError(f"Voyage API error: {e.message}") from e
        except Exception as e:
            raise RuntimeError(f"Embedding failed: {str(e)}") from e

    def embed_batch(self, texts: List[str], is_query: bool = False) -> List[EmbedResult]:
        """Batch embedding."""
        if not texts:
            return []

        def _call() -> List[EmbedResult]:
            response = self.client.embeddings.create(**self._build_kwargs(texts))
            return [EmbedResult(dense_vector=item.embedding) for item in response.data]

        try:
            results = self._run_with_retry(
                _call,
                logger=logger,
                operation_name="Voyage batch embedding",
            )
            # Estimate token usage for batch
            total_tokens = sum(self._estimate_tokens(text) for text in texts)
            self.update_token_usage(
                model_name=self.model_name,
                provider="voyage",
                prompt_tokens=total_tokens,
                completion_tokens=0,
            )
            return results
        except openai.APIError as e:
            raise RuntimeError(f"Voyage API error: {e.message}") from e
        except Exception as e:
            raise RuntimeError(f"Batch embedding failed: {str(e)}") from e

    async def embed_batch_async(
        self, texts: List[str], is_query: bool = False
    ) -> List[EmbedResult]:
        if not texts:
            return []

        client = self._get_async_client()

        async def _call() -> List[EmbedResult]:
            response = await client.embeddings.create(**self._build_kwargs(texts))
            return [EmbedResult(dense_vector=item.embedding) for item in response.data]

        try:
            results = await self._run_with_async_retry(
                _call,
                logger=logger,
                operation_name="Voyage async batch embedding",
            )
            total_tokens = sum(self._estimate_tokens(text) for text in texts)
            self.update_token_usage(
                model_name=self.model_name,
                provider="voyage",
                prompt_tokens=total_tokens,
                completion_tokens=0,
            )
            return results
        except openai.APIError as e:
            raise RuntimeError(f"Voyage API error: {e.message}") from e
        except Exception as e:
            raise RuntimeError(f"Batch embedding failed: {str(e)}") from e

    def get_dimension(self) -> int:
        """Get embedding dimension."""
        return self._dimension
