# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: Apache-2.0
"""Volcengine Embedder Implementation"""

from typing import Any, Dict, List, Optional

import volcenginesdkarkruntime

from openviking.models.embedder.base import (
    DenseEmbedderBase,
    EmbedResult,
    HybridEmbedderBase,
    SparseEmbedderBase,
    truncate_and_normalize,
)


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

        self.api_key = api_key
        self.api_base = api_base or "https://ark.cn-beijing.volces.com/api/v3"
        self.dimension = dimension
        self.input_type = input_type

        if not self.api_key:
            raise ValueError("api_key is required")

        # Initialize Volcengine client
        self.client = volcenginesdkarkruntime.Ark(api_key=self.api_key, base_url=self.api_base)

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

    def embed(self, text: str) -> EmbedResult:
        """Perform dense embedding on text

        Args:
            text: Input text

        Returns:
            EmbedResult: Result containing dense_vector

        Raises:
            RuntimeError: When API call fails
        """
        try:
            if self.input_type == "multimodal":
                # Use multimodal embeddings API
                response = self.client.multimodal_embeddings.create(
                    input=[{"type": "text", "text": text}], model=self.model_name
                )
                vector = response.data.embedding
            else:
                # Use text embeddings API
                response = self.client.embeddings.create(input=text, model=self.model_name)
                vector = response.data[0].embedding

            vector = truncate_and_normalize(vector, self.dimension)
            return EmbedResult(dense_vector=vector)
        except Exception as e:
            raise RuntimeError(f"Volcengine embedding failed: {str(e)}") from e

    def embed_batch(self, texts: List[str]) -> List[EmbedResult]:
        """Batch embedding

        Args:
            texts: List of texts

        Returns:
            List[EmbedResult]: List of embedding results

        Raises:
            RuntimeError: When API call fails
        """
        if not texts:
            return []

        try:
            if self.input_type == "multimodal":
                multimodal_inputs = [{"type": "text", "text": text} for text in texts]
                response = self.client.multimodal_embeddings.create(
                    input=multimodal_inputs, model=self.model_name
                )
                data = response.data
            else:
                response = self.client.embeddings.create(input=texts, model=self.model_name)
                data = response.data

            return [
                EmbedResult(dense_vector=truncate_and_normalize(item.embedding, self.dimension))
                for item in data
            ]
        except Exception as e:
            raise RuntimeError(f"Volcengine batch embedding failed: {str(e)}") from e

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
        config: Optional[Dict[str, Any]] = None,
    ):
        """Initialize Volcengine Sparse Embedder

        Args:
            model_name: Volcengine model name
            api_key: API key for authentication
            api_base: API base URL
            config: Additional configuration dict

        Raises:
            ValueError: If api_key is not provided
        """
        super().__init__(model_name, config)

        self.api_key = api_key
        self.api_base = api_base

        if not self.api_key:
            raise ValueError("api_key is required")

        self.client = volcenginesdkarkruntime.Ark(api_key=self.api_key, base_url=self.api_base)

    def embed(self, text: str) -> EmbedResult:
        """Perform sparse embedding on text

        Args:
            text: Input text

        Returns:
            EmbedResult: Result containing sparse_vector

        Raises:
            RuntimeError: When API call fails
        """
        try:
            # Must use multimodal endpoint for sparse
            response = self.client.multimodal_embeddings.create(
                input=[{"type": "text", "text": text}],
                model=self.model_name,
                sparse_embedding={"type": "enabled"},
            )
            item = response.data[0]
            sparse_vector = getattr(item, "sparse_embedding", None)
            return EmbedResult(sparse_vector=process_sparse_embedding(sparse_vector))
        except Exception as e:
            raise RuntimeError(f"Volcengine sparse embedding failed: {str(e)}") from e

    def embed_batch(self, texts: List[str]) -> List[EmbedResult]:
        """Batch sparse embedding

        Args:
            texts: List of texts

        Returns:
            List[EmbedResult]: List of embedding results

        Raises:
            RuntimeError: When API call fails
        """
        if not texts:
            return []
        try:
            multimodal_inputs = [{"type": "text", "text": text} for text in texts]
            response = self.client.multimodal_embeddings.create(
                input=multimodal_inputs, model=self.model_name, sparse_embedding={"type": "enabled"}
            )
            results = []
            for item in response.data:
                sparse_vector = getattr(item, "sparse_embedding", None)
                results.append(EmbedResult(sparse_vector=process_sparse_embedding(sparse_vector)))
            return results
        except Exception as e:
            raise RuntimeError(f"Volcengine batch sparse embedding failed: {str(e)}") from e


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
        self.api_key = api_key
        self.api_base = api_base
        self.dimension = dimension
        self.input_type = input_type

        if not self.api_key:
            raise ValueError("api_key is required")

        self.client = volcenginesdkarkruntime.Ark(api_key=self.api_key, base_url=self.api_base)
        self._dimension = dimension or 2048

    def embed(self, text: str) -> EmbedResult:
        """Perform hybrid embedding on text

        Args:
            text: Input text

        Returns:
            EmbedResult: Result containing both dense_vector and sparse_vector

        Raises:
            RuntimeError: When API call fails
        """
        try:
            # Always use multimodal for hybrid to get both

            response = self.client.multimodal_embeddings.create(
                input=[{"type": "text", "text": text}],
                model=self.model_name,
                sparse_embedding={"type": "enabled"},
            )
            item = response.data
            dense_vector = truncate_and_normalize(item.embedding, self.dimension)
            sparse_vector = getattr(item, "sparse_embedding", None)

            return EmbedResult(
                dense_vector=dense_vector, sparse_vector=process_sparse_embedding(sparse_vector)
            )
        except Exception as e:
            raise RuntimeError(f"Volcengine hybrid embedding failed: {str(e)}") from e

    def embed_batch(self, texts: List[str]) -> List[EmbedResult]:
        """Batch hybrid embedding

        Args:
            texts: List of texts

        Returns:
            List[EmbedResult]: List of embedding results

        Raises:
            RuntimeError: When API call fails
        """
        if not texts:
            return []
        try:
            multimodal_inputs = [{"type": "text", "text": text} for text in texts]
            response = self.client.multimodal_embeddings.create(
                input=multimodal_inputs, model=self.model_name, sparse_embedding={"type": "enabled"}
            )
            results = []
            for item in response.data:
                dense_vector = truncate_and_normalize(item.embedding, self.dimension)
                sparse_vector = getattr(item, "sparse_embedding", None)
                results.append(
                    EmbedResult(
                        dense_vector=dense_vector,
                        sparse_vector=process_sparse_embedding(sparse_vector),
                    )
                )
            return results
        except Exception as e:
            raise RuntimeError(f"Volcengine batch hybrid embedding failed: {str(e)}") from e

    def get_dimension(self) -> int:
        return self._dimension
