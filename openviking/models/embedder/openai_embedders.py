# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0
"""OpenAI Embedder Implementation"""

import logging
from typing import Any, Dict, List, Optional

import openai

from openviking.models.embedder.base import (
    DenseEmbedderBase,
    EmbedResult,
    HybridEmbedderBase,
    SparseEmbedderBase,
)
from openviking.models.vlm.registry import DEFAULT_AZURE_API_VERSION
from openviking.telemetry import get_current_telemetry

logger = logging.getLogger(__name__)


class OpenAIDenseEmbedder(DenseEmbedderBase):
    """OpenAI-Compatible Dense Embedder Implementation

    Supports OpenAI embedding models (e.g., text-embedding-3-small, text-embedding-3-large)
    and OpenAI-compatible third-party models that support non-symmetric embeddings.

    Note: Official OpenAI models are symmetric and do not support the input_type parameter.
    Non-symmetric mode (context='query'/'document') is only supported by OpenAI-compatible
    third-party models (e.g., BGE-M3, Jina, Cohere, etc.) that implement the input_type parameter.

    Example:
        >>> # Symmetric mode (official OpenAI models)
        >>> embedder = OpenAIDenseEmbedder(
        ...     model_name="text-embedding-3-small",
        ...     api_key="sk-xxx",
        ...     dimension=1536
        ... )
        >>> result = embedder.embed("Hello world")
        >>> print(len(result.dense_vector))
        1536

        >>> # Non-symmetric mode (OpenAI-compatible third-party models)
        >>> embedder = OpenAIDenseEmbedder(
        ...     model_name="bge-m3",
        ...     api_key="your-api-key",
        ...     api_base="https://your-api-endpoint.com/v1",
        ...     query_param="query",
        ...     document_param="passage"
        ... )
        >>> query_vector = embedder.embed("search query", is_query=True)
        >>> doc_vector = embedder.embed("document text", is_query=False)

        >>> # Multiple parameters with key=value format
        >>> advanced_embedder = OpenAIDenseEmbedder(
        ...     model_name="custom-model",
        ...     api_key="your-api-key",
        ...     api_base="https://your-api-endpoint.com/v1",
        ...     query_param="input_type=query,task=search,domain=finance",
        ...     document_param="input_type=passage,task=index,domain=finance"
        ... )
        >>> advanced_vector = advanced_embedder.embed("financial query", is_query=True)
    """

    def __init__(
        self,
        model_name: str = "text-embedding-3-small",
        api_key: Optional[str] = None,
        api_base: Optional[str] = None,
        api_version: Optional[str] = None,
        dimension: Optional[int] = None,
        query_param: Optional[str] = None,
        document_param: Optional[str] = None,
        config: Optional[Dict[str, Any]] = None,
        extra_headers: Optional[Dict[str, str]] = None,
        input_type: Optional[str] = None,
        provider: str = "openai",
    ):
        """Initialize OpenAI-Compatible Dense Embedder

        Args:
            model_name: Model name. For official OpenAI models (e.g., text-embedding-3-small),
                       use symmetric mode (query_param=None, document_param=None).
                       For OpenAI-compatible third-party models (e.g., BGE-M3, Jina, Cohere), use
                       non-symmetric mode with query_param/document_param.
            api_key: API key, if None will read from env vars (OPENVIKING_EMBEDDING_API_KEY or OPENAI_API_KEY)
            api_base: API base URL, optional. Required for third-party OpenAI-compatible APIs.
            dimension: Target dimension for output vectors. If specified and the model returns vectors
                      with a different dimension, the output will be truncated to this dimension.
                      If None, uses the model's default dimension without truncation.
            query_param: Parameter for query-side embeddings. Supports simple values (e.g., 'query')
                         or key=value format (e.g., 'input_type=query,task=search'). Defaults to None.
                         Setting this (or document_param) activates non-symmetric mode.
                         Only supported by OpenAI-compatible third-party models.
            document_param: Parameter for document-side embeddings. Supports simple values (e.g., 'passage')
                           or key=value format (e.g., 'input_type=passage,task=index'). Defaults to None.
                           Setting this (or query_param) activates non-symmetric mode.
                           Only supported by OpenAI-compatible third-party models.
            config: Additional configuration dict
            extra_headers: Extra HTTP headers to include in API requests (e.g., for OpenRouter:
                          {'HTTP-Referer': 'https://your-site.com', 'X-Title': 'Your App'})

        Raises:
            ValueError: If api_key is not provided and env vars are not set

        Note:
            Official OpenAI models (e.g., text-embedding-3-small, text-embedding-3-large) are
            symmetric and do not support the input_type parameter. Non-symmetric mode is only
            supported by OpenAI-compatible third-party models (e.g., BGE-M3, Jina, Cohere) that
            implement the input_type parameter.
        """
        super().__init__(model_name, config)

        self.api_key = api_key
        self.api_base = api_base
        self.api_version = api_version
        self.dimension = dimension
        self.query_param = query_param
        self.document_param = document_param
        self._provider = provider.lower()

        # Allow missing api_key when api_base is set (e.g. local OpenAI-compatible servers)
        if not self.api_key and not self.api_base:
            raise ValueError("api_key is required")

        client_kwargs: Dict[str, Any] = {"api_key": self.api_key or "no-key"}
        if self._provider == "azure":
            if not self.api_base:
                raise ValueError("api_base (Azure endpoint) is required for Azure provider")
            client_kwargs["azure_endpoint"] = self.api_base
            client_kwargs["api_version"] = self.api_version or DEFAULT_AZURE_API_VERSION
            if extra_headers:
                client_kwargs["default_headers"] = extra_headers
            self.client = openai.AzureOpenAI(**client_kwargs)
        else:
            if self.api_base:
                client_kwargs["base_url"] = self.api_base
            if extra_headers:
                client_kwargs["default_headers"] = extra_headers
            self.client = openai.OpenAI(**client_kwargs)

        # Auto-detect dimension
        self._dimension = dimension
        self._actual_model_dimension = None
        if self._dimension is None:
            self._dimension = self._detect_dimension()

    def _detect_dimension(self) -> int:
        """Detect dimension by making an actual API call"""
        try:
            result = self.embed("test")
            detected_dim = len(result.dense_vector) if result.dense_vector else 1536
            self._actual_model_dimension = detected_dim
            return detected_dim
        except Exception:
            # Use default value, text-embedding-3-small defaults to 1536
            return 1536

    def _truncate_vector(self, vector: List[float]) -> List[float]:
        """Truncate vector to target dimension if needed.

        Args:
            vector: Input vector from API

        Returns:
            Truncated vector if dimension is set and smaller than input, otherwise original vector
        """
        if self.dimension is not None and len(vector) > self.dimension:
            return vector[: self.dimension]
        return vector

    def _update_telemetry_token_usage(self, response) -> None:
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
            provider=self._provider,
            prompt_tokens=prompt_tokens,
            completion_tokens=completion_tokens,
        )

    def _parse_param_string(self, param: Optional[str]) -> Dict[str, str]:
        """Parse parameter string to dictionary for key=value format

        Args:
            param: Parameter string (e.g., "input_type=query,task=search")

        Returns:
            Dictionary of parsed parameters
        """
        if not param:
            return {}

        result = {}

        # Split by comma for multiple parameters
        parts = [p.strip() for p in param.split(",")]

        for part in parts:
            if "=" in part:
                key, value = part.split("=", 1)
                result[key.strip()] = value.strip()

        return result

    def _build_extra_body(self, is_query: bool = False) -> Optional[Dict[str, Any]]:
        """Build extra_body dict for OpenAI-compatible parameters

        Args:
            is_query: Flag to indicate if this is for query embeddings

        Returns:
            Dict containing input_type and other parameters if non-symmetric mode is active.
            Supports key=value format for multiple parameters (e.g., "input_type=query,task=search").
            Only supported by OpenAI-compatible third-party models.
        """
        extra_body = {}

        # Determine which parameter to use based on is_query flag
        active_param = None
        if is_query and self.query_param is not None:
            active_param = self.query_param
        elif not is_query and self.document_param is not None:
            active_param = self.document_param

        if active_param:
            if "=" in active_param:
                # Parse key=value format (e.g., "input_type=query,task=search")
                parsed = self._parse_param_string(active_param)
                extra_body.update(parsed)
            else:
                # Simple format (e.g., "query" -> {"input_type": "query"})
                extra_body["input_type"] = active_param

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

        def _call() -> EmbedResult:
            kwargs: Dict[str, Any] = {"input": text, "model": self.model_name}
            # Don't pass dimensions parameter to API - some OpenAI-compatible models don't support it
            # Instead, we'll truncate the result vector if needed

            extra_body = self._build_extra_body(is_query=is_query)
            if extra_body:
                kwargs["extra_body"] = extra_body

            response = self.client.embeddings.create(**kwargs)
            self._update_telemetry_token_usage(response)
            vector = response.data[0].embedding

            # Truncate vector if needed
            vector = self._truncate_vector(vector)

            return EmbedResult(dense_vector=vector)

        try:
            return self._run_with_retry(
                _call,
                logger=logger,
                operation_name="OpenAI embedding",
            )
        except openai.APIError as e:
            raise RuntimeError(f"OpenAI API error: {e.message}") from e
        except Exception as e:
            raise RuntimeError(f"Embedding failed: {str(e)}") from e

    def embed_batch(self, texts: List[str], is_query: bool = False) -> List[EmbedResult]:
        """Batch embedding (OpenAI native support)

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

        def _call() -> List[EmbedResult]:
            kwargs: Dict[str, Any] = {"input": texts, "model": self.model_name}
            # Don't pass dimensions parameter to API - some OpenAI-compatible models don't support it
            # Instead, we'll truncate the result vectors if needed

            extra_body = self._build_extra_body(is_query=is_query)
            if extra_body:
                kwargs["extra_body"] = extra_body

            response = self.client.embeddings.create(**kwargs)
            self._update_telemetry_token_usage(response)

            # Truncate vectors if needed
            return [
                EmbedResult(dense_vector=self._truncate_vector(item.embedding))
                for item in response.data
            ]

        try:
            return self._run_with_retry(
                _call,
                logger=logger,
                operation_name="OpenAI batch embedding",
            )
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

    def embed(self, text: str, is_query: bool = False) -> EmbedResult:
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

    def embed(self, text: str, is_query: bool = False) -> EmbedResult:
        raise NotImplementedError()

    def get_dimension(self) -> int:
        raise NotImplementedError()
