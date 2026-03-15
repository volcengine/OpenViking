# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: Apache-2.0
"""MiniMax Embedder Implementation via HTTP API"""

import time
from typing import Any, Dict, List, Optional, Union

import requests
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

from openviking.models.embedder.base import DenseEmbedderBase, EmbedResult
from openviking_cli.utils.logger import default_logger as logger


class MinimaxDenseEmbedder(DenseEmbedderBase):
    """MiniMax Dense Embedder Implementation

    Supports MiniMax embedding models via official HTTP API.
    API Docs: https://platform.minimaxi.com/docs/api-reference/api-overview

    Example:
        >>> embedder = MinimaxDenseEmbedder(
        ...     model_name="embo-01",
        ...     api_key="your-api-key",
        ...     group_id="your-group-id",
        ...     type="db"  # or "query"
        ... )
    """

    DEFAULT_API_BASE = "https://api.minimax.chat/v1/embeddings"
    DEFAULT_MODEL = "embo-01"

    def __init__(
        self,
        model_name: str = DEFAULT_MODEL,
        api_key: Optional[str] = None,
        api_base: Optional[str] = None,
        group_id: Optional[str] = None,
        dimension: Optional[int] = None,
        type: str = "db",
        config: Optional[Dict[str, Any]] = None,
    ):
        """Initialize MiniMax Dense Embedder

        Args:
            model_name: Model name, defaults to embo-01
            api_key: API key
            api_base: API base URL, defaults to https://api.minimax.chat/v1/embeddings
            group_id: Group ID (Required by MiniMax API)
            dimension: Dimension (Optional, MiniMax embo-01 is usually 1536 but docs don't specify, we'll detect)
            type: "db" (for storage) or "query" (for retrieval). Default: "db"
            config: Additional configuration dict
        """
        super().__init__(model_name, config)

        self.api_key = api_key
        self.api_base = api_base or self.DEFAULT_API_BASE
        self.group_id = group_id
        self.type = type
        self._dimension = dimension

        if not self.api_key:
            raise ValueError("api_key is required for MiniMax embedder")
        
        # Initialize session with retry logic
        self.session = self._create_session()

        # Auto-detect dimension if not provided
        if self._dimension is None:
            try:
                self._dimension = self._detect_dimension()
            except Exception as e:
                logger.warning(f"Failed to detect MiniMax dimension: {e}. Defaulting to 1536.")
                self._dimension = 1536

    def _create_session(self) -> requests.Session:
        """Create a requests session with retry logic"""
        session = requests.Session()
        retry_strategy = Retry(
            total=6,
            backoff_factor=1,  # 1s, 2s, 4s, 8s, 16s, 32s
            status_forcelist=[429, 500, 502, 503, 504],
            allowed_methods=["POST"],
        )
        adapter = HTTPAdapter(max_retries=retry_strategy)
        session.mount("https://", adapter)
        session.mount("http://", adapter)
        return session

    def _detect_dimension(self) -> int:
        """Detect dimension by making an actual API call"""
        result = self.embed("test")
        return len(result.dense_vector) if result.dense_vector else 1536

    def _call_api(self, texts: List[str]) -> List[List[float]]:
        """Call MiniMax API"""
        headers = {
            "Authorization": f"Bearer {self.api_key}",
            "Content-Type": "application/json",
        }
        
        params = {}
        if self.group_id:
            params["GroupId"] = self.group_id

        payload = {
            "model": self.model_name,
            "type": self.type,
            "texts": texts,
        }

        try:
            response = self.session.post(
                self.api_base,
                headers=headers,
                params=params,
                json=payload,
                timeout=60,  # 60s timeout
            )
            response.raise_for_status()
            data = response.json()
            
            # Check for business error code
            base_resp = data.get("base_resp", {})
            if base_resp.get("status_code") != 0:
                raise RuntimeError(f"MiniMax API error: {base_resp.get('status_msg')}")
            
            vectors = data.get("vectors", [])
            if not vectors:
                raise RuntimeError("MiniMax API returned empty vectors")

            return vectors

        except requests.exceptions.RequestException as e:
            raise RuntimeError(f"MiniMax network error: {str(e)}") from e
        except Exception as e:
            raise RuntimeError(f"MiniMax embedding failed: {str(e)}") from e

    def embed(self, text: str) -> EmbedResult:
        """Perform dense embedding on text"""
        vectors = self._call_api([text])
        return EmbedResult(dense_vector=vectors[0])

    def embed_batch(self, texts: List[str]) -> List[EmbedResult]:
        """Batch embedding"""
        if not texts:
            return []
            
        # MiniMax might have batch size limits, but let's assume the caller handles batching or use safe defaults
        # For now, we pass through. If needed, we can implement internal chunking.
        vectors = self._call_api(texts)
        return [EmbedResult(dense_vector=v) for v in vectors]

    def get_dimension(self) -> int:
        """Get embedding dimension"""
        return self._dimension
