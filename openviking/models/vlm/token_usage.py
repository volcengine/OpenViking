# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: Apache-2.0
"""VLM Token usage monitoring data structures"""

from dataclasses import dataclass, field
from datetime import datetime
from typing import Dict, Optional

from openviking.utils.time_utils import format_iso8601


@dataclass
class TokenUsage:
    """Token usage statistics"""

    prompt_tokens: int = 0
    completion_tokens: int = 0
    total_tokens: int = 0
    last_updated: datetime = field(default_factory=datetime.now)

    def update(self, prompt_tokens: int, completion_tokens: int) -> None:
        """Update token usage

        Args:
            prompt_tokens: Number of input tokens
            completion_tokens: Number of output tokens
        """
        self.prompt_tokens += prompt_tokens
        self.completion_tokens += completion_tokens
        self.total_tokens = self.prompt_tokens + self.completion_tokens
        self.last_updated = datetime.now()

    def reset(self) -> None:
        """Reset token usage statistics"""
        self.prompt_tokens = 0
        self.completion_tokens = 0
        self.total_tokens = 0
        self.last_updated = datetime.now()

    def to_dict(self) -> Dict:
        """Convert to dictionary format

        Returns:
            Token usage dictionary
        """
        return {
            "prompt_tokens": self.prompt_tokens,
            "completion_tokens": self.completion_tokens,
            "total_tokens": self.total_tokens,
            "last_updated": format_iso8601(self.last_updated),
        }

    def __str__(self) -> str:
        return (
            f"TokenUsage(prompt={self.prompt_tokens}, "
            f"completion={self.completion_tokens}, "
            f"total={self.total_tokens})"
        )


@dataclass
class ModelTokenUsage:
    """Token usage statistics by model"""

    model_name: str
    total_usage: TokenUsage = field(default_factory=TokenUsage)
    usage_by_provider: Dict[str, TokenUsage] = field(default_factory=dict)

    def update(self, provider: str, prompt_tokens: int, completion_tokens: int) -> None:
        """Update token usage for specified provider

        Args:
            provider: Provider name (openai, volcengine)
            prompt_tokens: Number of input tokens
            completion_tokens: Number of output tokens
        """
        # Update total usage
        self.total_usage.update(prompt_tokens, completion_tokens)

        # Update provider usage
        if provider not in self.usage_by_provider:
            self.usage_by_provider[provider] = TokenUsage()

        self.usage_by_provider[provider].update(prompt_tokens, completion_tokens)

    def get_provider_usage(self, provider: str) -> Optional[TokenUsage]:
        """Get token usage for specified provider

        Args:
            provider: Provider name

        Returns:
            TokenUsage object, or None if provider doesn't exist
        """
        return self.usage_by_provider.get(provider)

    def to_dict(self) -> Dict:
        """Convert to dictionary format

        Returns:
            Token usage statistics in dictionary format
        """
        result = {
            "model_name": self.model_name,
            "total_usage": self.total_usage.to_dict(),
            "usage_by_provider": {},
        }

        for provider, usage in self.usage_by_provider.items():
            result["usage_by_provider"][provider] = usage.to_dict()

        return result

    def __str__(self) -> str:
        providers = ", ".join(
            [
                f"{provider}: {usage.total_tokens}"
                for provider, usage in self.usage_by_provider.items()
            ]
        )
        return f"ModelTokenUsage(model={self.model_name}, total={self.total_usage.total_tokens}, providers=[{providers}])"


class TokenUsageTracker:
    """Token usage tracker"""

    def __init__(self):
        self._usage_by_model: Dict[str, ModelTokenUsage] = {}

    def update(
        self, model_name: str, provider: str, prompt_tokens: int, completion_tokens: int
    ) -> None:
        """Update token usage

        Args:
            model_name: Model name
            provider: Provider name
            prompt_tokens: Number of input tokens
            completion_tokens: Number of output tokens
        """
        if model_name not in self._usage_by_model:
            self._usage_by_model[model_name] = ModelTokenUsage(model_name)

        self._usage_by_model[model_name].update(provider, prompt_tokens, completion_tokens)

    def get_model_usage(self, model_name: str) -> Optional[ModelTokenUsage]:
        """Get token usage for specified model

        Args:
            model_name: Model name

        Returns:
            ModelTokenUsage object, or None if model doesn't exist
        """
        return self._usage_by_model.get(model_name)

    def get_all_usage(self) -> Dict[str, ModelTokenUsage]:
        """Get token usage for all models

        Returns:
            Token usage dictionary by model
        """
        return self._usage_by_model.copy()

    def get_total_usage(self) -> TokenUsage:
        """Get total token usage

        Returns:
            Total token usage statistics
        """
        total = TokenUsage()
        for model_usage in self._usage_by_model.values():
            total.prompt_tokens += model_usage.total_usage.prompt_tokens
            total.completion_tokens += model_usage.total_usage.completion_tokens

            total.total_tokens += model_usage.total_usage.total_tokens

        return total

    def reset(self) -> None:
        """Reset all token usage statistics"""
        self._usage_by_model.clear()

    def to_dict(self) -> Dict:
        """Convert to dictionary format

        Returns:
            Token usage statistics in dictionary format
        """
        result = {
            "total_usage": self.get_total_usage().to_dict(),
            "usage_by_model": {},
        }

        for model_name, model_usage in self._usage_by_model.items():
            result["usage_by_model"][model_name] = model_usage.to_dict()

        return result

    def __str__(self) -> str:
        models = ", ".join(
            [
                f"{model}: {usage.total_usage.total_tokens}"
                for model, usage in self._usage_by_model.items()
            ]
        )
        total = self.get_total_usage()
        return f"TokenUsageTracker(total={total.total_tokens}, models=[{models}])"
