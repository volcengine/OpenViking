# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0
"""Retry decorator for ordinary VLM requests."""

from pathlib import Path
from typing import Any, Dict, List, Optional, Union

from openviking.utils.model_retry import effective_max_retries, retry_async, retry_sync
from openviking_cli.utils import get_logger

from .base import VLMBase, VLMResponse

logger = get_logger(__name__)


class RetryVLM(VLMBase):
    """Own ordinary request retries for one concrete VLM target."""

    def __init__(self, inner: VLMBase):
        if isinstance(inner, RetryVLM):
            raise ValueError("VLM is already wrapped with RetryVLM")
        super().__init__(inner.config)
        self.inner = inner

    def __getattr__(self, name: str) -> Any:
        return getattr(self.inner, name)

    @property
    def token_tracker(self):
        return self.inner.token_tracker

    def get_token_usage(self) -> Dict[str, Any]:
        return self.inner.get_token_usage()

    def reset_token_usage(self) -> None:
        self.inner.reset_token_usage()

    def update_token_usage(self, *args, **kwargs) -> None:
        self.inner.update_token_usage(*args, **kwargs)

    def record_failed_call(self, *, duration_seconds: float, error: Exception) -> None:
        self.inner.record_failed_call(duration_seconds=duration_seconds, error=error)

    def get_completion(
        self,
        prompt: str = "",
        thinking: Optional[bool] = None,
        tools: Optional[List[Dict[str, Any]]] = None,
        tool_choice: Optional[str] = None,
        messages: Optional[List[Dict[str, Any]]] = None,
        **kwargs: Any,
    ) -> Union[str, VLMResponse]:
        return retry_sync(
            lambda: self.inner.get_completion(
                prompt=prompt,
                thinking=thinking,
                tools=tools,
                tool_choice=tool_choice,
                messages=messages,
                **kwargs,
            ),
            max_retries=effective_max_retries(self.max_retries),
            logger=logger,
            operation_name=f"{self.provider} VLM completion",
            model_errors_only=True,
        )

    async def get_completion_async(
        self,
        prompt: str = "",
        thinking: Optional[bool] = None,
        tools: Optional[List[Dict[str, Any]]] = None,
        tool_choice: Optional[str] = None,
        messages: Optional[List[Dict[str, Any]]] = None,
        max_tokens: Optional[int] = None,
        **kwargs: Any,
    ) -> Union[str, VLMResponse]:
        return await retry_async(
            lambda: self.inner.get_completion_async(
                prompt=prompt,
                thinking=thinking,
                tools=tools,
                tool_choice=tool_choice,
                messages=messages,
                max_tokens=max_tokens,
                **kwargs,
            ),
            max_retries=effective_max_retries(self.max_retries),
            logger=logger,
            operation_name=f"{self.provider} VLM async completion",
            model_errors_only=True,
        )

    def get_vision_completion(
        self,
        prompt: str = "",
        images: Optional[List[Union[str, Path, bytes]]] = None,
        thinking: Optional[bool] = None,
        tools: Optional[List[Dict[str, Any]]] = None,
        tool_choice: Optional[str] = None,
        messages: Optional[List[Dict[str, Any]]] = None,
        **kwargs: Any,
    ) -> Union[str, VLMResponse]:
        return retry_sync(
            lambda: self.inner.get_vision_completion(
                prompt=prompt,
                images=images,
                thinking=thinking,
                tools=tools,
                tool_choice=tool_choice,
                messages=messages,
                **kwargs,
            ),
            max_retries=effective_max_retries(self.max_retries),
            logger=logger,
            operation_name=f"{self.provider} VLM vision completion",
            model_errors_only=True,
        )

    async def get_vision_completion_async(
        self,
        prompt: str = "",
        images: Optional[List[Union[str, Path, bytes]]] = None,
        thinking: Optional[bool] = None,
        tools: Optional[List[Dict[str, Any]]] = None,
        tool_choice: Optional[str] = None,
        messages: Optional[List[Dict[str, Any]]] = None,
        **kwargs: Any,
    ) -> Union[str, VLMResponse]:
        return await retry_async(
            lambda: self.inner.get_vision_completion_async(
                prompt=prompt,
                images=images,
                thinking=thinking,
                tools=tools,
                tool_choice=tool_choice,
                messages=messages,
                **kwargs,
            ),
            max_retries=effective_max_retries(self.max_retries),
            logger=logger,
            operation_name=f"{self.provider} VLM async vision completion",
            model_errors_only=True,
        )

    def supports_media(
        self,
        *,
        media_type: str,
        filename: str,
        size_bytes: int,
    ) -> bool:
        return self.inner.supports_media(
            media_type=media_type,
            filename=filename,
            size_bytes=size_bytes,
        )

    async def get_media_completion_async(
        self,
        *,
        prompt: str,
        media_path: Path,
        filename: str,
        media_type: str,
    ) -> str:
        return await self.inner.get_media_completion_async(
            prompt=prompt,
            media_path=media_path,
            filename=filename,
            media_type=media_type,
        )

    def is_available(self) -> bool:
        return self.inner.is_available()

    def close(self) -> None:
        self.inner.close()
