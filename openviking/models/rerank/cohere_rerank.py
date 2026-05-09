# Copyright (c) 2026 Antigravity / Dico Angelo
# SPDX-License-Identifier: AGPL-3.0
"""
Cohere Rerank API Client.

Drop-in replacement for VikingDB RerankClient, using Cohere's Rerank v3.5 API.
Same interface: rerank_batch(query, documents) -> List[float]
"""

# For logging, use Python's built-in logging
import logging
import time
from typing import List, Optional

import httpx

from openviking.models.rerank.base import RerankBase

logger = logging.getLogger(__name__)


class CohereRerankClient(RerankBase):
    """Cohere Rerank API client — same interface as VikingDB RerankClient."""

    def __init__(
        self,
        api_key: str,
        model: str = "rerank-v3.5",
        api_base: str = "https://api.cohere.com",
    ):
        super().__init__()
        self.api_key = api_key
        self.model = model
        self.api_base = api_base.rstrip("/")
        self.provider = "cohere"
        self._client = httpx.Client(
            base_url=self.api_base,
            headers={
                "Authorization": f"Bearer {self.api_key}",
                "Content-Type": "application/json",
            },
            timeout=30.0,
        )

    def rerank_batch(self, query: str, documents: List[str]) -> Optional[List[float]]:
        """
        Rerank documents against a query using Cohere Rerank API.

        Args:
            query: Query text
            documents: List of document texts to rank

        Returns:
            List of relevance scores (0-1) in same order as input documents,
            or None on failure (caller should fall back to vector scores).
        """
        if not documents:
            return []

        try:
            started = time.monotonic()
            resp = self._client.post(
                "/v2/rerank",
                json={
                    "model": self.model,
                    "query": query,
                    "documents": documents,
                    "top_n": len(documents),
                    "return_documents": False,
                },
            )
            resp.raise_for_status()
            data = resp.json()

            # Update token usage tracking
            self._extract_and_update_token_usage(
                data,
                query,
                documents,
                duration_seconds=time.monotonic() - started,
            )

            # Cohere returns results sorted by score desc with index field
            # We need to map back to original order
            scores = [0.0] * len(documents)
            for result in data.get("results", []):
                idx = result["index"]
                scores[idx] = result["relevance_score"]

            logger.debug(f"[CohereRerank] Reranked {len(documents)} documents")
            return scores

        except httpx.HTTPStatusError as e:
            logger.error(f"[CohereRerank] API error: {e.response.status_code} {e.response.text}")
            return None
        except Exception as e:
            logger.error(f"[CohereRerank] Rerank failed: {e}")
            return None

    def close(self):
        self._client.close()

    @classmethod
    def from_config(cls, config) -> Optional["CohereRerankClient"]:
        """
        Create CohereRerankClient from RerankConfig.

        Args:
            config: RerankConfig instance with provider='cohere'

        Returns:
            CohereRerankClient instance or None if config is not available
        """
        if not config or not config.is_available():
            return None
        return cls(
            api_key=config.api_key,
            model=config.model_name if config.model_name != "doubao-seed-rerank" else "rerank-v3.5",
        )
