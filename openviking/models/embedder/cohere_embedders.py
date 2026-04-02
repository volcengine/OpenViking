# Copyright (c) 2026 Antigravity / Dico Angelo
# SPDX-License-Identifier: AGPL-3.0
"""Cohere dense embedder implementation.

Uses Cohere's Embed API v2 (https://docs.cohere.com/reference/embed).
Supports embed-v4.0 and embed-english-v3.0 models with input_type
for asymmetric retrieval.
"""

from typing import Any, Dict, List, Optional

import httpx

from openviking.models.embedder.base import DenseEmbedderBase, EmbedResult, truncate_and_normalize

COHERE_MODEL_DIMENSIONS = {
    "embed-v4.0": 1536,
    "embed-multilingual-v3.0": 1024,
    "embed-english-v3.0": 1024,
    "embed-multilingual-light-v3.0": 384,
    "embed-english-light-v3.0": 384,
}

# embed-v4.0 supports server-side dimension reduction via output_dimension
COHERE_ALLOWED_DIMENSIONS = {
    "embed-v4.0": {256, 512, 1024, 1536},
}


def get_cohere_model_default_dimension(model_name: Optional[str]) -> int:
    if not model_name:
        return 1024
    return COHERE_MODEL_DIMENSIONS.get(model_name.lower(), 1024)


class CohereDenseEmbedder(DenseEmbedderBase):
    """Cohere dense embedder.

    Cohere uses its own REST API (not OpenAI-compatible), so we call it
    directly via httpx.  Supports asymmetric search via input_type.
    """

    def __init__(
        self,
        model_name: str = "embed-v4.0",
        api_key: Optional[str] = None,
        api_base: Optional[str] = None,
        dimension: Optional[int] = None,
        config: Optional[Dict[str, Any]] = None,
    ):
        super().__init__(model_name, config)

        self.api_key = api_key
        self.api_base = (api_base or "https://api.cohere.com").rstrip("/")

        if not self.api_key:
            raise ValueError("api_key is required for Cohere provider")

        self._native_dimension = get_cohere_model_default_dimension(model_name)
        self._dimension = dimension or self._native_dimension

        # Check if server-side dimension reduction is supported
        normalized = model_name.lower()
        allowed = COHERE_ALLOWED_DIMENSIONS.get(normalized)
        if allowed and dimension is not None and dimension not in allowed:
            raise ValueError(
                f"Dimension {dimension} not supported for '{model_name}'. "
                f"Allowed: {sorted(allowed)}"
            )

        # Prefer server-side output_dimension when the model supports it
        self._use_server_dim = (
            allowed is not None and dimension is not None and dimension != self._native_dimension
        )
        # Fallback to client-side truncation for v3 models
        self._needs_truncation = (
            not self._use_server_dim
            and dimension is not None
            and dimension < self._native_dimension
        )
        self._client = httpx.Client(
            base_url=self.api_base,
            headers={
                "Authorization": f"Bearer {self.api_key}",
                "Content-Type": "application/json",
            },
            timeout=60.0,
        )

    def _call_api(self, texts: List[str], input_type: str) -> List[List[float]]:
        payload: Dict[str, Any] = {
            "model": self.model_name,
            "texts": texts,
            "input_type": input_type,
            "embedding_types": ["float"],
        }
        if self._use_server_dim:
            payload["output_dimension"] = self._dimension
        resp = self._client.post("/v2/embed", json=payload)
        resp.raise_for_status()
        data = resp.json()
        return data["embeddings"]["float"]

    def _normalize_vector(self, vector: List[float]) -> List[float]:
        """Truncate and renormalize if dimension reduction was requested."""
        if self._needs_truncation:
            return truncate_and_normalize(vector, self._dimension)
        return vector

    def embed(self, text: str, is_query: bool = False) -> EmbedResult:
        input_type = "search_query" if is_query else "search_document"
        try:
            vectors = self._call_api([text], input_type)
            result = EmbedResult(dense_vector=self._normalize_vector(vectors[0]))
            # Estimate token usage
            estimated_tokens = self._estimate_tokens(text)
            self.update_token_usage(
                model_name=self.model_name,
                provider="cohere",
                prompt_tokens=estimated_tokens,
                completion_tokens=0,
            )
            return result
        except httpx.HTTPStatusError as e:
            raise RuntimeError(
                f"Cohere API error: {e.response.status_code} {e.response.text}"
            ) from e
        except Exception as e:
            raise RuntimeError(f"Cohere embedding failed: {e}") from e

    def embed_batch(self, texts: List[str], is_query: bool = False) -> List[EmbedResult]:
        if not texts:
            return []
        input_type = "search_query" if is_query else "search_document"
        try:
            results: List[EmbedResult] = []
            for i in range(0, len(texts), 96):
                batch = texts[i : i + 96]
                vectors = self._call_api(batch, input_type)
                results.extend(EmbedResult(dense_vector=self._normalize_vector(v)) for v in vectors)
            # Estimate token usage for batch
            total_tokens = sum(self._estimate_tokens(text) for text in texts)
            self.update_token_usage(
                model_name=self.model_name,
                provider="cohere",
                prompt_tokens=total_tokens,
                completion_tokens=0,
            )
            return results
        except httpx.HTTPStatusError as e:
            raise RuntimeError(
                f"Cohere API error: {e.response.status_code} {e.response.text}"
            ) from e
        except Exception as e:
            raise RuntimeError(f"Cohere batch embedding failed: {e}") from e

    def close(self):
        """Close the httpx client connection pool."""
        self._client.close()

    def get_dimension(self) -> int:
        return self._dimension
