# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0
"""
ModelsObserver: Multi-model system observability tool.

Provides methods to observe and report token usage across VLM, Embedding, and Rerank models.
"""

from typing import Any, Optional

from openviking.storage.observers.base_observer import BaseObserver
from openviking_cli.utils.logger import get_logger

logger = get_logger(__name__)


class ModelsObserver(BaseObserver):
    """
    ModelsObserver: System observability tool for multi-model token usage monitoring.

    Provides methods to query token usage status and format output for VLM, Embedding, and Rerank models.
    """

    def __init__(
        self,
        vlm_instance: Optional[Any] = None,
        embedding_instance: Optional[Any] = None,
        rerank_instance: Optional[Any] = None,
    ):
        """
        Initialize ModelsObserver with model instances.

        Args:
            vlm_instance: VLM instance to observe (optional)
            embedding_instance: Embedding instance to observe (optional)
            rerank_instance: Rerank instance to observe (optional)
        """
        self._vlm_instance = vlm_instance
        self._embedding_instance = embedding_instance
        self._rerank_instance = rerank_instance

    def get_status_table(self) -> str:
        """
        Format token usage status as a string table.

        Returns:
            Formatted table string representation of token usage for all models
        """
        return self._format_status_as_table()

    def _format_status_as_table(self) -> str:
        """
        Format token usage status as a table using tabulate.

        Returns:
            Formatted table string representation of token usage for all models
        """
        from tabulate import tabulate

        sections = []

        # VLM section
        if self._vlm_instance:
            try:
                vlm_data = self._get_vlm_usage()
                if vlm_data:
                    sections.append(("VLM", vlm_data))
            except Exception as e:
                logger.warning(f"Error getting VLM usage: {e}")

        # Embedding section
        if self._embedding_instance:
            try:
                embedding_data = self._get_embedding_usage()
                if embedding_data:
                    sections.append(("Embedding", embedding_data))
            except Exception as e:
                logger.warning(f"Error getting Embedding usage: {e}")

        # Rerank section
        if self._rerank_instance:
            try:
                rerank_data = self._get_rerank_usage()
                if rerank_data:
                    sections.append(("Rerank", rerank_data))
            except Exception as e:
                logger.warning(f"Error getting Rerank usage: {e}")

        if not sections:
            return "No model usage data available."

        # Format output
        lines = []
        for model_type, data in sections:
            lines.append(f"\n{model_type} Models:")
            lines.append(tabulate(data, headers="keys", tablefmt="pretty"))
            lines.append("")

        return "\n".join(lines)

    def _get_vlm_usage(self) -> Optional[list]:
        """Get VLM token usage data."""
        if not self._vlm_instance:
            return None

        usage_data = self._vlm_instance.get_token_usage()

        if not usage_data.get("usage_by_model"):
            return None

        data = []
        for model_name, model_data in usage_data["usage_by_model"].items():
            for provider_name, provider_data in model_data["usage_by_provider"].items():
                data.append(
                    {
                        "Model": model_name,
                        "Provider": provider_name,
                        "Calls": provider_data.get("call_count", 0),
                        "Prompt": provider_data["prompt_tokens"],
                        "Completion": provider_data["completion_tokens"],
                        "Total": provider_data["total_tokens"],
                        "Last Updated": provider_data["last_updated"],
                    }
                )

        return data

    def _get_embedding_usage(self) -> Optional[list]:
        """Get Embedding token usage data."""
        if not self._embedding_instance:
            return None

        if hasattr(self._embedding_instance, "get_token_usage"):
            usage_data = self._embedding_instance.get_token_usage()
        elif hasattr(self._embedding_instance, "_token_tracker"):
            usage_data = self._embedding_instance._token_tracker.to_dict()
        else:
            return None

        if not usage_data.get("usage_by_model"):
            return None

        data = []
        for model_name, model_data in usage_data["usage_by_model"].items():
            for provider_name, provider_data in model_data["usage_by_provider"].items():
                data.append(
                    {
                        "Model": model_name,
                        "Provider": provider_name,
                        "Calls": provider_data.get("call_count", 0),
                        "Prompt": provider_data["prompt_tokens"],
                        "Completion": provider_data["completion_tokens"],
                        "Total": provider_data["total_tokens"],
                        "Last Updated": provider_data["last_updated"],
                    }
                )

        return data

    def _get_rerank_usage(self) -> Optional[list]:
        """Get Rerank token usage data."""
        if not self._rerank_instance:
            return None

        if hasattr(self._rerank_instance, "get_token_usage"):
            usage_data = self._rerank_instance.get_token_usage()
        elif hasattr(self._rerank_instance, "_token_tracker"):
            usage_data = self._rerank_instance._token_tracker.to_dict()
        else:
            return None

        if not usage_data.get("usage_by_model"):
            return None

        data = []
        for model_name, model_data in usage_data["usage_by_model"].items():
            for provider_name, provider_data in model_data["usage_by_provider"].items():
                data.append(
                    {
                        "Model": model_name,
                        "Provider": provider_name,
                        "Calls": provider_data.get("call_count", 0),
                        "Prompt": provider_data["prompt_tokens"],
                        "Completion": provider_data["completion_tokens"],
                        "Total": provider_data["total_tokens"],
                        "Last Updated": provider_data["last_updated"],
                    }
                )

        return data

    def __str__(self) -> str:
        return self.get_status_table()

    def is_healthy(self) -> bool:
        """
        Check if model system is healthy.

        For ModelsObserver, healthy means at least one model is available and token tracking is working.

        Returns:
            True if system is healthy, False otherwise
        """
        return (
            self._vlm_instance is not None
            or self._embedding_instance is not None
            or self._rerank_instance is not None
        )

    def has_errors(self) -> bool:
        """
        Check if model system has any errors.

        For ModelsObserver, errors are not tracked in token usage.

        Returns:
            False (no error tracking in token usage)
        """
        return False
