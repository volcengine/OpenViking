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
3. Translating raw `client.responses.create(stream=True)` events back into a
   format compatible with standard Chat Completion responses.
"""

from __future__ import annotations

import asyncio
import base64
import hashlib
import hmac
import json
import secrets
import threading
from dataclasses import replace
from types import SimpleNamespace
from typing import Any, Dict, List, Optional

import httpx

try:
    import openai
except ImportError:
    openai = None

from ..base import VLMResponse
from .codex_auth import (
    resolve_codex_runtime_credentials,
    resolve_codex_runtime_credentials_async,
    validate_codex_base_url,
)
from .codex_responses_adapter import (
    CodexAsyncChatShim,
    CodexAsyncCompletionsAdapter,
    CodexChatShim,
    CodexCompletionsAdapter,
    CodexResponsesLimits,
    CodexResponsesState,
    CodexResponsesTurn,
    CodexStateBindingError,
    CodexStateValidationError,
)
from .openai_vlm import OpenAIVLM, _build_openai_client_kwargs


class CodexVLM(OpenAIVLM):
    def __init__(self, config: Dict[str, Any]):
        normalized = dict(config)
        normalized["provider"] = "openai-codex"
        normalized["api_base"] = validate_codex_base_url(normalized.get("api_base"))
        super().__init__(normalized)
        self._async_client = None
        self._state_adapter = None
        self._state_async_adapter = None
        self._state_adapter_lock = threading.Lock()

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

    def _build_async_responses_client(self, api_key: str, api_base: str):
        kwargs = _build_openai_client_kwargs(
            "openai",
            api_key,
            api_base,
            self.api_version,
            self.extra_headers,
            self.timeout,
        )
        return openai.AsyncOpenAI(**kwargs)

    def _build_state_responses_client(self, api_key: str, api_base: str):
        kwargs = _build_openai_client_kwargs(
            "openai",
            api_key,
            api_base,
            self.api_version,
            None,
            self.timeout,
        )
        kwargs["http_client"] = httpx.Client(
            follow_redirects=False,
            timeout=self.timeout,
        )
        return openai.OpenAI(**kwargs)

    def _build_async_state_responses_client(self, api_key: str, api_base: str):
        kwargs = _build_openai_client_kwargs(
            "openai",
            api_key,
            api_base,
            self.api_version,
            None,
            self.timeout,
        )
        kwargs["http_client"] = httpx.AsyncClient(
            follow_redirects=False,
            timeout=self.timeout,
        )
        return openai.AsyncOpenAI(**kwargs)

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
            self.api_base = validate_codex_base_url(explicit_api_base)
            return self.api_key, self.api_base
        credentials = resolve_codex_runtime_credentials()
        self.api_key = credentials["api_key"]
        self.api_base = validate_codex_base_url(explicit_api_base or credentials["base_url"])
        return self.api_key, self.api_base

    def _build_text_kwargs(self, *args, **kwargs):
        request_kwargs = super()._build_text_kwargs(*args, **kwargs)
        if self.stream:
            request_kwargs["stream"] = True
        return request_kwargs

    def _build_vision_kwargs(self, *args, **kwargs):
        request_kwargs = super()._build_vision_kwargs(*args, **kwargs)
        if self.stream:
            request_kwargs["stream"] = True
        return request_kwargs

    def get_client(self):
        if openai is None:
            raise ImportError("Please install openai: pip install openai")
        return self._get_or_create_sync_responses_client()

    def get_async_client(self):
        if openai is None:
            raise ImportError("Please install openai: pip install openai")
        return self._get_or_create_async_responses_client()

    def _get_or_create_state_adapters(
        self,
    ) -> tuple[CodexCompletionsAdapter, CodexAsyncCompletionsAdapter]:
        with self._state_adapter_lock:
            return self._initialize_state_adapters()

    def _initialize_state_adapters(
        self,
    ) -> tuple[CodexCompletionsAdapter, CodexAsyncCompletionsAdapter]:
        if not self.config.get("responses_state_enabled", False):
            raise CodexStateValidationError(
                "Codex Responses state mode is not enabled for this instance."
            )
        if self.extra_headers or self.extra_request_body:
            raise CodexStateValidationError(
                "Codex Responses state pilot forbids custom request fields."
            )
        if self._state_adapter is not None and self._state_async_adapter is not None:
            return self._state_adapter, self._state_async_adapter

        api_key, api_base, principal_binding, credential_binding = (
            self._resolve_state_runtime_credentials()
        )
        state_key = secrets.token_bytes(32)

        def fingerprint(label: str, value: str) -> str:
            digest = hmac.new(
                state_key,
                f"{label}:{value}".encode("utf-8"),
                hashlib.sha256,
            ).hexdigest()
            return f"{label}:v1:{digest}"

        credential_fingerprint = fingerprint("credential", credential_binding)
        principal_fingerprint = fingerprint("principal", principal_binding)

        def verify_credentials(
            current: tuple[str, str, str, str],
        ) -> tuple[str, str]:
            current_key, current_base, current_principal, current_credential = current
            if (
                fingerprint("credential", current_credential) != credential_fingerprint
                or fingerprint("principal", current_principal) != principal_fingerprint
                or current_base.rstrip("/") != api_base.rstrip("/")
            ):
                raise CodexStateBindingError("Codex state credential or origin binding changed.")
            return current_key, current_base

        def checked_credentials() -> tuple[str, str]:
            return verify_credentials(self._resolve_state_runtime_credentials())

        def sync_factory():
            return self._build_state_responses_client(*checked_credentials())

        async def async_factory():
            current = await self._resolve_state_runtime_credentials_async()
            return self._build_async_state_responses_client(*verify_credentials(current))

        limits = CodexResponsesLimits(
            max_bytes=int(self.config.get("responses_state_max_bytes", 32 * 1024 * 1024)),
            max_items=int(self.config.get("responses_state_max_items", 4096)),
            max_turns=int(self.config.get("responses_state_max_turns", 256)),
            max_images=int(self.config.get("responses_state_max_images", 8)),
            max_image_bytes=int(
                self.config.get("responses_state_max_image_bytes", 8 * 1024 * 1024)
            ),
            max_tool_output_bytes=int(
                self.config.get("responses_state_max_tool_output_bytes", 1024 * 1024)
            ),
            max_total_tool_output_bytes=int(
                self.config.get("responses_state_max_total_tool_output_bytes", 4 * 1024 * 1024)
            ),
            max_tool_call_ids=int(self.config.get("responses_state_max_tool_call_ids", 4096)),
            max_tool_call_id_bytes=int(
                self.config.get("responses_state_max_tool_call_id_bytes", 512)
            ),
            ttl_seconds=int(self.config.get("responses_state_ttl_seconds", 3600)),
            max_concurrent_chains=int(self.config.get("responses_state_max_concurrent_chains", 16)),
        )
        compact_threshold = self.config.get("responses_compact_threshold")
        self._state_adapter = CodexCompletionsAdapter(
            sync_factory,
            self.model or "gpt-5.3-codex",
            async_client_factory=async_factory,
            state_integrity_key=state_key,
            origin=api_base,
            principal_fingerprint=principal_fingerprint,
            credential_fingerprint=credential_fingerprint,
            state_limits=limits,
            responses_compact_threshold=(
                int(compact_threshold) if compact_threshold is not None else None
            ),
            responses_sdk_version=str(getattr(openai, "__version__", "unknown")),
        )
        self._state_async_adapter = CodexAsyncCompletionsAdapter(self._state_adapter)
        return self._state_adapter, self._state_async_adapter

    @staticmethod
    def _stable_oauth_principal(access_token: str) -> str:
        if access_token.count(".") != 2:
            return ""
        payload = access_token.split(".", 2)[1]
        payload += "=" * ((4 - len(payload) % 4) % 4)
        try:
            claims = json.loads(base64.urlsafe_b64decode(payload.encode("ascii")).decode("utf-8"))
        except Exception:
            return ""
        if not isinstance(claims, dict):
            return ""
        auth_claims = claims.get("https://api.openai.com/auth")
        identity = {
            "iss": claims.get("iss"),
            "sub": claims.get("sub"),
            "account_id": claims.get("account_id"),
            "chatgpt_account_id": (
                auth_claims.get("chatgpt_account_id") if isinstance(auth_claims, dict) else None
            ),
            "client_id": claims.get("client_id") or claims.get("azp"),
        }
        stable = {
            key: str(value).strip()
            for key, value in identity.items()
            if isinstance(value, str) and value.strip()
        }
        if not any(stable.get(key) for key in ("sub", "account_id", "chatgpt_account_id")):
            return ""
        return json.dumps(
            stable,
            sort_keys=True,
            separators=(",", ":"),
        )

    @staticmethod
    def _oauth_state_bindings(
        credentials: Dict[str, Any],
        explicit_api_base: str,
    ) -> tuple[str, str, str, str]:
        access_token = str(credentials["api_key"])
        principal = CodexVLM._stable_oauth_principal(access_token)
        slot = str(credentials.get("credential_slot") or credentials.get("path") or "").strip()
        client_id = str(credentials.get("client_id", "") or "").strip()
        if not principal:
            raise CodexStateBindingError("Codex OAuth state requires stable principal claims.")
        if not slot:
            raise CodexStateBindingError("Codex OAuth state requires a stable credential slot.")
        credential_slot = json.dumps(
            {
                "client_id": client_id or None,
                "slot": slot,
            },
            sort_keys=True,
            separators=(",", ":"),
        )
        return (
            access_token,
            explicit_api_base or str(credentials["base_url"]),
            principal,
            credential_slot,
        )

    def _resolve_state_runtime_credentials(self) -> tuple[str, str, str, str]:
        explicit_api_key = str(self.config.get("api_key", "") or "").strip()
        explicit_api_base = str(self.config.get("api_base", "") or "").strip().rstrip("/")
        if explicit_api_key:
            base_url = validate_codex_base_url(explicit_api_base)
            return (
                explicit_api_key,
                base_url,
                f"explicit:{explicit_api_key}",
                f"explicit:{explicit_api_key}",
            )

        return self._oauth_state_bindings(
            resolve_codex_runtime_credentials(),
            explicit_api_base,
        )

    async def _resolve_state_runtime_credentials_async(
        self,
    ) -> tuple[str, str, str, str]:
        explicit_api_key = str(self.config.get("api_key", "") or "").strip()
        explicit_api_base = str(self.config.get("api_base", "") or "").strip().rstrip("/")
        if explicit_api_key:
            base_url = validate_codex_base_url(explicit_api_base)
            return (
                explicit_api_key,
                base_url,
                f"explicit:{explicit_api_key}",
                f"explicit:{explicit_api_key}",
            )
        return self._oauth_state_bindings(
            await resolve_codex_runtime_credentials_async(),
            explicit_api_base,
        )

    def _project_state_turn(
        self,
        turn: CodexResponsesTurn[Any],
        *,
        has_tools: bool,
    ) -> CodexResponsesTurn[Any]:
        usage_value = getattr(turn.result, "usage", None)
        if usage_value is not None:
            prompt_details = getattr(usage_value, "prompt_tokens_details", None)
            completion_details = getattr(
                usage_value,
                "completion_tokens_details",
                None,
            )
            self.update_token_usage(
                model_name=self.model or "gpt-5.3-codex",
                provider=self.provider,
                prompt_tokens=int(getattr(usage_value, "prompt_tokens", 0) or 0),
                completion_tokens=int(getattr(usage_value, "completion_tokens", 0) or 0),
                prompt_cached_tokens=int(getattr(prompt_details, "cached_tokens", 0) or 0),
                completion_reasoning_tokens=int(
                    getattr(completion_details, "reasoning_tokens", 0) or 0
                ),
            )
        if has_tools:
            choice = turn.result.choices[0]
            message = choice.message
            usage = {}
            if usage_value is not None:
                usage = {
                    "prompt_tokens": usage_value.prompt_tokens,
                    "completion_tokens": usage_value.completion_tokens,
                    "total_tokens": usage_value.total_tokens,
                    "prompt_tokens_details": getattr(
                        usage_value,
                        "prompt_tokens_details",
                        None,
                    ),
                }
            result = VLMResponse(
                content=message.content,
                tool_calls=self._parse_tool_calls(message),
                finish_reason=choice.finish_reason or "stop",
                usage=usage,
            )
        else:
            result = self._clean_response(self._extract_content_from_response(turn.result))
        return replace(turn, result=result)

    def get_completion_with_state(
        self,
        prompt: str = "",
        *,
        state: Optional[CodexResponsesState] = None,
        expected_generation: Optional[int] = None,
        instructions: Optional[str] = None,
        tools: Optional[List[Dict[str, Any]]] = None,
        tool_choice: Optional[str] = None,
        messages: Optional[List[Dict[str, Any]]] = None,
    ) -> CodexResponsesTurn[Any]:
        """Run one opt-in, caller-managed Codex Responses turn without retries."""
        adapter, _async_adapter = self._get_or_create_state_adapters()
        turn = adapter.create_with_state(
            state=state,
            expected_generation=expected_generation,
            model=self.model or "gpt-5.3-codex",
            instructions=instructions
            or str(self.config.get("responses_instructions", "You are a helpful assistant.")),
            messages=messages or [{"role": "user", "content": prompt}],
            tools=tools,
            tool_choice=tool_choice,
        )
        has_tools = bool(tools or getattr(turn.result.choices[0].message, "tool_calls", None))
        return self._project_state_turn(turn, has_tools=has_tools)

    def probe_responses_compaction_capability(
        self,
        *,
        probe_input: List[Dict[str, Any]],
    ) -> None:
        """Explicitly run the endpoint-bound, potentially billable probe."""
        adapter, _async_adapter = self._get_or_create_state_adapters()
        adapter.probe_compaction_capability(probe_input=probe_input)

    async def get_completion_with_state_async(
        self,
        prompt: str = "",
        *,
        state: Optional[CodexResponsesState] = None,
        expected_generation: Optional[int] = None,
        instructions: Optional[str] = None,
        tools: Optional[List[Dict[str, Any]]] = None,
        tool_choice: Optional[str] = None,
        messages: Optional[List[Dict[str, Any]]] = None,
    ) -> CodexResponsesTurn[Any]:
        """Run one opt-in Codex Responses turn on a native async stream."""
        _adapter, async_adapter = await asyncio.to_thread(self._get_or_create_state_adapters)
        turn = await async_adapter.create_with_state(
            state=state,
            expected_generation=expected_generation,
            model=self.model or "gpt-5.3-codex",
            instructions=instructions
            or str(self.config.get("responses_instructions", "You are a helpful assistant.")),
            messages=messages or [{"role": "user", "content": prompt}],
            tools=tools,
            tool_choice=tool_choice,
        )
        has_tools = bool(tools or getattr(turn.result.choices[0].message, "tool_calls", None))
        return self._project_state_turn(turn, has_tools=has_tools)

    def is_available(self) -> bool:
        if str(self.config.get("api_key", "") or "").strip():
            return True
        try:
            resolve_codex_runtime_credentials(refresh_if_expiring=False)
        except Exception:
            return False
        return True
