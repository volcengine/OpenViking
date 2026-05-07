# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0
"""
RerankBase: Base class for all rerank clients.

Provides common token usage tracking functionality.
"""

import logging
from typing import Any, Dict

_token_tracker_instance = None
logger = logging.getLogger(__name__)


def _get_token_tracker():
    """Lazy import to avoid circular dependency. Returns shared singleton instance."""
    global _token_tracker_instance
    if _token_tracker_instance is None:
        from openviking.models.vlm.token_usage import TokenUsageTracker

        _token_tracker_instance = TokenUsageTracker()
    return _token_tracker_instance


def get_shared_rerank_token_usage() -> Dict[str, Any]:
    """
    Return the process-wide rerank token usage snapshot.

    This reads the shared singleton `TokenUsageTracker` used by all rerank clients.
    It exists so observability paths (e.g. Prometheus scrape) can access rerank usage
    without constructing provider clients that may allocate network resources.
    """
    return _get_token_tracker().to_dict()


class RerankBase:
    """Base class for all rerank clients

    Provides common token usage tracking functionality.
    """

    def __init__(self):
        """Initialize rerank client with token tracking"""
        # Token usage tracking
        self._token_tracker = _get_token_tracker()

    def _estimate_tokens(self, text: str) -> int:
        """Estimate token count for text (rough approximation: 1 token ≈ 4 characters)."""
        return max(1, len(text) // 4)

    def update_token_usage(
        self,
        model_name: str,
        provider: str,
        prompt_tokens: int,
        completion_tokens: int,
        duration_seconds: float = 0.0,
    ) -> None:
        """Update token usage

        Args:
            model_name: Model name
            provider: Provider name (vikingdb, openai, cohere, litellm, etc.)
            prompt_tokens: Number of input tokens
            completion_tokens: Number of output tokens
            duration_seconds: Wall-clock duration of the rerank provider call in seconds
        """
        self._token_tracker.update(
            model_name=model_name,
            provider=provider,
            prompt_tokens=prompt_tokens,
            completion_tokens=completion_tokens,
        )
        try:
            from openviking.telemetry import get_current_telemetry

            get_current_telemetry().record_token_usage(
                "rerank",
                int(prompt_tokens),
                int(completion_tokens),
                stage="rerank",
            )
        except Exception as e:
            # Telemetry must never break rerank execution.
            if logger.isEnabledFor(logging.DEBUG):
                logger.debug(
                    "rerank.update_token_usage telemetry emit failed provider=%s model_name=%s err=%s: %s",
                    provider,
                    model_name,
                    type(e).__name__,
                    e,
                )
        try:
            from openviking.metrics.datasources import RerankEventDataSource
            from openviking.observability.context import get_root_observability_context

            root_context = get_root_observability_context()

            RerankEventDataSource.record_call(
                provider=str(provider),
                model_name=str(model_name),
                duration_seconds=max(float(duration_seconds), 0.0),
                prompt_tokens=int(prompt_tokens),
                completion_tokens=int(completion_tokens),
                account_id=root_context.account_id if root_context is not None else None,
            )
        except Exception as e:
            # Metrics must never break rerank execution.
            if logger.isEnabledFor(logging.DEBUG):
                logger.debug(
                    "rerank.update_token_usage metrics emit failed provider=%s model_name=%s err=%s: %s",
                    provider,
                    model_name,
                    type(e).__name__,
                    e,
                )

    def _extract_and_update_token_usage(
        self,
        response_data: dict,
        query: str,
        documents: list,
        duration_seconds: float = 0.0,
    ) -> None:
        """Extract and update token usage from API response.

        Args:
            response_data: Raw API response dict
            query: Query text (for estimation if needed)
            documents: List of documents (for estimation if needed)
            duration_seconds: Wall-clock duration of the rerank provider call in seconds
        """
        prompt_tokens = 0
        completion_tokens = 0

        # Try to extract token usage from response meta (Coherever format)
        meta = response_data.get("meta", {})
        if isinstance(meta, dict):
            billed_units = meta.get("billed_units", {})
            if isinstance(billed_units, dict):
                prompt_tokens = billed_units.get("input_tokens", 0)
                completion_tokens = billed_units.get("output_tokens", 0)

        # If no token info in response, estimate based on text
        if prompt_tokens == 0 and completion_tokens == 0:
            prompt_tokens = self._estimate_tokens(query)
            for doc in documents:
                prompt_tokens += self._estimate_tokens(str(doc))
            # Rerank typically doesn't generate output tokens, just scores
            completion_tokens = 0

        # Get model name
        model_name = getattr(self, "model_name", None) or getattr(self, "model", None) or "unknown"

        self.update_token_usage(
            model_name=model_name,
            provider=self.provider,
            prompt_tokens=prompt_tokens,
            completion_tokens=completion_tokens,
            duration_seconds=duration_seconds,
        )

    def get_token_usage(self) -> Dict[str, Any]:
        """Get token usage

        Returns:
            Dict[str, Any]: Token usage dictionary
        """
        return self._token_tracker.to_dict()

    def reset_token_usage(self) -> None:
        """Reset token usage"""
        self._token_tracker.reset()
