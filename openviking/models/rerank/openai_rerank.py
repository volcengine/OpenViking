# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0
"""
OpenAI-compatible Rerank API Client.

Supports third-party rerank services like Alibaba Cloud DashScope (qwen3-rerank)
via api_key + api_base configuration.
"""

# For logging, use Python's built-in logging
from typing import Dict, List, Optional

import requests

from openviking.models.rerank.base import RerankBase
from openviking_cli.utils import get_logger

logger = get_logger(__name__)


class OpenAIRerankClient(RerankBase):
    """
    OpenAI-compatible rerank API client using Bearer token auth.

    Compatible with services like Alibaba Cloud DashScope.
    """

    def __init__(
        self,
        api_key: str,
        api_base: str,
        model_name: str,
        extra_headers: Optional[Dict[str, str]] = None,
        timeout: float = 30.0,
    ) -> None:
        """
        Initialize OpenAI-compatible rerank client.

        Args:
            api_key: Bearer token for authentication
            api_base: Full endpoint URL for the rerank API
            model_name: Model name to use for reranking
            extra_headers: Optional extra headers for API requests
            timeout: HTTP request timeout in seconds. Defaults to 30. Increase for
                local LLM servers that incur model cold-start latency on the first call.
        """
        super().__init__()
        self.api_key = api_key
        self.api_base = api_base
        self.model_name = model_name
        self.extra_headers = extra_headers or {}
        self.timeout = timeout
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

        # Filter out empty/blank documents that providers like SiliconFlow silently drop.
        # Track original indices so we can reconstruct a full scores array.
        non_empty_docs: List[str] = []
        non_empty_indices: List[int] = []
        for i, doc in enumerate(documents):
            text = (doc or "").strip()
            if text:
                non_empty_docs.append(text)
                non_empty_indices.append(i)
            else:
                logger.debug(
                    "[OpenAIRerankClient] Skipping empty document at index %s", i
                )

        if not non_empty_docs:
            return [0.0] * len(documents)

        req_body = {
            "model": self.model_name,
            "query": query,
            "documents": non_empty_docs,
            "top_n": len(non_empty_docs),
        }

        try:
            headers = {
                "Authorization": f"Bearer {self.api_key}",
                "Content-Type": "application/json",
            }
            if self.extra_headers:
                headers.update(self.extra_headers)

            response = requests.post(
                url=self.api_base,
                headers=headers,
                json=req_body,
                timeout=self.timeout,
            )
            response.raise_for_status()
            result = response.json()

            # Update token usage tracking (estimate, OpenAI rerank doesn't provide token info)
            self._extract_and_update_token_usage(result, query, non_empty_docs)

            # Standard OpenAI/Cohere rerank format: results[].{index, relevance_score}
            results = result.get("results")
            if not results:
                logger.warning(f"[OpenAIRerankClient] Unexpected response format: {result}")
                return None

            if len(results) != len(non_empty_docs):
                logger.warning(
                    "[OpenAIRerankClient] Unexpected rerank result length: expected=%s actual=%s",
                    len(non_empty_docs),
                    len(results),
                )
                return None

            # Map API results (indexed against non_empty_docs) back to original indices
            scores = [0.0] * len(documents)
            for item in results:
                api_idx = item.get("index")
                if api_idx is None or not (0 <= api_idx < len(non_empty_docs)):
                    logger.warning(
                        "[OpenAIRerankClient] Out-of-bounds or missing index in result: %s", item
                    )
                    return None
                original_idx = non_empty_indices[api_idx]
                scores[original_idx] = item.get("relevance_score", 0.0)

            logger.debug(f"[OpenAIRerankClient] Reranked {len(non_empty_docs)} documents")
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
            extra_headers=config.extra_headers,
            timeout=config.timeout,
        )
