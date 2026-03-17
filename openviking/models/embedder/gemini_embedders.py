# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: Apache-2.0
"""Gemini Embedding 2 provider using the official google-genai SDK."""

from typing import Any, Dict, List, Optional

from google import genai
from google.genai import types
from google.genai.errors import APIError

import logging

try:
    import anyio
    _ANYIO_AVAILABLE = True
except ImportError:
    _ANYIO_AVAILABLE = False

from openviking.models.embedder.base import (
    DenseEmbedderBase,
    EmbedResult,
    truncate_and_normalize,
)

logger = logging.getLogger("gemini_embedders")

_TEXT_BATCH_SIZE = 100

# Maximum input tokens per Gemini embedding request (model hard limit).
_GEMINI_INPUT_TOKEN_LIMIT = 8192


class GeminiDenseEmbedder(DenseEmbedderBase):
    """Dense embedder backed by Google's Gemini Embedding 2 model.

    Input token limit: 8,192 tokens per request.
    Output dimension: 128–3072 (recommended: 768, 1536, 3072; default: 3072).
    """

    KNOWN_DIMENSIONS: Dict[str, int] = {
        "gemini-embedding-2-preview": 3072,
        "gemini-embedding-001": 3072,
        "text-embedding-004": 768,
    }

    def __init__(
        self,
        model_name: str = "gemini-embedding-2-preview",
        api_key: Optional[str] = None,
        dimension: Optional[int] = None,
        task_type: Optional[str] = None,
        max_concurrent_batches: int = 10,
        config: Optional[Dict[str, Any]] = None,
    ):
        super().__init__(model_name, config)
        if not api_key:
            raise ValueError("Gemini provider requires api_key")
        self.client = genai.Client(api_key=api_key)
        self.task_type = task_type
        self._dimension = dimension or self.KNOWN_DIMENSIONS.get(model_name, 3072)
        self._max_concurrent_batches = max_concurrent_batches
        config_kwargs: Dict[str, Any] = {"output_dimensionality": self._dimension}
        if self.task_type:
            config_kwargs["task_type"] = self.task_type
        self._embed_config = types.EmbedContentConfig(**config_kwargs)

    def embed(self, text: str) -> EmbedResult:
        try:
            result = self.client.models.embed_content(
                model=self.model_name,
                contents=text,
                config=self._embed_config,
            )
            vector = truncate_and_normalize(list(result.embeddings[0].values), self._dimension)
            return EmbedResult(dense_vector=vector)
        except APIError as e:
            raise RuntimeError(f"Gemini embedding failed (code={e.code}): {e}") from e

    def embed_batch(self, texts: List[str]) -> List[EmbedResult]:
        if not texts:
            return []
        results: List[EmbedResult] = []
        for i in range(0, len(texts), _TEXT_BATCH_SIZE):
            batch = texts[i : i + _TEXT_BATCH_SIZE]
            try:
                response = self.client.models.embed_content(
                    model=self.model_name,
                    contents=batch,
                    config=self._embed_config,
                )
                for emb in response.embeddings:
                    vector = truncate_and_normalize(list(emb.values), self._dimension)
                    results.append(EmbedResult(dense_vector=vector))
            except APIError as e:
                logger.warning(
                    f"Gemini batch embed failed (code={e.code}) for batch of {len(batch)}, "
                    "falling back to individual calls"
                )
                for text in batch:
                    results.append(self.embed(text))
        return results

    async def async_embed_batch(self, texts: List[str]) -> List[EmbedResult]:
        """Concurrent batch embedding via client.aio — requires anyio to be installed.

        Dispatches all 100-text chunks in parallel, bounded by max_concurrent_batches.
        Per-batch APIError falls back to individual embed() calls via thread pool.
        Raises ImportError if anyio is not installed.
        """
        if not _ANYIO_AVAILABLE:
            raise ImportError(
                "anyio is required for async_embed_batch: pip install 'openviking[gemini-async]'"
            )
        if not texts:
            return []
        batches = [texts[i : i + _TEXT_BATCH_SIZE] for i in range(0, len(texts), _TEXT_BATCH_SIZE)]
        results: List[Optional[List[EmbedResult]]] = [None] * len(batches)
        sem = anyio.Semaphore(self._max_concurrent_batches)

        async def _embed_one(idx: int, batch: List[str]) -> None:
            async with sem:
                try:
                    response = await self.client.aio.models.embed_content(
                        model=self.model_name, contents=batch, config=self._embed_config
                    )
                    results[idx] = [
                        EmbedResult(
                            dense_vector=truncate_and_normalize(list(emb.values), self._dimension)
                        )
                        for emb in response.embeddings
                    ]
                except APIError as e:
                    logger.warning(
                        f"Gemini batch embed failed (code={e.code}) for batch of {len(batch)}, "
                        "falling back to individual calls"
                    )
                    results[idx] = [
                        await anyio.to_thread.run_sync(self.embed, text) for text in batch
                    ]

        async with anyio.create_task_group() as tg:
            for idx, batch in enumerate(batches):
                tg.start_soon(_embed_one, idx, batch)

        return [r for batch_results in results for r in (batch_results or [])]

    def get_dimension(self) -> int:
        return self._dimension

    def close(self):
        if hasattr(self.client, "_http_client"):
            try:
                self.client._http_client.close()
            except Exception:
                pass
