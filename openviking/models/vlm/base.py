# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: Apache-2.0
"""VLM base interface and abstract classes"""

from abc import ABC, abstractmethod
from pathlib import Path
from typing import Any, Dict, List, Union

from openviking.utils.time_utils import format_iso8601

from .token_usage import TokenUsageTracker


class VLMBase(ABC):
    """VLM base abstract class"""

    def __init__(self, config: Dict[str, Any]):
        self.config = config
        self.provider = config.get("provider", "openai")
        self.model = config.get("model")
        self.api_key = config.get("api_key")
        self.api_base = config.get("api_base")
        self.temperature = config.get("temperature", 0.0)
        self.max_retries = config.get("max_retries", 2)

        # Token usage tracking
        self._token_tracker = TokenUsageTracker()

    @abstractmethod
    def get_completion(self, prompt: str, thinking: bool = False) -> str:
        """Get text completion"""
        pass

    @abstractmethod
    async def get_completion_async(
        self, prompt: str, thinking: bool = False, max_retries: int = 0
    ) -> str:
        """Get text completion asynchronously"""
        pass

    @abstractmethod
    def get_vision_completion(
        self,
        prompt: str,
        images: List[Union[str, Path, bytes]],
        thinking: bool = False,
    ) -> str:
        """Get vision completion"""
        pass

    @abstractmethod
    async def get_vision_completion_async(
        self,
        prompt: str,
        images: List[Union[str, Path, bytes]],
        thinking: bool = False,
    ) -> str:
        """Get vision completion asynchronously"""
        pass

    def is_available(self) -> bool:
        """Check if available"""
        return self.api_key is not None or self.api_base is not None

    # Token usage tracking methods
    def update_token_usage(
        self, model_name: str, provider: str, prompt_tokens: int, completion_tokens: int
    ) -> None:
        """Update token usage

        Args:
            model_name: Model name
            provider: Provider name (openai, volcengine)
            prompt_tokens: Number of prompt tokens
            completion_tokens: Number of completion tokens
        """
        self._token_tracker.update(
            model_name=model_name,
            provider=provider,
            prompt_tokens=prompt_tokens,
            completion_tokens=completion_tokens,
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
        provider = config.get("provider") or config.get("backend") or "openai"

        use_litellm = config.get("use_litellm", True)

        if not use_litellm:
            if provider == "openai":
                from .backends.openai_vlm import OpenAIVLM

                return OpenAIVLM(config)
            elif provider == "volcengine":
                from .backends.volcengine_vlm import VolcEngineVLM

                return VolcEngineVLM(config)

        from .backends.litellm_vlm import LiteLLMVLMProvider

        return LiteLLMVLMProvider(config)

    @staticmethod
    def get_available_providers() -> List[str]:
        """Get list of available providers"""
        from .registry import get_all_provider_names

        return get_all_provider_names()
