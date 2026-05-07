# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0

"""
Codex VLM Backend Integration

This module implements the integration with the Codex provider for Vision-Language Models (VLM).
Unlike standard OpenAI API billing endpoints which use the Chat Completions API, Codex's
subscription-based endpoints process multimodal (vision/VLM) requests primarily through
the auxiliary Responses API (`client.responses`).

The complexity in this file arises from the need to shim/adapt standard Chat Completions
requests (used by OpenViking) into Responses API requests. This involves:
1. Converting `text` and `image_url` parts into `input_text` and `input_image`.
2. Adapting tool calls and schemas.
3. Translating the `client.responses.stream` event stream back into a format
   compatible with standard Chat Completion responses.
"""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any, Dict

try:
    import openai
except ImportError:
    openai = None

from .codex_auth import DEFAULT_CODEX_BASE_URL, resolve_codex_runtime_credentials
from .codex_responses_adapter import (
    CodexAsyncChatShim,
    CodexAsyncCompletionsAdapter,
    CodexChatShim,
    CodexCompletionsAdapter,
)
from .openai_vlm import OpenAIVLM, _build_openai_client_kwargs


class CodexVLM(OpenAIVLM):
    def __init__(self, config: Dict[str, Any]):
        normalized = dict(config)
        normalized["provider"] = "openai-codex"
        if not normalized.get("api_base"):
            normalized["api_base"] = DEFAULT_CODEX_BASE_URL
        super().__init__(normalized)

    def _build_responses_client(self, api_key: str, api_base: str):
        kwargs = _build_openai_client_kwargs(
            "openai",
            api_key,
            api_base,
            self.api_version,
            self.extra_headers,
            self.timeout,
        )
        return openai.OpenAI(**kwargs)

    def _get_or_create_sync_responses_client(self):
        if self._sync_client is None:
            adapter = CodexCompletionsAdapter(
                lambda: self._build_responses_client(*self._resolve_runtime_credentials()),
                self.model or "gpt-5.3-codex",
            )
            self._sync_client = SimpleNamespace(chat=CodexChatShim(adapter))
        return self._sync_client

    def _get_or_create_async_responses_client(self):
        # The async path uses a sync Responses client behind asyncio.to_thread so
        # credential refresh and auth-store I/O do not block the event loop.
        if self._async_client is None:
            sync_adapter = CodexCompletionsAdapter(
                lambda: self._build_responses_client(*self._resolve_runtime_credentials()),
                self.model or "gpt-5.3-codex",
            )
            self._async_client = SimpleNamespace(
                chat=CodexAsyncChatShim(CodexAsyncCompletionsAdapter(sync_adapter))
            )
        return self._async_client

    def _resolve_runtime_credentials(self) -> tuple[str, str]:
        explicit_api_key = str(self.config.get("api_key", "") or "").strip()
        explicit_api_base = str(self.config.get("api_base", "") or "").strip().rstrip("/")
        if explicit_api_key:
            self.api_key = explicit_api_key
            self.api_base = explicit_api_base or DEFAULT_CODEX_BASE_URL
            return self.api_key, self.api_base
        credentials = resolve_codex_runtime_credentials()
        self.api_key = credentials["api_key"]
        self.api_base = explicit_api_base or credentials["base_url"]
        return self.api_key, self.api_base

    def get_client(self):
        if openai is None:
            raise ImportError("Please install openai: pip install openai")
        return self._get_or_create_sync_responses_client()

    def get_async_client(self):
        if openai is None:
            raise ImportError("Please install openai: pip install openai")
        return self._get_or_create_async_responses_client()

    def is_available(self) -> bool:
        if str(self.config.get("api_key", "") or "").strip():
            return True
        try:
            resolve_codex_runtime_credentials(refresh_if_expiring=False)
        except Exception:
            return False
        return True
