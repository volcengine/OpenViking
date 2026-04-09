# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0
"""LiteLLM Embedder Implementation

Uses litellm to provide a unified embedding interface across many providers
(OpenRouter, Ollama, vLLM, and any OpenAI-compatible endpoint).
"""

import logging
import os
from typing import Any, Dict, List, Optional

import litellm

from openviking.models.embedder.base import DenseEmbedderBase, EmbedResult
from openviking.telemetry import get_current_telemetry

logger = logging.getLogger(__name__)


class LiteLLMDenseEmbedder(DenseEmbedderBase):
    """LiteLLM Dense Embedder Implementation

    Routes embedding requests through litellm, supporting dozens of providers
    via a unified interface. Model names use litellm's provider/model format
    (e.g., "openai/text-embedding-3-small", "ollama/nomic-embed-text").

    Example:
        >>> # OpenRouter embeddings
        >>> embedder = LiteLLMDenseEmbedder(
        ...     model_name="openai/text-embedding-3-small",
        ...     api_key="sk-or-...",
        ...     api_base="https://openrouter.ai/api/v1",
        ...     dimension=1536,
        ... )
        >>> result = embedder.embed("Hello world")

        >>> # Local Ollama embeddings
        >>> embedder = LiteLLMDenseEmbedder(
        ...     model_name="ollama/nomic-embed-text",
        ...     api_base="http://localhost:11434",
        ...     dimension=768,
        ... )
        >>> result = embedder.embed("Hello world")
    """

    def __init__(
        self,
        model_name: str,
        api_key: Optional[str] = None,
        api_base: Optional[str] = None,
        dimension: Optional[int] = None,
        query_param: Optional[str] = None,
        document_param: Optional[str] = None,
        extra_headers: Optional[Dict[str, str]] = None,
        config: Optional[Dict[str, Any]] = None,
    ):
        """Initialize LiteLLM Dense Embedder

        Args:
            model_name: Model name in litellm format (e.g., "openai/text-embedding-3-small").
            api_key: API key for the provider. Falls back to provider-specific env vars.
            api_base: Custom API base URL (e.g., "https://openrouter.ai/api/v1").
            dimension: Embedding vector dimension (required).
            query_param: Parameter value for query-side embeddings (non-symmetric mode).
            document_param: Parameter value for document-side embeddings (non-symmetric mode).
            extra_headers: Extra HTTP headers for API requests.
            config: Additional configuration dict.
        """
        super().__init__(model_name, config)

        os.environ.setdefault("LITELLM_LOCAL_MODEL_COST_MAP", "True")

        self.api_key = api_key
        self.api_base = api_base
        self.dimension = dimension
        self.query_param = query_param
        self.document_param = document_param
        self.extra_headers = extra_headers

        if dimension is None:
            raise ValueError(
                "LiteLLM embedding provider requires 'dimension' to be set explicitly. "
                "Check your embedding model's documentation for the correct dimension."
            )
        self._dimension = dimension

    def _build_kwargs(self, is_query: bool = False) -> Dict[str, Any]:
        """Build kwargs dict for litellm.embedding() call."""
        kwargs: Dict[str, Any] = {"model": self.model_name}

        if self.api_key:
            kwargs["api_key"] = self.api_key
        if self.api_base:
            kwargs["api_base"] = self.api_base
        if self.extra_headers:
            kwargs["extra_headers"] = self.extra_headers
        if self.dimension:
            kwargs["dimensions"] = self.dimension

        # Non-symmetric embedding support
        active_param = None
        if is_query and self.query_param is not None:
            active_param = self.query_param
        elif not is_query and self.document_param is not None:
            active_param = self.document_param

        if active_param:
            if "=" in active_param:
                # Parse key=value format (e.g., "input_type=query,task=search")
                extra_body = {}
                for part in active_param.split(","):
                    part = part.strip()
                    if "=" in part:
                        key, value = part.split("=", 1)
                        extra_body[key.strip()] = value.strip()
                if extra_body:
                    kwargs["extra_body"] = extra_body
            else:
                kwargs["input_type"] = active_param

        return kwargs

    def _update_telemetry_token_usage(self, response) -> None:
        """Update telemetry and token usage from response."""
        usage = getattr(response, "usage", None)
        if not usage:
            return

        def _usage_value(key: str, default: int = 0) -> int:
            if isinstance(usage, dict):
                return int(usage.get(key, default) or default)
            return int(getattr(usage, key, default) or default)

        prompt_tokens = _usage_value("prompt_tokens", 0)
        total_tokens = _usage_value("total_tokens", prompt_tokens)
        output_tokens = max(total_tokens - prompt_tokens, 0)

        # Update telemetry
        get_current_telemetry().add_token_usage_by_source(
            "embedding",
            prompt_tokens,
            output_tokens,
        )

        # Update token usage tracker
        self.update_token_usage(
            model_name=self.model_name,
            provider="litellm",
            prompt_tokens=prompt_tokens,
            completion_tokens=output_tokens,
        )

    def embed(self, text: str, is_query: bool = False) -> EmbedResult:
        """Perform dense embedding on text via litellm.

        Args:
            text: Input text
            is_query: Flag to indicate if this is a query embedding

        Returns:
            EmbedResult: Result containing dense_vector

        Raises:
            RuntimeError: When embedding call fails
        """

        def _call() -> EmbedResult:
            kwargs = self._build_kwargs(is_query=is_query)
            kwargs["input"] = [text]
            response = litellm.embedding(**kwargs)
            self._update_telemetry_token_usage(response)
            vector = response.data[0]["embedding"]
            return EmbedResult(dense_vector=vector)

        try:
            return self._run_with_retry(
                _call,
                logger=logger,
                operation_name="LiteLLM embedding",
            )
        except Exception as e:
            raise RuntimeError(f"LiteLLM embedding failed: {e}") from e

    async def embed_async(self, text: str, is_query: bool = False) -> EmbedResult:
        async def _call() -> EmbedResult:
            kwargs = self._build_kwargs(is_query=is_query)
            kwargs["input"] = [text]
            response = await litellm.aembedding(**kwargs)
            self._update_telemetry_token_usage(response)
            vector = response.data[0]["embedding"]
            return EmbedResult(dense_vector=vector)

        try:
            return await self._run_with_async_retry(
                _call,
                logger=logger,
                operation_name="LiteLLM async embedding",
            )
        except Exception as e:
            raise RuntimeError(f"LiteLLM embedding failed: {e}") from e

    def embed_batch(self, texts: List[str], is_query: bool = False) -> List[EmbedResult]:
        """Batch embedding via litellm.

        Args:
            texts: List of texts
            is_query: Flag to indicate if these are query embeddings

        Returns:
            List[EmbedResult]: List of embedding results

        Raises:
            RuntimeError: When embedding call fails
        """
        if not texts:
            return []

        def _call() -> List[EmbedResult]:
            kwargs = self._build_kwargs(is_query=is_query)
            kwargs["input"] = texts
            response = litellm.embedding(**kwargs)
            self._update_telemetry_token_usage(response)
            return [EmbedResult(dense_vector=item["embedding"]) for item in response.data]

        try:
            return self._run_with_retry(
                _call,
                logger=logger,
                operation_name="LiteLLM batch embedding",
            )
        except Exception as e:
            raise RuntimeError(f"LiteLLM batch embedding failed: {e}") from e

    async def embed_batch_async(
        self, texts: List[str], is_query: bool = False
    ) -> List[EmbedResult]:
        if not texts:
            return []

        async def _call() -> List[EmbedResult]:
            kwargs = self._build_kwargs(is_query=is_query)
            kwargs["input"] = texts
            response = await litellm.aembedding(**kwargs)
            self._update_telemetry_token_usage(response)
            return [EmbedResult(dense_vector=item["embedding"]) for item in response.data]

        try:
            return await self._run_with_async_retry(
                _call,
                logger=logger,
                operation_name="LiteLLM async batch embedding",
            )
        except Exception as e:
            raise RuntimeError(f"LiteLLM batch embedding failed: {e}") from e

    def get_dimension(self) -> int:
        """Get embedding dimension.

        Returns:
            int: Vector dimension
        """
        return self._dimension
