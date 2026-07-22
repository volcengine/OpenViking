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

# DashScope native API paths that use a nested request/response envelope.
# The OpenAI-compatible endpoint (/compatible-api/) uses the flat protocol.
_DASHSCOPE_NATIVE_PATH_MARKERS = ("/api/v1/services/rerank",)


def _uses_nested_envelope(api_base: str) -> bool:
    """Return True if the endpoint uses DashScope's native nested envelope.

    DashScope exposes two API styles:
    - ``/compatible-api/v1/reranks`` — OpenAI-compatible, flat body and
      top-level ``results`` (used by qwen3-rerank).
    - ``/api/v1/services/rerank`` — native DashScope, nested
      ``input``/``output`` envelope (used by gte-rerank-v2, qwen3-vl-rerank).

    Detection is path-based, not hostname-based, so both styles work
    regardless of which DashScope host or gateway the user configures.
    """
    return any(marker in api_base for marker in _DASHSCOPE_NATIVE_PATH_MARKERS)


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
        self._uses_nested_envelope = _uses_nested_envelope(api_base)

    def _build_request_body(self, query: str, documents: List[str]) -> dict:
        """Build the request body for the rerank API.

        DashScope native API uses a nested envelope:
            {"model": ..., "input": {"query": ..., "documents": [...]}, "parameters": {...}}

        Standard OpenAI/Cohere and DashScope compatible-api use a flat body:
            {"model": ..., "query": ..., "documents": [...]}
        """
        if self._uses_nested_envelope:
            return {
                "model": self.model_name,
                "input": {
                    "query": query,
                    "documents": documents,
                },
                "parameters": {
                    "return_documents": False,
                },
            }
        return {
            "model": self.model_name,
            "query": query,
            "documents": documents,
        }

    def _extract_results(self, response_json: dict) -> Optional[List[dict]]:
        """Extract the results list from an API response.

        DashScope native API nests results under ``output.results``;
        standard services and DashScope compatible-api return ``results``
        at the top level.
        """
        if self._uses_nested_envelope:
            output = response_json.get("output")
            if isinstance(output, dict):
                return output.get("results")
            return None
        return response_json.get("results")

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

        req_body = self._build_request_body(query, documents)

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
            self._extract_and_update_token_usage(result, query, documents)

            results = self._extract_results(result)
            if not results:
                logger.warning(f"[OpenAIRerankClient] Unexpected response format: {result}")
                return None

            if len(results) != len(documents):
                logger.warning(
                    "[OpenAIRerankClient] Sparse rerank results: expected=%s actual=%s",
                    len(documents),
                    len(results),
                )

            # Results may be sparse or out of order. Missing documents keep a
            # zero score while returned scores map back to their input indexes.
            # Both "relevance_score" (singular) and "relevance_scores" (plural)
            # are accepted — DashScope uses the singular form.
            scores = [0.0] * len(documents)
            for item in results:
                idx = item.get("index")
                if idx is None or not (0 <= idx < len(documents)):
                    logger.warning(
                        "[OpenAIRerankClient] Out-of-bounds or missing index in result: %s", item
                    )
                    return None
                scores[idx] = item.get("relevance_score", item.get("relevance_scores", 0.0))

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
            extra_headers=config.extra_headers,
            timeout=config.timeout,
        )
