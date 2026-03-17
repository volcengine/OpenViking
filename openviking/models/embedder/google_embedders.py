# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: Apache-2.0
"""Google/Gemini AI Embedder Implementation"""

import logging
from typing import Any, Dict, List, Optional

import openai

from openviking.models.embedder.base import (
    DenseEmbedderBase,
    EmbedResult,
)
from openviking.telemetry import get_current_telemetry

logger = logging.getLogger(__name__)

# Default dimensions for Google/Gemini embedding models
GOOGLE_MODEL_DIMENSIONS = {
    "gemini-embedding-2-preview": 3072,  # Gemini Embedding 2 with MRL support
    "gemini-embedding-001": 768,  # Legacy text-only model
}


class GoogleDenseEmbedder(DenseEmbedderBase):
    """Google/Gemini AI Dense Embedder Implementation

    Uses Google/Gemini embedding API via OpenAI-compatible client.
    Supports task-specific embeddings and Matryoshka dimension reduction.

    Supports both simple task_type values and key=value format for multiple parameters.

    Example:
        >>> # Simple usage with query/document task types
        >>> embedder = GoogleDenseEmbedder(
        ...     model_name="gemini-embedding-2-preview",
        ...     api_key="your-gemini-api-key",
        ...     dimension=1024,
        ...     query_param="RETRIEVAL_QUERY",
        ...     document_param="RETRIEVAL_DOCUMENT"
        ... )
        >>> query_result = embedder.embed("Search query", is_query=True)
        >>> doc_result = embedder.embed("Document content", is_query=False)
        
        >>> # Enhanced usage with key=value format
        >>> advanced_embedder = GoogleDenseEmbedder(
        ...     model_name="gemini-embedding-2-preview",
        ...     api_key="your-gemini-api-key",
        ...     dimension=1024,
        ...     query_param="task_type=RETRIEVAL_QUERY,output_dimensionality=1024",
        ...     document_param="task_type=RETRIEVAL_DOCUMENT,output_dimensionality=1024"
        ... )
    """

    def __init__(
        self,
        model_name: str = "gemini-embedding-2-preview",
        api_key: Optional[str] = None,
        api_base: Optional[str] = None,
        dimension: Optional[int] = None,
        query_param: Optional[str] = None,
        document_param: Optional[str] = None,
        config: Optional[Dict[str, Any]] = None,
        max_tokens: Optional[int] = None,
        extra_headers: Optional[Dict[str, str]] = None,
    ):
        """Initialize Google/Gemini AI Dense Embedder

        Args:
            model_name: Google/Gemini model name, defaults to gemini-embedding-2-preview
            api_key: API key, required
            api_base: API base URL, defaults to https://generativelanguage.googleapis.com/v1beta/openai/
            dimension: Dimension for Matryoshka reduction, optional
            query_param: Parameter for query-side embeddings. Supports simple task_type
                        values (e.g., "RETRIEVAL_QUERY") or key=value format
                        (e.g., "task_type=RETRIEVAL_QUERY,output_dimensionality=1024").
                        Valid task_type values: RETRIEVAL_QUERY, RETRIEVAL_DOCUMENT,
                        SEMANTIC_SIMILARITY, CLASSIFICATION, CLUSTERING
            document_param: Parameter for document-side embeddings. Supports simple task_type
                           values or key=value format.
            config: Additional configuration dict
            max_tokens: Maximum token count per embedding request, None to use default (8000)
            extra_headers: Extra HTTP headers to include in API requests

        Raises:
            ValueError: If api_key is not provided
        """
        super().__init__(model_name, config)

        self.api_key = api_key
        self.api_base = api_base or "https://generativelanguage.googleapis.com/v1beta/openai/"
        self.dimension = dimension
        self.query_param = query_param
        self.document_param = document_param
        self.max_tokens = max_tokens or 8000
        self.extra_headers = extra_headers

        if not self.api_key:
            raise ValueError("api_key is required")

        # Initialize OpenAI-compatible client with Google base URL
        client_kwargs: Dict[str, Any] = {
            "api_key": self.api_key,
            "base_url": self.api_base,
        }
        if self.extra_headers:
            client_kwargs["default_headers"] = self.extra_headers
            
        self.client = openai.OpenAI(**client_kwargs)

        # Determine dimension
        max_dim = GOOGLE_MODEL_DIMENSIONS.get(model_name, 3072)
        if dimension is not None and dimension > max_dim:
            raise ValueError(
                f"Requested dimension {dimension} exceeds maximum {max_dim} for model '{model_name}'. "
                f"Google/Gemini models support Matryoshka dimension reduction up to {max_dim}."
            )
        self._dimension = dimension if dimension is not None else max_dim

    def _parse_param_string(self, param: Optional[str]) -> Dict[str, str]:
        """Parse parameter string to dictionary for key=value format
        
        Args:
            param: Parameter string (e.g., "task_type=RETRIEVAL_QUERY,output_dimensionality=1024")
            
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
        """Build extra_body dict for Google-specific parameters
        
        Args:
            is_query: Flag to indicate if this is for query embeddings
            
        Returns:
            Dict containing Google-specific parameters if configured
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
                # Parse key=value format (e.g., "task_type=RETRIEVAL_QUERY,output_dimensionality=1024")
                parsed = self._parse_param_string(active_param)
                extra_body.update(parsed)
            else:
                # Simple format (e.g., "RETRIEVAL_QUERY" -> {"task_type": "RETRIEVAL_QUERY"})
                extra_body["task_type"] = active_param
                
        return extra_body if extra_body else None

    def _update_telemetry_token_usage(self, response: Any) -> None:
        """Update telemetry with token usage from API response"""
        if hasattr(response, "usage"):
            usage = response.usage
            prompt_tokens = usage.prompt_tokens if hasattr(usage, "prompt_tokens") else 0
            output_tokens = usage.total_tokens - prompt_tokens if hasattr(usage, "total_tokens") else 0
            
            get_current_telemetry().add_token_usage_by_source(
                "embedding",
                prompt_tokens,
                output_tokens,
            )

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
            if self.dimension:
                kwargs["dimensions"] = self.dimension

            extra_body = self._build_extra_body(is_query=is_query)
            if extra_body:
                kwargs["extra_body"] = extra_body

            response = self.client.embeddings.create(**kwargs)
            self._update_telemetry_token_usage(response)
            vector = response.data[0].embedding

            return EmbedResult(dense_vector=vector)
        except openai.APIError as e:
            raise RuntimeError(f"Google/Gemini API error: {e.message}") from e
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

    def _chunk_and_embed(self, text: str, is_query: bool = False) -> EmbedResult:
        """Chunk oversized text and average the embeddings.

        Args:
            text: Oversized input text
            is_query: Flag to indicate if this is a query embedding

        Returns:
            EmbedResult: Result containing only dense_vector (averaged from chunks)
        """
        chunks = self._chunk_text(text, self.max_tokens)
        chunk_vectors: List[List[float]] = []
        
        for chunk in chunks:
            result = self._embed_single(chunk, is_query=is_query)
            chunk_vectors.append(result.dense_vector)
        
        # Average the chunk vectors
        if not chunk_vectors:
            return EmbedResult(dense_vector=[0.0] * self._dimension)
            
        avg_vector = [
            sum(v[i] for v in chunk_vectors) / len(chunk_vectors)
            for i in range(len(chunk_vectors[0]))
        ]
        
        return EmbedResult(dense_vector=avg_vector)

    def embed_batch(self, texts: List[str], is_query: bool = False) -> List[EmbedResult]:
        """Batch embedding with automatic chunking for oversized inputs.

        Short texts are batched together via the Google API for efficiency.
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

        # Separate short and long texts
        for idx, text in enumerate(texts):
            if not text or self._estimate_tokens(text) <= self.max_tokens:
                short_indices.append(idx)
                short_texts.append(text if text else " ")
            else:
                # Handle oversized text individually
                results[idx] = self._chunk_and_embed(text, is_query=is_query)

        # Batch process short texts
        if short_texts:
            try:
                kwargs: Dict[str, Any] = {"input": short_texts, "model": self.model_name}
                if self.dimension:
                    kwargs["dimensions"] = self.dimension

                extra_body = self._build_extra_body(is_query=is_query)
                if extra_body:
                    kwargs["extra_body"] = extra_body

                response = self.client.embeddings.create(**kwargs)
                self._update_telemetry_token_usage(response)
                
                for idx, item in zip(short_indices, response.data):
                    results[idx] = EmbedResult(dense_vector=item.embedding)
            except openai.APIError as e:
                raise RuntimeError(f"Google/Gemini API error: {e.message}") from e
            except Exception as e:
                raise RuntimeError(f"Batch embedding failed: {str(e)}") from e

        return results  # type: ignore[return-value]

    def get_dimension(self) -> int:
        """Get embedding dimension

        Returns:
            int: Vector dimension
        """
        return self._dimension
