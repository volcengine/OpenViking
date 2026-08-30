# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0
"""Volcengine Embedder Implementation"""

import time

from typing import Any, Awaitable, Callable, Dict, List, Optional, TypeVar

import volcenginesdkarkruntime

from openviking.models.embedder.base import (
    DenseEmbedderBase,
    EmbeddingInput,
    EmbedResult,
    HybridEmbedderBase,
    SparseEmbedderBase,
    extract_text_from_content,
    truncate_and_normalize,
)
from openviking.telemetry import get_current_telemetry
from openviking.metrics.datasources import EmbeddingEventDataSource
from openviking.observability.context import get_root_observability_context
from openviking.utils.model_retry import extract_metric_error_code
from openviking.utils.async_client_cache import LoopScopedAsyncClientCache
from openviking_cli.utils.logger import default_logger as logger

VOLCENGINE_CLIENT_REQUEST_ID_HEADER = "X-Client-Request-Id"
VOLCENGINE_CLIENT_REQUEST_ID = "ToB-direct,OpenViking_Service,openviking-service_cn-beijing"
T = TypeVar("T")


def _record_failed_embedding_call(
    embedder, *, duration_seconds: float, error: Exception
) -> None:
    """Emit a failed Ark request attempt with the same model labels as a success."""
    try:
        root_context = get_root_observability_context()
        EmbeddingEventDataSource.record_call(
            provider="volcengine",
            model_name=str(embedder.model_name),
            duration_seconds=max(float(duration_seconds), 0.0),
            prompt_tokens=0,
            completion_tokens=0,
            error_code=extract_metric_error_code(error),
            account_id=root_context.account_id if root_context is not None else None,
        )
    except Exception:
        # Metrics must never change the provider failure behavior.
        return


def _measure_embedding_call(embedder, call: Callable[[], T]) -> tuple[T, float]:
    """Measure one Ark SDK request and emit its failed-attempt metric when needed."""
    started = time.perf_counter()
    try:
        return call(), time.perf_counter() - started
    except Exception as error:
        _record_failed_embedding_call(
            embedder, duration_seconds=time.perf_counter() - started, error=error
        )
        raise


async def _measure_embedding_call_async(
    embedder, call: Callable[[], Awaitable[T]]
) -> tuple[T, float]:
    """Async equivalent of :func:`_measure_embedding_call`."""
    started = time.perf_counter()
    try:
        return await call(), time.perf_counter() - started
    except Exception as error:
        _record_failed_embedding_call(
            embedder, duration_seconds=time.perf_counter() - started, error=error
        )
        raise


def _build_volcengine_headers(extra_headers: Optional[Dict[str, str]]) -> Dict[str, str]:
    headers = dict(extra_headers or {})
    if not any(k.lower() == VOLCENGINE_CLIENT_REQUEST_ID_HEADER.lower() for k in headers):
        headers[VOLCENGINE_CLIENT_REQUEST_ID_HEADER] = VOLCENGINE_CLIENT_REQUEST_ID
    return headers


def to_multimodal_input(content: "EmbeddingInput") -> List[Dict[str, Any]]:
    """Normalize an embedding input into the content-parts payload the Volcengine
    multimodal embeddings API expects.

    A plain string is wrapped as a single text part. A list of content parts
    (text and/or ``image_url``) is passed through unchanged.
    """
    if isinstance(content, str):
        return [{"type": "text", "text": content}]
    return list(content)


def process_sparse_embedding(sparse_data: Any) -> Dict[str, float]:
    """Process sparse embedding data from SDK response"""
    if not sparse_data:
        return {}
    result = {}

    # Helper to extract index/value from an item (dict or object)
    def extract_pair(item):
        idx = getattr(item, "index", None)
        if idx is None and isinstance(item, dict):
            idx = item.get("index")

        val = getattr(item, "value", None)
        if val is None and isinstance(item, dict):
            val = item.get("value")

        return idx, val

    if isinstance(sparse_data, list):
        for item in sparse_data:
            idx, val = extract_pair(item)
            if idx is not None and val is not None:
                result[str(idx)] = float(val)
    elif hasattr(sparse_data, "index"):
        # Single object case (unlikely for vector but possible per type hint)
        idx, val = extract_pair(sparse_data)
        if idx is not None and val is not None:
            result[str(idx)] = float(val)
    elif isinstance(sparse_data, dict):
        # Maybe a direct dict?
        return {str(k): float(v) for k, v in sparse_data.items()}

    return result


class VolcengineDenseEmbedder(DenseEmbedderBase):
    """Volcengine Dense Embedder Implementation

    Supports Volcengine embedding models such as doubao-embedding.
    """

    def __init__(
        self,
        model_name: str,
        api_key: Optional[str] = None,
        api_base: Optional[str] = None,
        dimension: Optional[int] = None,
        input_type: str = "multimodal",
        extra_headers: Optional[Dict[str, str]] = None,
        config: Optional[Dict[str, Any]] = None,
    ):
        """Initialize Volcengine Dense Embedder

        Args:
            model_name: Volcengine model name (e.g., doubao-embedding)
            api_key: API key for authentication
            api_base: API base URL
            dimension: Target dimension for truncation (optional)
            input_type: Input type - "text" or "multimodal" (default: "multimodal")
            config: Additional configuration dict

        Raises:
            ValueError: If api_key is not provided
        """
        super().__init__(model_name, config)
        self.provider = "volcengine"

        self.api_key = api_key
        self.api_base = api_base or "https://ark.cn-beijing.volces.com/api/v3"
        self.dimension = dimension
        self.input_type = input_type
        self.extra_headers = _build_volcengine_headers(extra_headers)

        if not self.api_key:
            raise ValueError("api_key is required")

        # Initialize Volcengine client
        ark_kwargs = {"api_key": self.api_key}
        if self.api_base:
            ark_kwargs["base_url"] = self.api_base
        self.client = volcenginesdkarkruntime.Ark(**ark_kwargs)
        self._ark_kwargs = ark_kwargs
        self._async_client_cache = LoopScopedAsyncClientCache()

        # Auto-detect dimension
        self._dimension = dimension
        if self._dimension is None:
            self._dimension = self._detect_dimension()

    def _detect_dimension(self) -> int:
        """Detect dimension by making an actual API call"""
        try:
            result = self.embed("test")
            return len(result.dense_vector) if result.dense_vector else 2048
        except Exception:
            return 2048  # Default dimension

    @property
    def supports_multimodal(self) -> bool:
        """Multimodal inputs are supported when using the multimodal endpoint."""
        return self.input_type == "multimodal"

    def _update_telemetry_token_usage(self, response, *, duration_seconds: float) -> None:
        usage = getattr(response, "usage", None)
        if not usage:
            return

        def _usage_value(key: str, default: int = 0) -> int:
            if isinstance(usage, dict):
                return int(usage.get(key, default) or default)
            return int(getattr(usage, key, default) or default)

        prompt_tokens = _usage_value("prompt_tokens", 0)
        total_tokens = _usage_value("total_tokens", prompt_tokens)
        completion_tokens = max(total_tokens - prompt_tokens, 0)

        # Update telemetry
        get_current_telemetry().add_token_usage_by_source(
            "embedding",
            prompt_tokens,
            completion_tokens,
        )

        # Update token tracker
        self.update_token_usage(
            model_name=self.model_name,
            provider="volcengine",
            prompt_tokens=prompt_tokens,
            completion_tokens=completion_tokens,
            duration_seconds=duration_seconds,
        )

    def embed(self, content: "EmbeddingInput", is_query: bool = False) -> EmbedResult:
        """Perform dense embedding on text or multimodal content

        Args:
            content: Input text, or a list of multimodal content parts such as
                ``[{"type": "text", "text": "..."}, {"type": "image_url", "image_url": {"url": "..."}}]``
            is_query: Flag to indicate if this is a query embedding

        Returns:
            EmbedResult: Result containing dense_vector

        Raises:
            RuntimeError: When API call fails
        """

        def _embed_call():
            if self.input_type == "multimodal":
                # Use multimodal embeddings API
                response, duration_seconds = _measure_embedding_call(
                    self,
                    lambda: self.client.multimodal_embeddings.create(
                        input=to_multimodal_input(content),
                        model=self.model_name,
                        extra_headers=self.extra_headers,
                    ),
                )
                self._update_telemetry_token_usage(
                    response, duration_seconds=duration_seconds
                )
                vector = response.data.embedding
            else:
                # Use text embeddings API (text-only)
                text = extract_text_from_content(content)
                response, duration_seconds = _measure_embedding_call(
                    self,
                    lambda: self.client.embeddings.create(
                        input=text,
                        model=self.model_name,
                        extra_headers=self.extra_headers,
                    ),
                )
                self._update_telemetry_token_usage(
                    response, duration_seconds=duration_seconds
                )
                vector = response.data[0].embedding

            vector = truncate_and_normalize(vector, self.dimension)
            return EmbedResult(dense_vector=vector)

        try:
            return self._run_with_retry(
                _embed_call,
                logger=logger,
                operation_name="Volcengine embedding",
            )
        except Exception as e:
            raise RuntimeError(f"Volcengine embedding failed: {str(e)}") from e

    def _get_async_client(self):
        return self._async_client_cache.get(
            lambda: volcenginesdkarkruntime.AsyncArk(**self._ark_kwargs)
        )

    async def embed_async(self, content: "EmbeddingInput", is_query: bool = False) -> EmbedResult:
        client = self._get_async_client()

        async def _embed_call() -> EmbedResult:
            if self.input_type == "multimodal":
                response, duration_seconds = await _measure_embedding_call_async(
                    self,
                    lambda: client.multimodal_embeddings.create(
                        input=to_multimodal_input(content),
                        model=self.model_name,
                        extra_headers=self.extra_headers,
                    ),
                )
                self._update_telemetry_token_usage(
                    response, duration_seconds=duration_seconds
                )
                vector = response.data.embedding
            else:
                text = extract_text_from_content(content)
                response, duration_seconds = await _measure_embedding_call_async(
                    self,
                    lambda: client.embeddings.create(
                        input=text,
                        model=self.model_name,
                        extra_headers=self.extra_headers,
                    ),
                )
                self._update_telemetry_token_usage(
                    response, duration_seconds=duration_seconds
                )
                vector = response.data[0].embedding

            return EmbedResult(dense_vector=truncate_and_normalize(vector, self.dimension))

        try:
            return await self._run_with_async_retry(
                _embed_call,
                logger=logger,
                operation_name="Volcengine async embedding",
            )
        except Exception as e:
            raise RuntimeError(f"Volcengine embedding failed: {str(e)}") from e

    def get_dimension(self) -> int:
        return self._dimension


class VolcengineSparseEmbedder(SparseEmbedderBase):
    """Volcengine Sparse Embedder Implementation

    Generates sparse embeddings using Volcengine's multimodal embedding API.
    """

    def __init__(
        self,
        model_name: str,
        api_key: Optional[str] = None,
        api_base: Optional[str] = None,
        input_type: str = "multimodal",
        extra_headers: Optional[Dict[str, str]] = None,
        config: Optional[Dict[str, Any]] = None,
    ):
        """Initialize Volcengine Sparse Embedder

        Args:
            model_name: Volcengine model name
            api_key: API key for authentication
            api_base: API base URL
            input_type: Input type - "text" or "multimodal" (default: "multimodal")
            config: Additional configuration dict

        Raises:
            ValueError: If api_key is not provided
        """
        super().__init__(model_name, config)
        self.provider = "volcengine"

        self.api_key = api_key
        self.api_base = api_base
        self.input_type = input_type
        self.extra_headers = _build_volcengine_headers(extra_headers)

        if not self.api_key:
            raise ValueError("api_key is required")

        ark_kwargs = {"api_key": self.api_key}
        if self.api_base:
            ark_kwargs["base_url"] = self.api_base
        self.client = volcenginesdkarkruntime.Ark(**ark_kwargs)
        self._ark_kwargs = ark_kwargs
        self._async_client_cache = LoopScopedAsyncClientCache()

    def _update_telemetry_token_usage(self, response, *, duration_seconds: float) -> None:
        usage = getattr(response, "usage", None)
        if not usage:
            return

        def _usage_value(key: str, default: int = 0) -> int:
            if isinstance(usage, dict):
                return int(usage.get(key, default) or default)
            return int(getattr(usage, key, default) or default)

        prompt_tokens = _usage_value("prompt_tokens", 0)
        total_tokens = _usage_value("total_tokens", prompt_tokens)
        completion_tokens = max(total_tokens - prompt_tokens, 0)

        # Update telemetry
        get_current_telemetry().add_token_usage_by_source(
            "embedding",
            prompt_tokens,
            completion_tokens,
        )

        # Update token tracker
        self.update_token_usage(
            model_name=self.model_name,
            provider="volcengine",
            prompt_tokens=prompt_tokens,
            completion_tokens=completion_tokens,
            duration_seconds=duration_seconds,
        )

    def embed(self, content: "EmbeddingInput", is_query: bool = False) -> EmbedResult:
        """Perform sparse embedding on text or multimodal content

        Args:
            content: Input text, or a list of multimodal content parts such as
                ``[{"type": "text", "text": "..."}, {"type": "image_url", "image_url": {"url": "..."}}]``
            is_query: Flag to indicate if this is a query embedding

        Returns:
            EmbedResult: Result containing sparse_vector

        Raises:
            RuntimeError: When API call fails
        """

        def _embed_call():
            # Must use multimodal endpoint for sparse
            response, duration_seconds = _measure_embedding_call(
                self,
                lambda: self.client.multimodal_embeddings.create(
                    input=to_multimodal_input(content),
                    model=self.model_name,
                    sparse_embedding={"type": "enabled"},
                    extra_headers=self.extra_headers,
                ),
            )
            self._update_telemetry_token_usage(
                response, duration_seconds=duration_seconds
            )
            item = response.data
            sparse_vector = getattr(item, "sparse_embedding", None)
            return EmbedResult(sparse_vector=process_sparse_embedding(sparse_vector))

        try:
            return self._run_with_retry(
                _embed_call,
                logger=logger,
                operation_name="Volcengine sparse embedding",
            )
        except Exception as e:
            raise RuntimeError(f"Volcengine sparse embedding failed: {str(e)}") from e

    def _get_async_client(self):
        return self._async_client_cache.get(
            lambda: volcenginesdkarkruntime.AsyncArk(**self._ark_kwargs)
        )

    async def embed_async(self, content: "EmbeddingInput", is_query: bool = False) -> EmbedResult:
        client = self._get_async_client()

        async def _embed_call() -> EmbedResult:
            response, duration_seconds = await _measure_embedding_call_async(
                self,
                lambda: client.multimodal_embeddings.create(
                    input=to_multimodal_input(content),
                    model=self.model_name,
                    sparse_embedding={"type": "enabled"},
                    extra_headers=self.extra_headers,
                ),
            )
            self._update_telemetry_token_usage(
                response, duration_seconds=duration_seconds
            )
            item = response.data
            sparse_vector = getattr(item, "sparse_embedding", None)
            return EmbedResult(sparse_vector=process_sparse_embedding(sparse_vector))

        try:
            return await self._run_with_async_retry(
                _embed_call,
                logger=logger,
                operation_name="Volcengine async sparse embedding",
            )
        except Exception as e:
            raise RuntimeError(f"Volcengine sparse embedding failed: {str(e)}") from e

    @property
    def supports_multimodal(self) -> bool:
        """Sparse vectors require text-only input even on the multimodal endpoint.

        The provider accepts the request at the endpoint level, but rejects
        non-text parts (images and videos) whenever ``sparse_embedding`` is
        enabled. Returning ``False`` makes the common embedder guard retain the
        extracted text and drop those parts before the provider call. Image-only
        search remains unsupported and is rejected by the retrieval capability
        check instead of embedding empty text.
        """
        return False


class VolcengineHybridEmbedder(HybridEmbedderBase):
    """Volcengine Hybrid Embedder Implementation

    Generates both dense and sparse embeddings simultaneously using Volcengine's
    multimodal embedding API.
    """

    def __init__(
        self,
        model_name: str,
        api_key: Optional[str] = None,
        api_base: Optional[str] = None,
        dimension: Optional[int] = None,
        input_type: str = "multimodal",
        extra_headers: Optional[Dict[str, str]] = None,
        config: Optional[Dict[str, Any]] = None,
    ):
        """Initialize Volcengine Hybrid Embedder

        Args:
            model_name: Volcengine model name
            api_key: API key for authentication
            api_base: API base URL
            dimension: Target dimension for dense vector truncation (optional)
            input_type: Input type - "text" or "multimodal" (default: "multimodal")
            config: Additional configuration dict

        Raises:
            ValueError: If api_key is not provided
        """
        super().__init__(model_name, config)
        self.provider = "volcengine"
        self.api_key = api_key
        self.api_base = api_base
        self.dimension = dimension
        self.input_type = input_type
        self.extra_headers = _build_volcengine_headers(extra_headers)

        if not self.api_key:
            raise ValueError("api_key is required")

        ark_kwargs = {"api_key": self.api_key}
        if self.api_base:
            ark_kwargs["base_url"] = self.api_base
        self.client = volcenginesdkarkruntime.Ark(**ark_kwargs)
        self._ark_kwargs = ark_kwargs
        self._async_client_cache = LoopScopedAsyncClientCache()
        self._dimension = dimension or 2048

    def _update_telemetry_token_usage(self, response, *, duration_seconds: float) -> None:
        usage = getattr(response, "usage", None)
        if not usage:
            return

        def _usage_value(key: str, default: int = 0) -> int:
            if isinstance(usage, dict):
                return int(usage.get(key, default) or default)
            return int(getattr(usage, key, default) or default)

        prompt_tokens = _usage_value("prompt_tokens", 0)
        total_tokens = _usage_value("total_tokens", prompt_tokens)
        completion_tokens = max(total_tokens - prompt_tokens, 0)

        # Update telemetry
        get_current_telemetry().add_token_usage_by_source(
            "embedding",
            prompt_tokens,
            completion_tokens,
        )

        # Update token tracker
        self.update_token_usage(
            model_name=self.model_name,
            provider="volcengine",
            prompt_tokens=prompt_tokens,
            completion_tokens=completion_tokens,
            duration_seconds=duration_seconds,
        )

    @property
    def supports_multimodal(self) -> bool:
        """Hybrid vectors include sparse output and therefore require text input.

        A hybrid provider request enables sparse embedding, for which image and
        video parts are unsupported. The base guard performs a deterministic
        text-only downgrade for mixed content. Image-only search remains
        unsupported because dropping its only input would produce an empty query.
        """
        return False

    def embed(self, content: "EmbeddingInput", is_query: bool = False) -> EmbedResult:
        """Perform hybrid embedding on text or multimodal content

        Args:
            content: Input text, or a list of multimodal content parts such as
                ``[{"type": "text", "text": "..."}, {"type": "image_url", "image_url": {"url": "..."}}]``
            is_query: Flag to indicate if this is a query embedding

        Returns:
            EmbedResult: Result containing both dense_vector and sparse_vector

        Raises:
            RuntimeError: When API call fails
        """

        def _embed_call():
            # Always use multimodal for hybrid to get both

            response, duration_seconds = _measure_embedding_call(
                self,
                lambda: self.client.multimodal_embeddings.create(
                    input=to_multimodal_input(content),
                    model=self.model_name,
                    sparse_embedding={"type": "enabled"},
                    extra_headers=self.extra_headers,
                ),
            )
            self._update_telemetry_token_usage(
                response, duration_seconds=duration_seconds
            )
            item = response.data
            dense_vector = truncate_and_normalize(item.embedding, self.dimension)
            sparse_vector = getattr(item, "sparse_embedding", None)

            return EmbedResult(
                dense_vector=dense_vector, sparse_vector=process_sparse_embedding(sparse_vector)
            )

        try:
            return self._run_with_retry(
                _embed_call,
                logger=logger,
                operation_name="Volcengine hybrid embedding",
            )
        except Exception as e:
            raise RuntimeError(f"Volcengine hybrid embedding failed: {str(e)}") from e

    def _get_async_client(self):
        return self._async_client_cache.get(
            lambda: volcenginesdkarkruntime.AsyncArk(**self._ark_kwargs)
        )

    async def embed_async(self, content: "EmbeddingInput", is_query: bool = False) -> EmbedResult:
        client = self._get_async_client()

        async def _embed_call() -> EmbedResult:
            response, duration_seconds = await _measure_embedding_call_async(
                self,
                lambda: client.multimodal_embeddings.create(
                    input=to_multimodal_input(content),
                    model=self.model_name,
                    sparse_embedding={"type": "enabled"},
                    extra_headers=self.extra_headers,
                ),
            )
            self._update_telemetry_token_usage(
                response, duration_seconds=duration_seconds
            )
            item = response.data
            dense_vector = truncate_and_normalize(item.embedding, self.dimension)
            sparse_vector = getattr(item, "sparse_embedding", None)
            return EmbedResult(
                dense_vector=dense_vector,
                sparse_vector=process_sparse_embedding(sparse_vector),
            )

        try:
            return await self._run_with_async_retry(
                _embed_call,
                logger=logger,
                operation_name="Volcengine async hybrid embedding",
            )
        except Exception as e:
            raise RuntimeError(f"Volcengine hybrid embedding failed: {str(e)}") from e

    def get_dimension(self) -> int:
        return self._dimension
