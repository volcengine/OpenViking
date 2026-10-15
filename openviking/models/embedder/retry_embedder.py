# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0
"""Retry decorator for one concrete embedding provider."""

import asyncio
from typing import Any, Dict

from openviking.models.embedder.base import EmbedderBase, EmbeddingInput, EmbedResult
from openviking.utils.model_retry import retry_async, retry_sync
from openviking_cli.utils import get_logger

logger = get_logger(__name__)


class RetryEmbedder(EmbedderBase):
    """Own retries for one concrete embedding target."""

    def __init__(self, inner: EmbedderBase):
        if isinstance(inner, RetryEmbedder):
            raise ValueError("Embedder is already wrapped with RetryEmbedder")
        super().__init__(inner.model_name, inner.config)
        self.inner = inner
        self.provider = inner.provider

    @property
    def query_param(self) -> Any:
        return getattr(self.inner, "query_param", None)

    @property
    def document_param(self) -> Any:
        return getattr(self.inner, "document_param", None)

    def embed(
        self,
        content: EmbeddingInput,
        is_query: bool = False,
        **kwargs: Any,
    ) -> EmbedResult:
        return retry_sync(
            lambda: self.inner.embed(content, is_query=is_query, **kwargs),
            max_retries=self.max_retries,
            logger=logger,
            operation_name=f"{self.provider} embedding",
        )

    async def embed_async(
        self,
        content: EmbeddingInput,
        is_query: bool = False,
        **kwargs: Any,
    ) -> EmbedResult:
        return await retry_async(
            lambda: self._bounded(self.inner.embed_async(content, is_query=is_query, **kwargs)),
            max_retries=self.max_retries,
            logger=logger,
            operation_name=f"{self.provider} async embedding",
        )

    async def _bounded(self, call):
        # SDK/httpx timeouts bound each socket read, so a response that keeps trickling
        # bytes never trips them; ``timeout`` must bound the whole attempt.
        timeout = self.config.get("timeout")
        try:
            return await asyncio.wait_for(call, timeout=timeout)
        except asyncio.TimeoutError as exc:
            # errors are classified by message; "timeout" keeps the attempt retryable
            raise TimeoutError(f"embedding request timeout after {timeout}s") from exc

    def prepare_embedding_input(self, content: EmbeddingInput) -> EmbeddingInput:
        return self.inner.prepare_embedding_input(content)

    @property
    def supports_multimodal(self) -> bool:
        return self.inner.supports_multimodal

    @property
    def is_dense(self) -> bool:
        return self.inner.is_dense

    @property
    def is_sparse(self) -> bool:
        return self.inner.is_sparse

    @property
    def is_hybrid(self) -> bool:
        return self.inner.is_hybrid

    def get_dimension(self) -> int:
        dimension_getter = getattr(self.inner, "get_dimension", None)
        if dimension_getter is None:
            raise AttributeError(
                f"{type(self.inner).__name__} has no get_dimension "
                "(sparse embedders have no fixed dimension)"
            )
        return dimension_getter()

    def close(self) -> None:
        self.inner.close()

    def get_token_usage(self) -> Dict[str, Any]:
        return self.inner.get_token_usage()

    def reset_token_usage(self) -> None:
        self.inner.reset_token_usage()
