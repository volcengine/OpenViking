# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0
"""VLM base interface and abstract classes"""

import logging
import re
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional, Union

from openviking.utils.exceptions import AllCredentialsFailedError
from openviking.utils.model_retry import (
    OrderedCredentialSwitcher,
    PrimaryBackupSwitcher,
    classify_api_error,
)
from openviking_cli.utils import get_logger

from .token_usage import TokenUsageTracker

_THINK_TAG_RE = re.compile(r"<think>[\s\S]*?</think>")
logger = get_logger(__name__)


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
        self.timeout = config.get("timeout", 600.0)
        self.max_tokens = config.get("max_tokens")
        self.extra_headers = config.get("extra_headers")
        self.extra_request_body = dict(config.get("extra_request_body") or {})
        self.stream = config.get("stream", False)
        self.thinking = config.get("thinking", False)

        # Token usage tracking
        self._token_tracker = TokenUsageTracker()

    @abstractmethod
    def get_completion(
        self,
        prompt: str = "",
        thinking: Optional[bool] = None,
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
        thinking: Optional[bool] = None,
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
        thinking: Optional[bool] = None,
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
        thinking: Optional[bool] = None,
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
        prompt_cached_tokens: int = 0,
        completion_reasoning_tokens: int = 0,
    ) -> None:
        """Update token usage

        Args:
            model_name: Model name
            provider: Provider name (openai, volcengine)
            prompt_tokens: Number of prompt tokens
            completion_tokens: Number of completion tokens
            duration_seconds: Wall-clock duration of the VLM call in seconds
            prompt_cached_tokens: Number of cached prompt tokens from provider usage details
            completion_reasoning_tokens: Number of reasoning completion tokens from provider usage details
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

            telemetry = get_current_telemetry()
            stage = get_current_telemetry_stage() or "vlm"
            telemetry.add_token_usage(
                prompt_tokens,
                completion_tokens,
                stage=stage,
                prompt_cached_tokens=max(int(prompt_cached_tokens), 0),
                completion_reasoning_tokens=max(int(completion_reasoning_tokens), 0),
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
            from openviking.metrics.datasources import VLMEventDataSource
            from openviking.observability.context import get_root_observability_context

            root_context = get_root_observability_context()

            VLMEventDataSource.record_call(
                provider=str(provider),
                model_name=str(model_name),
                duration_seconds=float(duration_seconds),
                prompt_tokens=int(prompt_tokens),
                completion_tokens=int(completion_tokens),
                account_id=root_context.account_id if root_context is not None else None,
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

    @property
    def token_tracker(self):
        """Public accessor for this instance's token usage tracker."""
        return self._token_tracker

    def get_token_usage(self) -> Dict[str, Any]:
        """Get token usage

        Returns:
            Dict[str, Any]: Token usage dictionary
        """
        return self._token_tracker.to_dict()

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

        elif provider == "openai-codex":
            from .backends.codex_vlm import CodexVLM

            return CodexVLM(config)

        elif provider == "kimi":
            from .backends.kimi_vlm import KimiVLM

            return KimiVLM(config)

        elif provider == "glm":
            from .backends.glm_vlm import GLMVLM

            return GLMVLM(config)

        else:
            from .backends.litellm_vlm import LiteLLMVLMProvider

            return LiteLLMVLMProvider(config)

def _annotate_vlm_error(exc: Exception, vlm_instance: "VLMBase") -> None:
    """Attach model and api_base info to an exception for better error diagnostics.

    The info is attached as attributes on the exception object so that upstream
    error mapping (e.g. error_mapping.py) can include them in the user-facing
    message, making it clear which model endpoint triggered the failure.
    """
    try:
        if not hasattr(exc, "_vlm_model"):
            model = getattr(vlm_instance, "model", None)
            if model:
                exc._vlm_model = model
        if not hasattr(exc, "_vlm_api_base"):
            api_base = getattr(vlm_instance, "api_base", None)
            if api_base:
                exc._vlm_api_base = api_base
    except Exception:
        # Never let annotation break the original error path
        pass


class FailoverVLM(VLMBase):
    """VLM wrapper that provides failover to a backup VLM instance.

    When the primary VLM instance fails with permanent or quota errors,
    this wrapper will automatically switch to using the backup VLM instance.
    After 10 minutes or 50 requests, it will attempt to failback to primary.
    """

    def __init__(
        self,
        primary: VLMBase,
        backup: VLMBase,
        failback_timeout_seconds: float = 600.0,  # 10 minutes
        failback_request_count: int = 50,
    ):
        """Initialize FailoverVLM with primary and backup VLM instances.

        Args:
            primary: The primary VLM instance to use first
            backup: The backup VLM instance to use when primary fails
            failback_timeout_seconds: Time after which to attempt failback to primary
            failback_request_count: Number of backup requests after which to attempt failback
        """
        # Use a dummy config since we're wrapping existing instances
        config = {
            "model": primary.model,
            "provider": primary.provider,
        }
        super().__init__(config)

        self.primary = primary
        self.backup = backup
        self._logger = logging.getLogger(__name__)
        self._switcher = PrimaryBackupSwitcher(
            failback_timeout_seconds=failback_timeout_seconds,
            failback_request_count=failback_request_count,
        )

    def _get_completion_with_failover(self, method_name: str, *args, **kwargs):
        """Execute a VLM method with failover support.

        Args:
            method_name: Name of the method to call on VLM instances
            *args: Positional arguments to pass to the method
            **kwargs: Keyword arguments to pass to the method

        Returns:
            The result from the VLM method

        Raises:
            The last exception encountered if both primary and backup fail
        """
        last_error = None

        # Try primary if we should
        if self._switcher.should_try_primary():
            try:
                method = getattr(self.primary, method_name)
                result = method(*args, **kwargs)
                self._switcher.record_primary_success()
                return result
            except Exception as e:
                _annotate_vlm_error(e, self.primary)
                last_error = e
                if self._switcher.record_primary_failure(e):
                    # Switched to backup, continue to try backup
                    pass
                else:
                    # Not a failover-worthy error, re-raise
                    raise

        # Try backup
        try:
            self._switcher.record_backup_request()
            method = getattr(self.backup, method_name)
            return method(*args, **kwargs)
        except Exception as e:
            _annotate_vlm_error(e, self.backup)
            last_error = e
            self._logger.error(f"Backup VLM also failed with error: {e}")
            raise last_error

    async def _get_completion_with_failover_async(self, method_name: str, *args, **kwargs):
        """Execute an async VLM method with failover support.

        Args:
            method_name: Name of the async method to call on VLM instances
            *args: Positional arguments to pass to the method
            **kwargs: Keyword arguments to pass to the method

        Returns:
            The result from the async VLM method

        Raises:
            The last exception encountered if both primary and backup fail
        """
        last_error = None

        # Try primary if we should
        if self._switcher.should_try_primary():
            try:
                method = getattr(self.primary, method_name)
                result = await method(*args, **kwargs)
                self._switcher.record_primary_success()
                return result
            except Exception as e:
                _annotate_vlm_error(e, self.primary)
                last_error = e
                if self._switcher.record_primary_failure(e):
                    # Switched to backup, continue to try backup
                    pass
                else:
                    # Not a failover-worthy error, re-raise
                    raise

        # Try backup
        try:
            self._switcher.record_backup_request()
            method = getattr(self.backup, method_name)
            return await method(*args, **kwargs)
        except Exception as e:
            _annotate_vlm_error(e, self.backup)
            last_error = e
            self._logger.error(f"Backup VLM also failed with error: {e}")
            raise last_error

    def get_completion(
        self,
        prompt: str = "",
        thinking: bool = False,
        tools: Optional[List[Dict[str, Any]]] = None,
        tool_choice: Optional[str] = None,
        messages: Optional[List[Dict[str, Any]]] = None,
    ) -> Union[str, VLMResponse]:
        """Get text completion with failover support."""
        return self._get_completion_with_failover(
            "get_completion",
            prompt=prompt,
            thinking=thinking,
            tools=tools,
            tool_choice=tool_choice,
            messages=messages,
        )

    async def get_completion_async(
        self,
        prompt: str = "",
        thinking: bool = False,
        tools: Optional[List[Dict[str, Any]]] = None,
        tool_choice: Optional[str] = None,
        messages: Optional[List[Dict[str, Any]]] = None,
    ) -> Union[str, VLMResponse]:
        """Get text completion asynchronously with failover support."""
        return await self._get_completion_with_failover_async(
            "get_completion_async",
            prompt=prompt,
            thinking=thinking,
            tools=tools,
            tool_choice=tool_choice,
            messages=messages,
        )

    def get_vision_completion(
        self,
        prompt: str = "",
        images: Optional[List[Union[str, Path, bytes]]] = None,
        thinking: bool = False,
        tools: Optional[List[Dict[str, Any]]] = None,
        tool_choice: Optional[str] = None,
        messages: Optional[List[Dict[str, Any]]] = None,
    ) -> Union[str, VLMResponse]:
        """Get vision completion with failover support."""
        return self._get_completion_with_failover(
            "get_vision_completion",
            prompt=prompt,
            images=images,
            thinking=thinking,
            tools=tools,
            tool_choice=tool_choice,
            messages=messages,
        )

    async def get_vision_completion_async(
        self,
        prompt: str = "",
        images: Optional[List[Union[str, Path, bytes]]] = None,
        thinking: bool = False,
        tools: Optional[List[Dict[str, Any]]] = None,
        tool_choice: Optional[str] = None,
        messages: Optional[List[Dict[str, Any]]] = None,
    ) -> Union[str, VLMResponse]:
        """Get vision completion asynchronously with failover support."""
        return await self._get_completion_with_failover_async(
            "get_vision_completion_async",
            prompt=prompt,
            images=images,
            thinking=thinking,
            tools=tools,
            tool_choice=tool_choice,
            messages=messages,
        )

    @property
    def is_using_backup(self) -> bool:
        """Check if currently using the backup VLM instance."""
        return self._switcher.is_using_backup

    @property
    def _active_vlm(self) -> VLMBase:
        """Get the currently active VLM instance."""
        return self.backup if self._switcher.is_using_backup else self.primary

    def update_token_usage(
        self,
        model_name: str,
        provider: str,
        prompt_tokens: int,
        completion_tokens: int,
        duration_seconds: float = 0.0,
    ) -> None:
        """Update token usage for the currently active instance."""
        self._active_vlm.update_token_usage(
            model_name=model_name,
            provider=provider,
            prompt_tokens=prompt_tokens,
            completion_tokens=completion_tokens,
            duration_seconds=duration_seconds,
        )

    def get_token_usage(self) -> Dict[str, Any]:
        """Get combined token usage from both primary and backup instances."""
        from openviking.models.vlm.token_usage import TokenUsageTracker

        merged_tracker = TokenUsageTracker.merge(
            self.primary.token_tracker, self.backup.token_tracker
        )
        return merged_tracker.to_dict()

    def reset_token_usage(self) -> None:
        """Reset token usage for both primary and backup instances."""
        self.primary.reset_token_usage()
        self.backup.reset_token_usage()


class MultiCredentialVLM(VLMBase):
    """VLM wrapper that provides failover across multiple ordered credentials.

    When a credential fails with quota_exceeded or permanent errors, this wrapper
    automatically advances to the next credential in the list. After failback thresholds
    are met, it attempts to move back to a higher-priority credential.

    Credentials are tried in order (index 0 is highest priority).
    """

    def __init__(
        self,
        vlm_instances: List[VLMBase],
        credential_ids: List[str],
        failback_timeout_seconds: float = 600.0,  # 10 minutes
        failback_request_count: int = 50,
    ):
        """Initialize MultiCredentialVLM with multiple VLM instances.

        Args:
            vlm_instances: List of VLM instances in priority order (0 is highest)
            credential_ids: List of credential IDs corresponding to the VLM instances
            failback_timeout_seconds: Time after which to attempt failback
            failback_request_count: Number of requests after which to attempt failback
        """
        if not vlm_instances:
            raise ValueError("At least one VLM instance is required")
        if len(vlm_instances) != len(credential_ids):
            raise ValueError("vlm_instances and credential_ids must have the same length")

        # Use the first instance's config as base
        first = vlm_instances[0]
        config = {
            "model": first.model,
            "provider": first.provider,
        }
        super().__init__(config)

        self._vlm_instances = vlm_instances
        self._credential_ids = credential_ids
        self._logger = logging.getLogger(__name__)
        self._switcher = OrderedCredentialSwitcher(
            n=len(vlm_instances),
            failback_timeout_seconds=failback_timeout_seconds,
            failback_request_count=failback_request_count,
        )

    def _get_completion_with_failover(self, method_name: str, *args, **kwargs):
        """Execute a VLM method with multi-credential failover support.

        Args:
            method_name: Name of the method to call on VLM instances
            *args: Positional arguments to pass to the method
            **kwargs: Keyword arguments to pass to the method

        Returns:
            The result from the VLM method

        Raises:
            AllCredentialsFailedError if all credentials fail
        """
        aggregated_errors = []

        # Start from the current (possibly failed-back) active credential, then
        # cycle through the whole ring once. This means an unavailable active
        # credential (e.g. the last one) does not block the request: a working
        # credential found this cycle serves it and becomes the new active one
        # (fast failover). The slow, sticky failback to higher priority is still
        # handled by maybe_failback() across requests.
        start = self._switcher.maybe_failback()
        n = self._switcher.n

        for offset in range(n):
            idx = (start + offset) % n
            credential_id = self._credential_ids[idx]
            vlm_instance = self._vlm_instances[idx]

            try:
                method = getattr(vlm_instance, method_name)
                result = method(*args, **kwargs)
                self._switcher.commit_success(idx)
                return result
            except Exception as exc:
                _annotate_vlm_error(exc, vlm_instance)
                error_class = classify_api_error(exc)
                aggregated_errors.append((credential_id, error_class, exc, idx))

                if self._switcher.is_fail_fast(error_class):
                    # Request-level failure (400 / input too large / content
                    # safety): re-raise the original exception so callers can
                    # react to its type; trying other credentials is useless.
                    raise

                self._logger.warning(
                    f"Credential {credential_id} failed with {error_class}, trying next credential"
                )

        raise AllCredentialsFailedError(aggregated_errors)

    async def _get_completion_with_failover_async(self, method_name: str, *args, **kwargs):
        """Execute an async VLM method with multi-credential failover support.

        Args:
            method_name: Name of the async method to call on VLM instances
            *args: Positional arguments to pass to the method
            **kwargs: Keyword arguments to pass to the method

        Returns:
            The result from the async VLM method

        Raises:
            AllCredentialsFailedError if all credentials fail
        """
        aggregated_errors = []

        # See the sync variant for the ring-traversal rationale.
        start = self._switcher.maybe_failback()
        n = self._switcher.n

        for offset in range(n):
            idx = (start + offset) % n
            credential_id = self._credential_ids[idx]
            vlm_instance = self._vlm_instances[idx]

            try:
                method = getattr(vlm_instance, method_name)
                result = await method(*args, **kwargs)
                self._switcher.commit_success(idx)
                return result
            except Exception as exc:
                _annotate_vlm_error(exc, vlm_instance)
                error_class = classify_api_error(exc)
                aggregated_errors.append((credential_id, error_class, exc, idx))

                if self._switcher.is_fail_fast(error_class):
                    # Request-level failure: re-raise the original exception;
                    # trying other credentials is useless.
                    raise

                self._logger.warning(
                    f"Credential {credential_id} failed with {error_class}, trying next credential"
                )

        raise AllCredentialsFailedError(aggregated_errors)

    def get_completion(
        self,
        prompt: str = "",
        thinking: bool = False,
        tools: Optional[List[Dict[str, Any]]] = None,
        tool_choice: Optional[str] = None,
        messages: Optional[List[Dict[str, Any]]] = None,
    ) -> Union[str, VLMResponse]:
        """Get text completion with multi-credential failover support."""
        return self._get_completion_with_failover(
            "get_completion",
            prompt=prompt,
            thinking=thinking,
            tools=tools,
            tool_choice=tool_choice,
            messages=messages,
        )

    async def get_completion_async(
        self,
        prompt: str = "",
        thinking: bool = False,
        tools: Optional[List[Dict[str, Any]]] = None,
        tool_choice: Optional[str] = None,
        messages: Optional[List[Dict[str, Any]]] = None,
    ) -> Union[str, VLMResponse]:
        """Get text completion asynchronously with multi-credential failover support."""
        return await self._get_completion_with_failover_async(
            "get_completion_async",
            prompt=prompt,
            thinking=thinking,
            tools=tools,
            tool_choice=tool_choice,
            messages=messages,
        )

    def get_vision_completion(
        self,
        prompt: str = "",
        images: Optional[List[Union[str, Path, bytes]]] = None,
        thinking: bool = False,
        tools: Optional[List[Dict[str, Any]]] = None,
        tool_choice: Optional[str] = None,
        messages: Optional[List[Dict[str, Any]]] = None,
    ) -> Union[str, VLMResponse]:
        """Get vision completion with multi-credential failover support."""
        return self._get_completion_with_failover(
            "get_vision_completion",
            prompt=prompt,
            images=images,
            thinking=thinking,
            tools=tools,
            tool_choice=tool_choice,
            messages=messages,
        )

    async def get_vision_completion_async(
        self,
        prompt: str = "",
        images: Optional[List[Union[str, Path, bytes]]] = None,
        thinking: bool = False,
        tools: Optional[List[Dict[str, Any]]] = None,
        tool_choice: Optional[str] = None,
        messages: Optional[List[Dict[str, Any]]] = None,
    ) -> Union[str, VLMResponse]:
        """Get vision completion asynchronously with multi-credential failover support."""
        return await self._get_completion_with_failover_async(
            "get_vision_completion_async",
            prompt=prompt,
            images=images,
            thinking=thinking,
            tools=tools,
            tool_choice=tool_choice,
            messages=messages,
        )

    @property
    def active_credential_id(self) -> str:
        """Get the ID of the currently active credential."""
        idx = self._switcher.get_active_index()
        if idx < len(self._credential_ids):
            return self._credential_ids[idx]
        return "exhausted"

    def get_token_usage(self) -> Dict[str, Any]:
        """Get combined token usage from all credential instances."""
        from openviking.models.vlm.token_usage import TokenUsageTracker

        if not self._vlm_instances:
            return {}

        merged_tracker = self._vlm_instances[0].token_tracker
        for instance in self._vlm_instances[1:]:
            merged_tracker = TokenUsageTracker.merge(merged_tracker, instance.token_tracker)

        return merged_tracker.to_dict()

    def reset_token_usage(self) -> None:
        """Reset token usage for all credential instances."""
        for instance in self._vlm_instances:
            instance.reset_token_usage()
