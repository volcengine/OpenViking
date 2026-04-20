# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0
"""
LiteLLM Rerank API Client.
"""

# For logging, use Python's built-in logging
import logging
import time
from typing import List, Optional

from openviking.models.rerank.base import RerankBase

logger = logging.getLogger(__name__)


class LiteLLMRerankClient(RerankBase):
    """
    LiteLLM rerank API client.
    """

    def __init__(self, api_key: Optional[str], api_base: Optional[str], model_name: str):
        """
        Initialize LiteLLM rerank client.

        Args:
            api_key: API key for LiteLLM providers (optional, can come from env)
            api_base: API base for LiteLLM providers (optional, can come from env)
            model_name: Model name to use for reranking
        """
        super().__init__()
        self.api_key = api_key
        self.api_base = api_base
        self.model_name = model_name
        self.provider = "litellm"

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

        try:
            import litellm

            started = time.monotonic()
            response = litellm.rerank(
                model=self.model_name,
                query=query,
                documents=[{"text": d} for d in documents],
                api_key=self.api_key,
                api_base=self.api_base,
            )

            # Update token usage tracking (estimate from response or input)
            response_dict = (
                response.model_dump() if hasattr(response, "model_dump") else response.__dict__
            )
            self._extract_and_update_token_usage(
                response_dict,
                query,
                documents,
                duration_seconds=time.monotonic() - started,
            )

            results = response.results
            if not results:
                logger.warning(f"[LiteLLMRerankClient] Unexpected response format: {response}")
                return None

            if len(results) != len(documents):
                logger.warning(
                    "[LiteLLMRerankClient] Unexpected rerank result length: expected=%s actual=%s",
                    len(documents),
                    len(results),
                )
                return None

            for item in results:
                idx = getattr(item, "index", None)
                if idx is None or not (0 <= idx < len(documents)):
                    logger.warning(
                        "[LiteLLMRerankClient] Out-of-bounds or missing index in result: %s", item
                    )
                    return None

            # Results may not be in original order — sort by index
            sorted_results = sorted(results, key=lambda x: x.index)
            scores = [getattr(item, "relevance_score", 0.0) for item in sorted_results]

            logger.debug(f"[LiteLLMRerankClient] Reranked {len(documents)} documents")
            return scores

        except Exception as e:
            logger.error(f"[LiteLLMRerankClient] Rerank failed: {e}")
            return None

    @classmethod
    def from_config(cls, config) -> Optional["LiteLLMRerankClient"]:
        """
        Create LiteLLMRerankClient from RerankConfig.

        Args:
            config: RerankConfig instance with provider='litellm'

        Returns:
            LiteLLMRerankClient instance or None if config is not available
        """
        if not config or not config.is_available():
            return None
        return cls(
            api_key=config.api_key,
            api_base=config.api_base,
            model_name=config.model,
        )
