# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: Apache-2.0
"""
VLMObserver: VLM system observability tool.

Provides methods to observe and report token usage across VLM models and backends.
"""

from openviking.models.vlm.base import VLMBase
from openviking.storage.observers.base_observer import BaseObserver
from openviking_cli.utils.logger import get_logger

logger = get_logger(__name__)


class VLMObserver(BaseObserver):
    """
    VLMObserver: System observability tool for VLM token usage monitoring.

    Provides methods to query token usage status and format output.
    """

    def __init__(self, vlm_instance: VLMBase):
        """
        Initialize VLMObserver with a VLM instance.

        Args:
            vlm_instance: VLMBase instance to observe
        """
        self._vlm_instance = vlm_instance

    def get_status_table(self) -> str:
        """
        Format token usage status as a string table.

        Returns:
            Formatted table string representation of token usage
        """
        return self._format_status_as_table()

    def _format_status_as_table(self) -> str:
        """
        Format token usage status as a table using tabulate.

        Returns:
            Formatted table string representation of token usage
        """
        from tabulate import tabulate

        usage_data = self._vlm_instance.get_token_usage()

        if not usage_data.get("usage_by_model"):
            return "No token usage data available."

        data = []
        total_prompt = 0
        total_completion = 0
        total_all = 0

        for model_name, model_data in usage_data["usage_by_model"].items():
            for provider_name, provider_data in model_data["usage_by_provider"].items():
                data.append(
                    {
                        "Model": model_name,
                        "Provider": provider_name,
                        "Prompt": provider_data["prompt_tokens"],
                        "Completion": provider_data["completion_tokens"],
                        "Total": provider_data["total_tokens"],
                        "Last Updated": provider_data["last_updated"],
                    }
                )
                total_prompt += provider_data["prompt_tokens"]
                total_completion += provider_data["completion_tokens"]
                total_all += provider_data["total_tokens"]

        if not data:
            return "No token usage data available."

        # Add total row
        data.append(
            {
                "Model": "TOTAL",
                "Provider": "",
                "Prompt": total_prompt,
                "Completion": total_completion,
                "Total": total_all,
                "Last Updated": "",
            }
        )

        return tabulate(data, headers="keys", tablefmt="pretty")

    def __str__(self) -> str:
        return self.get_status_table()

    def is_healthy(self) -> bool:
        """
        Check if VLM system is healthy.

        For VLMObserver, healthy means token tracking is enabled and working.

        Returns:
            True if system is healthy, False otherwise
        """
        return True  # Token tracking doesn't have a health state

    def has_errors(self) -> bool:
        """
        Check if VLM system has any errors.

        For VLMObserver, errors are not tracked in token usage.

        Returns:
            False (no error tracking in token usage)
        """
        return False
