# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0
"""
Text Embeddings Inference rerank API client.

Hugging Face Text Embeddings Inference (TEI) exposes rerank models through a
provider-specific `/rerank` endpoint. Its request/response shape differs from
OpenAI-compatible rerank APIs, so it needs a dedicated adapter.
"""

import time
from typing import Dict, List, Optional

import requests

from openviking.models.rerank.base import RerankBase
from openviking_cli.utils import get_logger

logger = get_logger(__name__)


class TEIRerankClient(RerankBase):
    """
    TEI rerank API client.

    TEI accepts `texts` and returns a list of `{index, score}` items:
    https://huggingface.co/docs/text-embeddings-inference
    """

    def __init__(
        self,
        api_base: str,
        api_key: Optional[str] = None,
        model_name: Optional[str] = None,
        extra_headers: Optional[Dict[str, str]] = None,
        batch_size: int = 32,
    ) -> None:
        """
        Initialize TEI rerank client.

        Args:
            api_base: TEI base URL (`http://host:port`) or full rerank endpoint.
            api_key: Optional Bearer token for TEI deployments that enforce auth.
            model_name: Optional model name used for usage tracking.
            extra_headers: Optional extra headers for API requests.
            batch_size: Maximum number of documents to send per TEI request.
        """
        super().__init__()
        self.api_base = api_base
        self.api_key = api_key
        self.model_name = model_name
        self.extra_headers = extra_headers or {}
        self.batch_size = max(1, int(batch_size))
        self.provider = "tei"

    @property
    def rerank_url(self) -> str:
        """Return the full TEI rerank URL while accepting base or endpoint config."""
        base = self.api_base.rstrip("/")
        if base.endswith("/rerank"):
            return base
        return f"{base}/rerank"

    def rerank_batch(self, query: str, documents: List[str]) -> Optional[List[float]]:
        """
        Batch rerank documents against a query.

        Args:
            query: Query text
            documents: List of document texts to rank

        Returns:
            List of rerank scores in the same order as input documents, or None
            when rerank fails and the caller should fall back.
        """
        if not documents:
            return []

        scores = [0.0] * len(documents)
        for start in range(0, len(documents), self.batch_size):
            chunk = documents[start : start + self.batch_size]
            chunk_scores = self._rerank_chunk(query, chunk)
            if chunk_scores is None:
                return None
            scores[start : start + len(chunk_scores)] = chunk_scores

        logger.debug(
            "[TEIRerankClient] Reranked %s documents in %s request(s)",
            len(documents),
            (len(documents) + self.batch_size - 1) // self.batch_size,
        )
        return scores

    def _rerank_chunk(self, query: str, documents: List[str]) -> Optional[List[float]]:
        """Rerank one TEI-sized chunk and return scores in chunk-local order."""

        req_body = {
            "query": query,
            "texts": documents,
            "raw_scores": False,
        }

        try:
            headers = {"Content-Type": "application/json"}
            if self.api_key:
                headers["Authorization"] = f"Bearer {self.api_key}"
            if self.extra_headers:
                headers.update(self.extra_headers)

            started = time.monotonic()
            response = requests.post(
                url=self.rerank_url,
                headers=headers,
                json=req_body,
                timeout=30,
            )
            response.raise_for_status()
            result = response.json()

            self._extract_and_update_token_usage(
                {"results": result} if isinstance(result, list) else result,
                query,
                documents,
                duration_seconds=time.monotonic() - started,
            )

            results = self._extract_results(result)
            if not results:
                logger.warning(f"[TEIRerankClient] Unexpected response format: {result}")
                return None

            scores = [0.0] * len(documents)
            for item in results:
                idx = item.get("index")
                if idx is None or not (0 <= idx < len(documents)):
                    logger.warning(
                        "[TEIRerankClient] Out-of-bounds or missing index in result: %s", item
                    )
                    return None
                scores[idx] = float(item.get("score", item.get("relevance_score", 0.0)))

            return scores

        except Exception as e:
            logger.error(f"[TEIRerankClient] Rerank failed: {e}")
            return None

    @staticmethod
    def _extract_results(result) -> Optional[List[dict]]:
        """Extract TEI rerank rows from supported response shapes."""
        if isinstance(result, list):
            return result
        if isinstance(result, dict):
            rows = result.get("results")
            if isinstance(rows, list):
                return rows
        return None

    @classmethod
    def from_config(cls, config) -> Optional["TEIRerankClient"]:
        """
        Create TEIRerankClient from RerankConfig.

        Args:
            config: RerankConfig instance with provider='tei'

        Returns:
            TEIRerankClient instance or None if config is not available
        """
        if not config or not config.is_available():
            return None
        return cls(
            api_base=config.api_base,
            api_key=config.api_key,
            model_name=config.model,
            extra_headers=config.extra_headers,
            batch_size=config.batch_size,
        )
