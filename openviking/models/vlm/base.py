# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0
"""VLM base interface and abstract classes"""

import logging
import re
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional, Union

from openviking.utils.time_utils import format_iso8601

from .token_usage import TokenUsageTracker

_THINK_TAG_RE = re.compile(r"<think>[\s\S]*?</think>")
logger = logging.getLogger(__name__)


@dataclass
class ToolCall:
    """Single tool call from LLM."""

    id: str
    name: str
    arguments: Dict[str, Any]


@dataclass
class VLMResponse:
    """VLM response that supports both text content and tool calls."""

    content: Optional[str] = None
    tool_calls: List[ToolCall] = field(default_factory=list)
    finish_reason: str = "stop"  # stop, tool_calls, length, error
    usage: Dict[str, int] = field(
        default_factory=dict
    )  # prompt_tokens, completion_tokens, total_tokens
    reasoning_content: Optional[str] = (
        None  # For thinking process (doubao thinking, deepseek r1, etc.)
    )

    @property
    def has_tool_calls(self) -> bool:
        """Check if response contains tool calls."""
        return len(self.tool_calls) > 0

    def __str__(self) -> str:
        """String representation for backward compatibility - returns content."""
        return self.content or ""


class VLMBase(ABC):
    """VLM base abstract class"""

    def __init__(self, config: Dict[str, Any]):
        self.config = config
        self.provider = config.get("provider", "openai")
        self.model = config.get("model")
        self.api_key = config.get("api_key")
        self.api_base = config.get("api_base")
        self.temperature = config.get("temperature", 0.0)
        self.max_retries = config.get("max_retries", 3)
        self.max_tokens = config.get("max_tokens")
        self.extra_headers = config.get("extra_headers")
        self.stream = config.get("stream", False)

        # Token usage tracking
        self._token_tracker = TokenUsageTracker()

    @abstractmethod
    def get_completion(
        self,
        prompt: str = "",
        thinking: bool = False,
        tools: Optional[List[Dict[str, Any]]] = None,
        tool_choice: Optional[str] = None,
        messages: Optional[List[Dict[str, Any]]] = None,
    ) -> Union[str, VLMResponse]:
        """Get text completion

        Args:
            prompt: Text prompt (used if messages not provided)
            thinking: Whether to enable thinking mode
            tools: Optional list of tool definitions in OpenAI function format
            tool_choice: Optional tool choice mode ("auto", "none", or specific tool name)
            messages: Optional list of message dicts (takes precedence over prompt)

        Returns:
            str if no tools provided, VLMResponse if tools provided
        """
        pass

    @abstractmethod
    async def get_completion_async(
        self,
        prompt: str = "",
        thinking: bool = False,
        tools: Optional[List[Dict[str, Any]]] = None,
        tool_choice: Optional[str] = None,
        messages: Optional[List[Dict[str, Any]]] = None,
    ) -> Union[str, VLMResponse]:
        """Get text completion asynchronously

        Args:
            prompt: Text prompt (used if messages not provided)
            thinking: Whether to enable thinking mode
            tools: Optional list of tool definitions in OpenAI function format
            tool_choice: Optional tool choice mode ("auto", "none", or specific tool name)
            messages: Optional list of message dicts (takes precedence over prompt)

        Returns:
            str if no tools provided, VLMResponse if tools provided
        """
        pass

    @abstractmethod
    def get_vision_completion(
        self,
        prompt: str = "",
        images: Optional[List[Union[str, Path, bytes]]] = None,
        thinking: bool = False,
        tools: Optional[List[Dict[str, Any]]] = None,
        tool_choice: Optional[str] = None,
        messages: Optional[List[Dict[str, Any]]] = None,
    ) -> Union[str, VLMResponse]:
        """Get vision completion

        Args:
            prompt: Text prompt (used if messages not provided)
            images: List of images (used if messages not provided)
            thinking: Whether to enable thinking mode
            tools: Optional list of tool definitions in OpenAI function format
            tool_choice: Optional tool choice mode ("auto", "none", or specific tool name)
            messages: Optional list of message dicts (takes precedence over prompt/images)

        Returns:
            str if no tools provided, VLMResponse if tools provided
        """
        pass

    @abstractmethod
    async def get_vision_completion_async(
        self,
        prompt: str = "",
        images: Optional[List[Union[str, Path, bytes]]] = None,
        thinking: bool = False,
        tools: Optional[List[Dict[str, Any]]] = None,
        tool_choice: Optional[str] = None,
        messages: Optional[List[Dict[str, Any]]] = None,
    ) -> Union[str, VLMResponse]:
        """Get vision completion asynchronously

        Args:
            prompt: Text prompt (used if messages not provided)
            images: List of images (used if messages not provided)
            thinking: Whether to enable thinking mode
            tools: Optional list of tool definitions in OpenAI function format
            tool_choice: Optional tool choice mode ("auto", "none", or specific tool name)
            messages: Optional list of message dicts (takes precedence over prompt/images)

        Returns:
            str if no tools provided, VLMResponse if tools provided
        """
        pass

    def _clean_response(self, content: str) -> str:
        """Strip reasoning tags (e.g. ``<think>...</think>``) from model output."""
        return _THINK_TAG_RE.sub("", content).strip()

    def is_available(self) -> bool:
        """Check if available"""
        return self.api_key is not None or self.api_base is not None

    # Token usage tracking methods
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
            provider: Provider name (openai, volcengine)
            prompt_tokens: Number of prompt tokens
            completion_tokens: Number of completion tokens
            duration_seconds: Wall-clock duration of the VLM call in seconds
        """
        self._token_tracker.update(
            model_name=model_name,
            provider=provider,
            prompt_tokens=prompt_tokens,
            completion_tokens=completion_tokens,
        )
        # Operation-level telemetry aggregation (no-op when telemetry is disabled).
        try:
            from openviking.telemetry import get_current_telemetry, get_current_telemetry_stage

            get_current_telemetry().add_token_usage(
                prompt_tokens,
                completion_tokens,
                stage=get_current_telemetry_stage() or "vlm",
            )
        except Exception as e:
            # Telemetry must never break model inference.
            if logger.isEnabledFor(logging.DEBUG):
                logger.debug(
                    "vlm.update_token_usage telemetry emit failed provider=%s model_name=%s err=%s: %s",
                    provider,
                    model_name,
                    type(e).__name__,
                    e,
                )

        # Record the VLM call in Prometheus metrics (if enabled).
        try:
            from openviking.metrics.account_context import get_metric_account_context
            from openviking.metrics.datasources import VLMEventDataSource

            VLMEventDataSource.record_call(
                provider=str(provider),
                model_name=str(model_name),
                duration_seconds=float(duration_seconds),
                prompt_tokens=int(prompt_tokens),
                completion_tokens=int(completion_tokens),
                account_id=get_metric_account_context().http_account_id,
            )
        except Exception as e:
            # Metrics must never break model inference.
            if logger.isEnabledFor(logging.DEBUG):
                logger.debug(
                    "vlm.update_token_usage metrics emit failed provider=%s model_name=%s err=%s: %s",
                    provider,
                    model_name,
                    type(e).__name__,
                    e,
                )

    def get_token_usage(self) -> Dict[str, Any]:
        """Get token usage

        Returns:
            Dict[str, Any]: Token usage dictionary
        """
        return self._token_tracker.to_dict()

    def get_token_usage_summary(self) -> Dict[str, Any]:
        """Get token usage summary

        Returns:
            Dict[str, Any]: Token usage summary
        """
        total_usage = self._token_tracker.get_total_usage()
        return {
            "total_prompt_tokens": total_usage.prompt_tokens,
            "total_completion_tokens": total_usage.completion_tokens,
            "total_tokens": total_usage.total_tokens,
            "last_updated": format_iso8601(total_usage.last_updated),
        }

    def reset_token_usage(self) -> None:
        """Reset token usage"""
        self._token_tracker.reset()

    def _extract_content_from_response(self, response) -> str:
        if isinstance(response, str):
            return response
        return response.choices[0].message.content or ""


class VLMFactory:
    """VLM factory class, creates corresponding VLM instance based on config"""

    @staticmethod
    def create(config: Dict[str, Any]) -> VLMBase:
        """Create VLM instance

        Args:
            config: VLM config, must contain 'provider' field

        Returns:
            VLMBase: VLM instance

        Raises:
            ValueError: If provider is not supported
            ImportError: If related dependencies are not installed
        """
        provider = (config.get("provider") or config.get("backend") or "openai").lower()

        if provider == "volcengine":
            from .backends.volcengine_vlm import VolcEngineVLM

            return VolcEngineVLM(config)

        elif provider in ("openai", "azure"):
            from .backends.openai_vlm import OpenAIVLM

            return OpenAIVLM(config)

        else:
            from .backends.litellm_vlm import LiteLLMVLMProvider

            return LiteLLMVLMProvider(config)

    @staticmethod
    def get_available_providers() -> List[str]:
        """Get list of available providers"""
        from .registry import get_all_provider_names

        return get_all_provider_names()
