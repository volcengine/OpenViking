# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: Apache-2.0
"""Google/Gemini AI Embedder Implementation"""

import logging
from typing import Any, Dict, List, Optional

import requests

from openviking.models.embedder.base import (
    DenseEmbedderBase,
    EmbedResult,
)

logger = logging.getLogger(__name__)

# Default dimensions for Google/Gemini embedding models
GOOGLE_MODEL_DIMENSIONS = {
    "gemini-embedding-2-preview": 3072,  # Gemini Embedding 2 with MRL support
}


class GoogleDenseEmbedder(DenseEmbedderBase):
    """Google Gemini Embedding 2 Dense Embedder Implementation

    Uses native Google Gemini embedding API with Parts format.
    Supports Gemini Embedding 2 (gemini-embedding-2-preview) only.
    Supports task-specific embeddings and Matryoshka dimension reduction.

    Example:
        >>> # Simple usage with query/document task types
        >>> embedder = GoogleDenseEmbedder(
        ...     api_key="your-gemini-api-key",
        ...     dimension=1024,
        ...     query_param="RETRIEVAL_QUERY",
        ...     document_param="RETRIEVAL_DOCUMENT"
        ... )
        >>> query_result = embedder.embed("Search query", is_query=True)
        >>> doc_result = embedder.embed("Document content", is_query=False)

        >>> # Enhanced usage with key=value format
        >>> advanced_embedder = GoogleDenseEmbedder(
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
        """Initialize Google Gemini Embedding 2 Dense Embedder

        Args:
            model_name: Must be "gemini-embedding-2-preview" (default and only supported model)
            api_key: Google API key, required
            api_base: API base URL, defaults to https://generativelanguage.googleapis.com/v1beta
            dimension: Dimension for Matryoshka reduction, optional (max 3072)
            query_param: Parameter for query-side embeddings. Supports simple task_type
                        values (e.g., "RETRIEVAL_QUERY") or key=value format
                        (e.g., "task_type=RETRIEVAL_QUERY,output_dimensionality=1024").
                        Valid task_type values: RETRIEVAL_QUERY, RETRIEVAL_DOCUMENT,
                        SEMANTIC_SIMILARITY, CLASSIFICATION, CLUSTERING
            document_param: Parameter for document-side embeddings. Supports simple task_type
                           values or key=value format.
            config: Additional configuration dict
            max_tokens: Maximum token count per embedding request, None to use default (8192)
            extra_headers: Extra HTTP headers to include in API requests

        Raises:
            ValueError: If api_key is not provided or unsupported model is specified
        """
        super().__init__(model_name, config)
        self.api_key = api_key
        self.api_base = api_base or "https://generativelanguage.googleapis.com/v1beta"
        self.dimension = dimension
        self.query_param = query_param
        self.document_param = document_param
        self._max_tokens = max_tokens or 8192
        self.extra_headers = extra_headers or {}

        if not self.api_key:
            raise ValueError("api_key is required")

        # Determine dimension - only support gemini-embedding-2-preview
        if model_name not in GOOGLE_MODEL_DIMENSIONS:
            raise ValueError(
                f"Unsupported model '{model_name}'. Only 'gemini-embedding-2-preview' is supported."
            )

        max_dim = GOOGLE_MODEL_DIMENSIONS[model_name]
        if dimension is not None and dimension > max_dim:
            raise ValueError(
                f"Requested dimension {dimension} exceeds maximum {max_dim} for model '{model_name}'. "
                f"Gemini Embedding 2 supports Matryoshka dimension reduction up to {max_dim}."
            )
        self._dimension = dimension if dimension is not None else max_dim

    def _parse_param_string(self, param: Optional[str]) -> Dict[str, Any]:
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

        # Map snake_case keys to camelCase as required by Google API
        key_map = {"task_type": "taskType"}

        for part in parts:
            if "=" in part:
                key, value = part.split("=", 1)
                key = key.strip()
                value = value.strip()
                key = key_map.get(key, key)

                # Convert numeric values and uppercase task type
                if key == "output_dimensionality" and value.isdigit():
                    result[key] = int(value)
                elif key == "taskType":
                    result[key] = value.upper()
                else:
                    result[key] = value

        return result

    def _build_request_params(self, is_query: bool = False) -> Dict[str, Any]:
        """Build request parameters for Google-specific settings

        Args:
            is_query: Flag to indicate if this is for query embeddings

        Returns:
            Dict containing Google-specific parameters
        """
        params = {}

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
                params.update(parsed)
            else:
                # Simple format (e.g., "retrieval_query" -> {"taskType": "RETRIEVAL_QUERY"})
                params["taskType"] = active_param.upper()

        # Add dimension if specified
        if self.dimension:
            params["output_dimensionality"] = self.dimension

        return params

    def _update_telemetry_token_usage(self, response_data: Dict[str, Any]) -> None:
        """Update telemetry with token usage from API response"""
        # Google API doesn't return token usage in the same format as OpenAI
        # We'll estimate based on text length for now
        pass

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
            # Build the URL for the embedding endpoint
            url = f"{self.api_base}/models/{self.model_name}:embedContent"

            # Build request headers
            headers = {
                "Content-Type": "application/json",
                "x-goog-api-key": self.api_key,
                **self.extra_headers,
            }

            # Build request body using Parts API
            request_body = {"content": {"parts": [{"text": text}]}}

            # Add task-specific parameters
            request_params = self._build_request_params(is_query=is_query)
            if request_params:
                request_body.update(request_params)

            # Make the API request
            response = requests.post(url, json=request_body, headers=headers, timeout=30)
            response.raise_for_status()

            response_data = response.json()

            # Extract the embedding vector
            if "embedding" in response_data and "values" in response_data["embedding"]:
                vector = response_data["embedding"]["values"]
            else:
                raise RuntimeError(f"Unexpected response format: {response_data}")

            self._update_telemetry_token_usage(response_data)

            return EmbedResult(dense_vector=vector)

        except requests.exceptions.RequestException as e:
            raise RuntimeError(f"Google/Gemini API request error: {str(e)}") from e
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
        if not text or not text.strip():
            return EmbedResult()

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

        Individual texts are processed sequentially since Google's native API
        doesn't support batch requests in the same way as OpenAI-compatible.
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

        results: List[EmbedResult] = []

        # Process each text individually
        for text in texts:
            if not text or not text.strip():
                results.append(EmbedResult())
                continue
            if self._estimate_tokens(text) <= self.max_tokens:
                result = self._embed_single(text, is_query=is_query)
            else:
                # Handle oversized text with chunking
                result = self._chunk_and_embed(text, is_query=is_query)

            results.append(result)

        return results

    def get_dimension(self) -> int:
        """Get embedding dimension

        Returns:
            int: Vector dimension
        """
        return self._dimension
