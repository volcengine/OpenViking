# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: Apache-2.0
"""Google/Gemini AI Embedder Implementation"""

import logging
import re
from typing import Any, Dict, List, Optional

import requests

from openviking.models.embedder.base import (
    DenseEmbedderBase,
    EmbedResult,
    exponential_backoff_retry,
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
    Supports Matryoshka dimension reduction via output_dimensionality.

    ## Note: taskType not supported by gemini-embedding-2-preview

    Tested 2026-03-19 against the live API at full 3072 dimensions:
    the taskType parameter is accepted without error but produces bit-for-bit
    identical vectors regardless of which task type is specified. All eight
    documented task types (RETRIEVAL_QUERY, RETRIEVAL_DOCUMENT,
    SEMANTIC_SIMILARITY, CLASSIFICATION, CLUSTERING, CODE_RETRIEVAL_QUERY,
    QUESTION_ANSWERING, FACT_VERIFICATION) return the same embedding as the
    default (no taskType). The parameter is therefore not sent.

    By contrast, gemini-embedding-001 does produce distinct vectors per task
    type. This is because taskType in Gemini embedding models is implemented as
    an instruction prefix injected into the embedding input — effectively
    "task: {task_type}, content: {text}" — rather than a separate model head or
    fine-tuned adapter. gemini-embedding-2-preview appears to have dropped this
    instruction-following behaviour.

    Example:
        >>> embedder = GoogleDenseEmbedder(
        ...     api_key="your-gemini-api-key",
        ...     dimension=1024,
        ... )
        >>> result = embedder.embed("Hello world")
        >>> print(len(result.dense_vector))
        1024
    """

    def __init__(
        self,
        model_name: str = "gemini-embedding-2-preview",
        api_key: Optional[str] = None,
        api_base: Optional[str] = None,
        dimension: Optional[int] = None,
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
        self._max_tokens = max_tokens or 8192
        self.extra_headers = extra_headers or {}

        if not self.api_key:
            raise ValueError("api_key is required")

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

    @property
    def max_tokens(self) -> int:
        """Maximum token count per embedding request."""
        return self._max_tokens

    def _estimate_tokens(self, text: str) -> int:
        """Estimate token count. Falls back to character-based heuristic if tiktoken unavailable."""
        try:
            import tiktoken

            enc = tiktoken.encoding_for_model(self.model_name)
            return len(enc.encode(text))
        except Exception:
            return max(len(text) // 3, len(text.encode("utf-8")) // 4)

    def _chunk_text(self, text: str) -> List[str]:
        """Split text into chunks each within max_tokens.

        Splitting priority: paragraphs (\\n\\n) > sentences (。.!?\\n) > fixed length.
        """
        max_tok = self.max_tokens
        if self._estimate_tokens(text) <= max_tok:
            return [text]

        paragraphs = text.split("\n\n")
        if len(paragraphs) > 1:
            chunks = self._merge_segments(paragraphs, max_tok, "\n\n")
            if all(self._estimate_tokens(c) <= max_tok for c in chunks):
                return chunks

        sentences = re.split(r"(?<=[。.!?\n])", text)
        sentences = [s for s in sentences if s]
        if len(sentences) > 1:
            chunks = self._merge_segments(sentences, max_tok, "")
            if all(self._estimate_tokens(c) <= max_tok for c in chunks):
                return chunks

        return self._fixed_length_split(text, max_tok)

    def _merge_segments(self, segments: List[str], max_tok: int, separator: str) -> List[str]:
        chunks: List[str] = []
        current = ""
        for seg in segments:
            candidate = (current + separator + seg) if current else seg
            if self._estimate_tokens(candidate) <= max_tok:
                current = candidate
            else:
                if current:
                    chunks.append(current)
                current = seg
        if current:
            chunks.append(current)
        return chunks

    def _fixed_length_split(self, text: str, max_tok: int) -> List[str]:
        total_tokens = self._estimate_tokens(text)
        chars_per_token = len(text) / max(total_tokens, 1)
        chunk_size = max(int(max_tok * chars_per_token * 0.9), 100)

        chunks: List[str] = []
        start = 0
        while start < len(text):
            end = start + chunk_size
            if end < len(text):
                boundary = text.rfind(" ", start, end)
                if boundary > start:
                    end = boundary
            chunks.append(text[start:end])
            start = end
        return chunks

    def _update_telemetry_token_usage(self, response_data: Dict[str, Any]) -> None:
        """Update telemetry with token usage from API response"""
        pass

    def _embed_single(self, text: str) -> EmbedResult:
        """Perform raw embedding without chunking logic.

        Args:
            text: Input text

        Returns:
            EmbedResult: Result containing only dense_vector

        Raises:
            RuntimeError: When API call fails
        """
        try:
            url = f"{self.api_base}/models/{self.model_name}:embedContent"

            headers = {
                "Content-Type": "application/json",
                "x-goog-api-key": self.api_key,
                **self.extra_headers,
            }

            request_body: Dict[str, Any] = {"content": {"parts": [{"text": text}]}}
            if self.dimension:
                request_body["output_dimensionality"] = self.dimension

            def _do_request():
                resp = requests.post(url, json=request_body, headers=headers, timeout=30)
                resp.raise_for_status()
                return resp

            response = exponential_backoff_retry(
                _do_request,
                is_retryable=lambda e: isinstance(e, requests.exceptions.ConnectionError)
                or isinstance(e, requests.exceptions.Timeout),
                logger=logger,
            )

            response_data = response.json()

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
            is_query: Ignored. gemini-embedding-2-preview does not support taskType.

        Returns:
            EmbedResult: Result containing only dense_vector

        Raises:
            RuntimeError: When API call fails
        """
        if not text or not text.strip():
            return EmbedResult()

        if self._estimate_tokens(text) > self.max_tokens:
            return self._chunk_and_embed(text)

        return self._embed_single(text)

    def _chunk_and_embed(self, text: str) -> EmbedResult:
        """Chunk oversized text and average the embeddings."""
        chunks = self._chunk_text(text)
        chunk_vectors: List[List[float]] = []

        for chunk in chunks:
            result = self._embed_single(chunk)
            chunk_vectors.append(result.dense_vector)

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
        does not support batch requests.

        Args:
            texts: List of texts
            is_query: Ignored. gemini-embedding-2-preview does not support taskType.

        Returns:
            List[EmbedResult]: List of embedding results

        Raises:
            RuntimeError: When API call fails
        """
        if not texts:
            return []

        results: List[EmbedResult] = []

        for text in texts:
            if not text or not text.strip():
                results.append(EmbedResult())
                continue
            if self._estimate_tokens(text) <= self.max_tokens:
                result = self._embed_single(text)
            else:
                result = self._chunk_and_embed(text)
            results.append(result)

        return results

    def get_dimension(self) -> int:
        """Get embedding dimension

        Returns:
            int: Vector dimension
        """
        return self._dimension
