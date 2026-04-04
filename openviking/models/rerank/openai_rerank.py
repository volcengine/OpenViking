# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0
"""
OpenAI-compatible Rerank API Client.

Supports third-party rerank services like Alibaba Cloud DashScope (qwen3-rerank)
via api_key + api_base configuration.
"""

# For logging, use Python's built-in logging
import logging
from typing import List, Optional

import requests

from openviking.models.rerank.base import RerankBase

logger = logging.getLogger(__name__)


class OpenAIRerankClient(RerankBase):
    """
    OpenAI-compatible rerank API client using Bearer token auth.

    Compatible with services like Alibaba Cloud DashScope.
    """

    def __init__(self, api_key: str, api_base: str, model_name: str):
        """
        Initialize OpenAI-compatible rerank client.

        Args:
            api_key: Bearer token for authentication
            api_base: Full endpoint URL for the rerank API
            model_name: Model name to use for reranking
        """
        super().__init__()
        self.api_key = api_key
        self.api_base = api_base
        self.model_name = model_name
        self.provider = "openai"

    def rerank_batch(self, query: str, documents: List[str]) -> Optional[List[float]]:
        """
        Batch rerank documents against a query.

        Args:
            query: Query text
            documents: List of document texts to rank

        Returns:
            List of rerank scores for each document (same order as input),
            or None when rerank fails and the caller should fall back
        """
        if not documents:
            return []

        req_body = {
            "model": self.model_name,
            "query": query,
            "documents": documents,
        }

        try:
            response = requests.post(
                url=self.api_base,
                headers={
                    "Authorization": f"Bearer {self.api_key}",
                    "Content-Type": "application/json",
                },
                json=req_body,
                timeout=30,
            )
            response.raise_for_status()
            result = response.json()

            # Update token usage tracking (estimate, OpenAI rerank doesn't provide token info)
            self._extract_and_update_token_usage(result, query, documents)

            # Standard OpenAI/Cohere rerank format: results[].{index, relevance_score}
            results = result.get("results")
            if not results:
                logger.warning(f"[OpenAIRerankClient] Unexpected response format: {result}")
                return None

            if len(results) != len(documents):
                logger.warning(
                    "[OpenAIRerankClient] Unexpected rerank result length: expected=%s actual=%s",
                    len(documents),
                    len(results),
                )
                return None

            # Results may not be in original order — sort by index
            scores = [0.0] * len(documents)
            for item in results:
                idx = item.get("index")
                if idx is None or not (0 <= idx < len(documents)):
                    logger.warning(
                        "[OpenAIRerankClient] Out-of-bounds or missing index in result: %s", item
                    )
                    return None
                scores[idx] = item.get("relevance_score", 0.0)

            logger.debug(f"[OpenAIRerankClient] Reranked {len(documents)} documents")
            return scores

        except Exception as e:
            logger.error(f"[OpenAIRerankClient] Rerank failed: {e}")
            return None

    @classmethod
    def from_config(cls, config) -> Optional["OpenAIRerankClient"]:
        """
        Create OpenAIRerankClient from RerankConfig.

        Args:
            config: RerankConfig instance with provider='openai'

        Returns:
            OpenAIRerankClient instance or None if config is not available
        """
        if not config or not config.is_available():
            return None
        return cls(
            api_key=config.api_key,
            api_base=config.api_base,
            model_name=config.model or "qwen3-rerank",
        )
