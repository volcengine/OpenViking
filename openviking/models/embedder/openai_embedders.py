# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: Apache-2.0
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
        >>> query_embedder = OpenAIDenseEmbedder(
        ...     model_name="bge-m3",
        ...     api_key="your-api-key",
        ...     api_base="https://your-api-endpoint.com/v1",
        ...     context="query",
        ...     query_param="query",
        ...     document_param="passage"
        ... )
        >>> query_vector = query_embedder.embed("search query")
    """

    def __init__(
        self,
        model_name: str = "text-embedding-3-small",
        api_key: Optional[str] = None,
        api_base: Optional[str] = None,
        dimension: Optional[int] = None,
        query_param: Optional[str] = None,
        document_param: Optional[str] = None,
        config: Optional[Dict[str, Any]] = None,
        max_tokens: Optional[int] = None,
        extra_headers: Optional[Dict[str, str]] = None,
    ):
        """Initialize OpenAI-Compatible Dense Embedder

        Args:
            model_name: Model name. For official OpenAI models (e.g., text-embedding-3-small),
                       use symmetric mode (query_param=None, document_param=None).
                       For OpenAI-compatible third-party models (e.g., BGE-M3, Jina, Cohere), use
                       non-symmetric mode with query_param/document_param.
            api_key: API key, if None will read from env vars (OPENVIKING_EMBEDDING_API_KEY or OPENAI_API_KEY)
            api_base: API base URL, optional. Required for third-party OpenAI-compatible APIs.
            dimension: Dimension (if model supports), optional
            query_param: The input_type value for query-side embeddings, e.g. 'query' or
                         'search_query'. Defaults to None.
                         Setting this (or document_param) activates non-symmetric mode.
                         Only supported by OpenAI-compatible third-party models.
            document_param: The input_type value for document-side embeddings, e.g. 'passage'
                            or 'document'. Defaults to None. Setting this (or query_param)
                            activates non-symmetric mode.
                            Only supported by OpenAI-compatible third-party models.
            config: Additional configuration dict
            max_tokens: Maximum token count per embedding request, None to use default (8000)
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
        super().__init__(model_name, config, max_tokens=max_tokens)

        self.api_key = api_key
        self.api_base = api_base
        self.dimension = dimension
        self.query_param = query_param
        self.document_param = document_param

        # Allow missing api_key when api_base is set (e.g. local OpenAI-compatible servers)
        if not self.api_key and not self.api_base:
            raise ValueError("api_key is required (or set api_base for local servers)")

        # Initialize OpenAI client
        # Use a placeholder api_key when not provided (for local OpenAI-compatible servers)
        client_kwargs = {"api_key": self.api_key or "no-key"}
        if self.api_base:
            client_kwargs["base_url"] = self.api_base
        # 透传自定义请求头（如 OpenRouter 要求的 HTTP-Referer / X-Title）
        if extra_headers:
            client_kwargs["default_headers"] = extra_headers
        self.client = openai.OpenAI(**client_kwargs)

        # Initialize tiktoken encoder
        self._tiktoken_enc = None
        try:
            import tiktoken

            self._tiktoken_enc = tiktoken.encoding_for_model(model_name)
        except Exception:
            logger.info(
                "tiktoken unavailable for model '%s', will use character-based estimation",
                model_name,
            )

        # Auto-detect dimension
        self._dimension = dimension
        if self._dimension is None:
            self._dimension = self._detect_dimension()

    @property
    def max_tokens(self) -> int:
        """OpenAI embedding models have 8192 token limit; use 8000 for safety buffer.

        Can be overridden via the max_tokens constructor parameter.
        """
        if self._max_tokens is not None:
            return self._max_tokens
        return 8000

    def _estimate_tokens(self, text: str) -> int:
        """Estimate tokens using tiktoken if available, fallback to len(text) // 3."""
        if self._tiktoken_enc is not None:
            return len(self._tiktoken_enc.encode(text))
        return len(text) // 3

    def _detect_dimension(self) -> int:
        """Detect dimension by making an actual API call"""
        try:
            result = self._embed_single("test", is_query=False)
            return len(result.dense_vector) if result.dense_vector else 1536
        except Exception:
            # Use default value, text-embedding-3-small defaults to 1536
            return 1536

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
        output_tokens = max(total_tokens - prompt_tokens, 0)
        get_current_telemetry().add_token_usage_by_source(
            "embedding",
            prompt_tokens,
            output_tokens,
        )

    def _build_extra_body(self, is_query: bool = False) -> Optional[Dict[str, Any]]:
        """Build extra_body dict for OpenAI-compatible parameters

        Returns:
            Dict containing input_type if non-symmetric mode is active.
            Only supported by OpenAI-compatible third-party models.
        """
        extra_body = {}
        input_type = None
        if is_query and self.query_param is not None:
            input_type = self.query_param
        elif not is_query and self.document_param is not None:
            input_type = self.document_param

        if input_type is not None:
            extra_body["input_type"] = input_type
        return extra_body if extra_body else None

    def _embed_single(self, text: str, is_query: bool = False) -> EmbedResult:
        """Perform raw embedding without chunking logic.

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

            extra_body = self._build_extra_body(is_query=is_query)
            if extra_body:
                kwargs["extra_body"] = extra_body

            response = self.client.embeddings.create(**kwargs)
            self._update_telemetry_token_usage(response)
            vector = response.data[0].embedding

            return EmbedResult(dense_vector=vector)
        except openai.APIError as e:
            raise RuntimeError(f"OpenAI API error: {e.message}") from e
        except Exception as e:
            raise RuntimeError(f"Embedding failed: {str(e)}") from e

    def embed(self, text: str, is_query: bool = False) -> EmbedResult:
        """Embed single text, with automatic chunking for oversized input.

        Args:
            text: Input text
            is_query: Flag to indicate if this is a query embedding

        Returns:
            EmbedResult: Result containing only dense_vector

        Raises:
            RuntimeError: When API call fails
        """
        if not text:
            return self._embed_single(text, is_query=is_query)

        if self._estimate_tokens(text) > self.max_tokens:
            return self._chunk_and_embed(text, is_query=is_query)
        return self._embed_single(text, is_query=is_query)

    def embed_batch(self, texts: List[str], is_query: bool = False) -> List[EmbedResult]:
        """Batch embedding with automatic chunking for oversized inputs.

        Short texts are batched together via the OpenAI API for efficiency.
        Oversized texts are individually chunked and embedded.

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

        results: List[Optional[EmbedResult]] = [None] * len(texts)
        short_indices: List[int] = []
        short_texts: List[str] = []

        for i, text in enumerate(texts):
            if text and self._estimate_tokens(text) > self.max_tokens:
                results[i] = self._chunk_and_embed(text, is_query=is_query)
            else:
                short_indices.append(i)
                short_texts.append(text)

        if short_texts:
            try:
                kwargs: Dict[str, Any] = {"input": short_texts, "model": self.model_name}

                extra_body = self._build_extra_body(is_query=is_query)
                if extra_body:
                    kwargs["extra_body"] = extra_body

                response = self.client.embeddings.create(**kwargs)
                self._update_telemetry_token_usage(response)
                for idx, item in zip(short_indices, response.data):
                    results[idx] = EmbedResult(dense_vector=item.embedding)
            except openai.APIError as e:
                raise RuntimeError(f"OpenAI API error: {e.message}") from e
            except Exception as e:
                raise RuntimeError(f"Batch embedding failed: {str(e)}") from e

        return results  # type: ignore[return-value]

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
