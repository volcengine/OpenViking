# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0
"""Gemini Embedding 2 provider using the official google-genai SDK."""

import asyncio
from typing import Any, Dict, List, Optional

from google import genai
from google.genai import types
from google.genai.errors import APIError, ClientError

try:
    from google.genai.types import HttpOptions, HttpRetryOptions

    _HTTP_RETRY_AVAILABLE = True
except ImportError:
    _HTTP_RETRY_AVAILABLE = False

import logging

from openviking.models.embedder.base import (
    DenseEmbedderBase,
    EmbedResult,
    truncate_and_normalize,
)

logger = logging.getLogger("gemini_embedders")

_TEXT_BATCH_SIZE = 100

# Keep for backward-compat with existing unit tests that import it
_GEMINI_INPUT_TOKEN_LIMIT = 8192  # gemini-embedding-2-preview hard limit

# Per-model token limits (Google API hard limits, from official docs)
_MODEL_TOKEN_LIMITS: Dict[str, int] = {
    "gemini-embedding-2-preview": 8192,
    "gemini-embedding-001": 2048,
}
_DEFAULT_TOKEN_LIMIT = 2048  # conservative fallback for unknown future models

_VALID_TASK_TYPES: frozenset = frozenset(
    {
        "RETRIEVAL_QUERY",
        "RETRIEVAL_DOCUMENT",
        "SEMANTIC_SIMILARITY",
        "CLASSIFICATION",
        "CLUSTERING",
        "QUESTION_ANSWERING",
        "FACT_VERIFICATION",
        "CODE_RETRIEVAL_QUERY",
    }
)

_ERROR_HINTS: Dict[int, str] = {
    400: "Invalid request — check model name and task_type value.",
    401: "Invalid API key. Verify your GOOGLE_API_KEY or api_key in config.",
    403: "Permission denied. API key may lack access to this model.",
    404: "Model not found: '{model}'. Check spelling (e.g. 'gemini-embedding-2-preview').",
    429: "Quota exceeded. Wait and retry, or increase your Google API quota.",
    500: "Gemini service error (Google-side). Retry after a delay.",
    503: "Gemini service unavailable. Retry after a delay.",
}


def _raise_api_error(e: APIError, model: str) -> None:
    hint = _ERROR_HINTS.get(e.code, "")
    # Gemini returns HTTP 400 (not 401) when the API key is invalid
    if e.code == 400 and "api key" in str(e).lower():
        hint = "Invalid API key. Verify your GOOGLE_API_KEY or api_key in config."
    msg = f"Gemini embedding failed (HTTP {e.code})"
    if hint:
        msg += f": {hint.format(model=model)}"
    raise RuntimeError(msg) from e


class GeminiDenseEmbedder(DenseEmbedderBase):
    """Dense embedder backed by Google's Gemini Embedding models.

    REST endpoint: /v1beta/models/{model}:embedContent (SDK handles Parts format internally).
    Input token limit: per-model (8192 for gemini-embedding-2-preview, 2048 for gemini-embedding-001).
    Output dimension: 1–3072 (MRL; recommended 768, 1536, 3072; default 3072).
    Task types: RETRIEVAL_QUERY, RETRIEVAL_DOCUMENT, SEMANTIC_SIMILARITY, CLASSIFICATION,
                CLUSTERING, CODE_RETRIEVAL_QUERY, QUESTION_ANSWERING, FACT_VERIFICATION.
    Non-symmetric: use query_param/document_param in EmbeddingModelConfig.
    """

    # Default output dimensions per model (used when user does not specify `dimension`).
    # gemini-embedding-2-preview: 3072 MRL model — supports 1–3072 via output_dimensionality
    # gemini-embedding-001:       3072 (native 768-dim vectors; 3072 shown as default for MRL compat)
    # text-embedding-004:         768  fixed-dim legacy model, does not support MRL truncation
    # Future gemini-embedding-*:  default 3072 via _default_dimension() fallback
    # Future text-embedding-*:    default 768  via _default_dimension() prefix rule
    supports_multimodal: bool = False  # text-only; multimodal planned separately

    KNOWN_DIMENSIONS: Dict[str, int] = {
        "gemini-embedding-2-preview": 3072,
        "gemini-embedding-001": 3072,
        "text-embedding-004": 768,
    }

    @classmethod
    def _default_dimension(cls, model: str) -> int:
        """Return default output dimension for a Gemini model.

        Lookup order:
        1. Exact match in KNOWN_DIMENSIONS
        2. Prefix rule: text-embedding-* → 768 (legacy fixed-dim series)
        3. Fallback: 3072 (gemini-embedding-* MRL models)

        Examples:
            gemini-embedding-2-preview → 3072 (exact match)
            gemini-embedding-2         → 3072 (fallback — future model)
            text-embedding-004         → 768  (exact match)
            text-embedding-005         → 768  (prefix rule — future model)
        """
        if model in cls.KNOWN_DIMENSIONS:
            return cls.KNOWN_DIMENSIONS[model]
        if model.startswith("text-embedding-"):
            return 768
        return 3072

    def __init__(
        self,
        model_name: str = "gemini-embedding-2-preview",
        api_key: Optional[str] = None,
        dimension: Optional[int] = None,
        task_type: Optional[str] = None,
        query_param: Optional[str] = None,
        document_param: Optional[str] = None,
        max_concurrent_batches: int = 10,
        config: Optional[Dict[str, Any]] = None,
    ):
        super().__init__(model_name, config)
        if not api_key:
            raise ValueError("Gemini provider requires api_key")
        if task_type and task_type not in _VALID_TASK_TYPES:
            raise ValueError(
                f"Invalid task_type '{task_type}'. "
                f"Valid values: {', '.join(sorted(_VALID_TASK_TYPES))}"
            )
        if dimension is not None and not (1 <= dimension <= 3072):
            raise ValueError(f"dimension must be between 1 and 3072, got {dimension}")
        if _HTTP_RETRY_AVAILABLE:
            self.client = genai.Client(
                api_key=api_key,
                http_options=HttpOptions(
                    retry_options=HttpRetryOptions(
                        attempts=max(self.max_retries + 1, 1),
                        initial_delay=0.5,
                        max_delay=8.0,
                        exp_base=2.0,
                    )
                ),
            )
        else:
            self.client = genai.Client(api_key=api_key)
        self.task_type = task_type
        self.query_param = query_param
        self.document_param = document_param
        self._dimension = dimension or self._default_dimension(model_name)
        self._token_limit = _MODEL_TOKEN_LIMITS.get(model_name, _DEFAULT_TOKEN_LIMIT)
        self._max_concurrent_batches = max_concurrent_batches

    def _build_config(
        self,
        *,
        task_type: Optional[str] = None,
        title: Optional[str] = None,
    ) -> types.EmbedContentConfig:
        """Build EmbedContentConfig, merging per-call overrides with instance defaults."""
        effective_task_type = task_type or self.task_type
        kwargs: Dict[str, Any] = {"output_dimensionality": self._dimension}
        if effective_task_type:
            kwargs["task_type"] = effective_task_type.upper()
        if title:
            kwargs["title"] = title
        return types.EmbedContentConfig(**kwargs)

    def _resolve_task_type(
        self,
        *,
        is_query: bool = False,
        task_type: Optional[str] = None,
    ) -> Optional[str]:
        if task_type is None:
            if is_query and self.query_param:
                task_type = self.query_param
            elif not is_query and self.document_param:
                task_type = self.document_param
        return task_type

    def __repr__(self) -> str:
        return (
            f"GeminiDenseEmbedder("
            f"model={self.model_name!r}, "
            f"dim={self._dimension}, "
            f"task_type={self.task_type!r})"
        )

    def embed(
        self,
        text: str,
        is_query: bool = False,
        *,
        task_type: Optional[str] = None,
        title: Optional[str] = None,
    ) -> EmbedResult:
        if not text or not text.strip():
            logger.warning("Empty text passed to embed(), returning zero vector")
            return EmbedResult(dense_vector=[0.0] * self._dimension)
        task_type = self._resolve_task_type(is_query=is_query, task_type=task_type)

        # SDK accepts plain str; converts to REST Parts format internally.
        def _call() -> EmbedResult:
            result = self.client.models.embed_content(
                model=self.model_name,
                contents=text,
                config=self._build_config(task_type=task_type, title=title),
            )
            vector = truncate_and_normalize(list(result.embeddings[0].values), self._dimension)
            return EmbedResult(dense_vector=vector)

        try:
            result = (
                _call()
                if _HTTP_RETRY_AVAILABLE
                else self._run_with_retry(
                    _call,
                    logger=logger,
                    operation_name="Gemini embedding",
                )
            )
            # Estimate token usage
            estimated_tokens = self._estimate_tokens(text)
            self.update_token_usage(
                model_name=self.model_name,
                provider="gemini",
                prompt_tokens=estimated_tokens,
                completion_tokens=0,
            )
            return result
        except (APIError, ClientError) as e:
            _raise_api_error(e, self.model_name)

    async def embed_async(
        self,
        text: str,
        is_query: bool = False,
        *,
        task_type: Optional[str] = None,
        title: Optional[str] = None,
    ) -> EmbedResult:
        if not text or not text.strip():
            logger.warning("Empty text passed to embed_async(), returning zero vector")
            return EmbedResult(dense_vector=[0.0] * self._dimension)

        task_type = self._resolve_task_type(is_query=is_query, task_type=task_type)

        async def _call() -> EmbedResult:
            result = await self.client.aio.models.embed_content(
                model=self.model_name,
                contents=text,
                config=self._build_config(task_type=task_type, title=title),
            )
            vector = truncate_and_normalize(list(result.embeddings[0].values), self._dimension)
            return EmbedResult(dense_vector=vector)

        try:
            result = await self._run_with_async_retry(
                _call,
                logger=logger,
                operation_name="Gemini async embedding",
            )
            estimated_tokens = self._estimate_tokens(text)
            self.update_token_usage(
                model_name=self.model_name,
                provider="gemini",
                prompt_tokens=estimated_tokens,
                completion_tokens=0,
            )
            return result
        except (APIError, ClientError) as e:
            _raise_api_error(e, self.model_name)

    def embed_batch(
        self,
        texts: List[str],
        is_query: bool = False,
        *,
        task_type: Optional[str] = None,
        titles: Optional[List[str]] = None,
    ) -> List[EmbedResult]:
        if not texts:
            return []
        # When titles are provided, delegate per-item (titles are per-document metadata).
        if titles is not None:
            return [
                self.embed(text, is_query=is_query, task_type=task_type, title=title)
                for text, title in zip(texts, titles, strict=True)
            ]
        task_type = self._resolve_task_type(is_query=is_query, task_type=task_type)
        results: List[EmbedResult] = []
        config = self._build_config(task_type=task_type)
        for i in range(0, len(texts), _TEXT_BATCH_SIZE):
            batch = texts[i : i + _TEXT_BATCH_SIZE]
            non_empty_indices = [j for j, t in enumerate(batch) if t and t.strip()]
            empty_indices = [j for j, t in enumerate(batch) if not (t and t.strip())]

            if not non_empty_indices:
                results.extend(EmbedResult(dense_vector=[0.0] * self._dimension) for _ in batch)
                continue

            non_empty_texts = [batch[j] for j in non_empty_indices]

            def _call_batch(
                non_empty_texts: List[str] = non_empty_texts,
                config: types.EmbedContentConfig = config,
            ) -> Any:
                response = self.client.models.embed_content(
                    model=self.model_name,
                    contents=non_empty_texts,
                    config=config,
                )
                return response

            try:
                if _HTTP_RETRY_AVAILABLE:
                    response = _call_batch()
                else:
                    response = self._run_with_retry(
                        _call_batch,
                        logger=logger,
                        operation_name="Gemini batch embedding",
                    )
                batch_results = [None] * len(batch)
                for j, emb in zip(non_empty_indices, response.embeddings, strict=True):
                    batch_results[j] = EmbedResult(
                        dense_vector=truncate_and_normalize(list(emb.values), self._dimension)
                    )
                for j in empty_indices:
                    batch_results[j] = EmbedResult(dense_vector=[0.0] * self._dimension)
                results.extend(batch_results)
            except (APIError, ClientError) as e:
                logger.warning(
                    "Gemini batch embed failed (HTTP %d) for batch of %d, falling back to individual",
                    e.code,
                    len(batch),
                )
                for text in batch:
                    results.append(self.embed(text, is_query=is_query))
        # Token usage is already tracked via individual embed() calls
        # No need to track here to avoid double counting
        return results

    async def embed_batch_async(
        self,
        texts: List[str],
        is_query: bool = False,
        *,
        task_type: Optional[str] = None,
        titles: Optional[List[str]] = None,
    ) -> List[EmbedResult]:
        if not texts:
            return []
        if titles is not None:
            return [
                await self.embed_async(
                    text,
                    is_query=is_query,
                    task_type=task_type,
                    title=title,
                )
                for text, title in zip(texts, titles, strict=True)
            ]

        task_type = self._resolve_task_type(is_query=is_query, task_type=task_type)
        batches = [texts[i : i + _TEXT_BATCH_SIZE] for i in range(0, len(texts), _TEXT_BATCH_SIZE)]
        results: List[Optional[List[EmbedResult]]] = [None] * len(batches)
        sem = asyncio.Semaphore(self._max_concurrent_batches)

        async def _embed_one(idx: int, batch: List[str]) -> None:
            async with sem:
                non_empty_indices = [j for j, t in enumerate(batch) if t and t.strip()]
                empty_indices = [j for j, t in enumerate(batch) if not (t and t.strip())]
                batch_results: List[Optional[EmbedResult]] = [None] * len(batch)
                for j in empty_indices:
                    batch_results[j] = EmbedResult(dense_vector=[0.0] * self._dimension)

                if not non_empty_indices:
                    results[idx] = [r for r in batch_results if r is not None]
                    return

                non_empty_texts = [batch[j] for j in non_empty_indices]

                async def _call_batch() -> Any:
                    return await self.client.aio.models.embed_content(
                        model=self.model_name,
                        contents=non_empty_texts,
                        config=self._build_config(task_type=task_type),
                    )

                try:
                    response = await self._run_with_async_retry(
                        _call_batch,
                        logger=logger,
                        operation_name="Gemini async batch embedding",
                    )
                    for j, emb in zip(non_empty_indices, response.embeddings, strict=True):
                        batch_results[j] = EmbedResult(
                            dense_vector=truncate_and_normalize(list(emb.values), self._dimension)
                        )
                    total_tokens = sum(self._estimate_tokens(text) for text in non_empty_texts)
                    self.update_token_usage(
                        model_name=self.model_name,
                        provider="gemini",
                        prompt_tokens=total_tokens,
                        completion_tokens=0,
                    )
                except (APIError, ClientError) as e:
                    logger.warning(
                        "Gemini async batch embed failed (HTTP %d) for batch of %d, falling back to per-item async calls",
                        e.code,
                        len(batch),
                    )
                    for j in non_empty_indices:
                        batch_results[j] = await self.embed_async(
                            batch[j],
                            is_query=is_query,
                            task_type=task_type,
                        )

                results[idx] = [r for r in batch_results if r is not None]

        await asyncio.gather(*(_embed_one(idx, batch) for idx, batch in enumerate(batches)))
        return [r for batch_results in results for r in (batch_results or [])]

    async def async_embed_batch(self, texts: List[str]) -> List[EmbedResult]:
        """Backward-compatible alias for the standardized async batch API."""
        return await self.embed_batch_async(texts)

    def get_dimension(self) -> int:
        return self._dimension

    def close(self):
        if hasattr(self.client, "_http_client"):
            try:
                self.client._http_client.close()
            except Exception:
                pass
