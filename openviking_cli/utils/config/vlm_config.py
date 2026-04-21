# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0
import importlib
from typing import Any, Dict, List, Optional, Union

from pydantic import BaseModel, Field, model_validator


def _load_codex_auth_module():
    importlib.import_module("openviking.models.vlm")
    return importlib.import_module("openviking.models.vlm.backends.codex_auth")


def _normalize_provider_name(name: Optional[str]) -> Optional[str]:
    if not isinstance(name, str):
        return name
    cleaned = name.strip().lower()
    return cleaned or None


class VLMConfig(BaseModel):
    """VLM configuration, supports multiple provider backends."""

    model: Optional[str] = Field(default=None, description="Model name")
    api_key: Optional[str] = Field(default=None, description="API key")
    api_base: Optional[str] = Field(default=None, description="API base URL")
    temperature: float = Field(default=0.0, description="Generation temperature")
    max_retries: int = Field(default=3, description="Maximum retry attempts")
    timeout: float = Field(
        default=60.0,
        gt=0.0,
        description=(
            "Per-request HTTP timeout in seconds for VLM API calls. Applied to "
            "the underlying OpenAI/Azure/LiteLLM clients. Increase for slow or "
            "high-latency endpoints (e.g., DashScope, local inference servers)."
        ),
    )

    provider: Optional[str] = Field(default=None, description="Provider type")
    backend: Optional[str] = Field(
        default=None, description="Backend provider (Deprecated, use 'provider' instead)"
    )

    providers: Dict[str, Dict[str, Any]] = Field(
        default_factory=dict,
        description="Multi-provider configuration, e.g. {'openai': {'api_key': 'xxx', 'api_base': 'xxx'}}",
    )

    default_provider: Optional[str] = Field(default=None, description="Default provider name")

    max_tokens: Optional[int] = Field(
        default=None,
        description="Maximum tokens for VLM completion output (None = provider default)",
    )

    thinking: bool = Field(default=False, description="Enable thinking mode for VolcEngine models")

    max_concurrent: int = Field(
        default=100, description="Maximum number of concurrent LLM calls for semantic processing"
    )

    api_version: Optional[str] = Field(
        default=None,
        description="API version for Azure OpenAI (e.g., '2025-01-01-preview').",
    )

    extra_headers: Optional[Dict[str, str]] = Field(
        default=None, description="Extra HTTP headers for OpenAI-compatible providers"
    )

    stream: bool = Field(
        default=False, description="Enable streaming mode for OpenAI-compatible providers"
    )

    _vlm_instance: Optional[Any] = None

    model_config = {"arbitrary_types_allowed": True, "extra": "forbid"}

    @model_validator(mode="before")
    @classmethod
    def sync_provider_backend(cls, data: Any) -> Any:
        if isinstance(data, dict):
            provider = data.get("provider")
            backend = data.get("backend")

            if backend is not None and provider is None:
                data["provider"] = backend
            data["provider"] = _normalize_provider_name(data.get("provider"))
            data["backend"] = _normalize_provider_name(data.get("backend"))
            data["default_provider"] = _normalize_provider_name(data.get("default_provider"))
            providers = data.get("providers")
            if isinstance(providers, dict):
                normalized: Dict[str, Dict[str, Any]] = {}
                provider_sources: Dict[str, str] = {}
                for name, config in providers.items():
                    normalized_name = _normalize_provider_name(name) or str(name)
                    existing_name = provider_sources.get(normalized_name)
                    if existing_name is not None and existing_name != str(name):
                        raise ValueError(
                            "Duplicate VLM provider config after normalization: "
                            f"{existing_name} and {name}"
                        )
                    normalized[normalized_name] = config
                    provider_sources[normalized_name] = str(name)
                data["providers"] = normalized
        return data

    @model_validator(mode="after")
    def validate_config(self):
        """Validate configuration completeness and consistency"""
        self._migrate_legacy_config()

        if self._has_any_config():
            if not self.model:
                raise ValueError("VLM configuration requires 'model' to be set")
            provider_name = self._resolve_provider_name()
            if provider_name == "openai-codex":
                has_codex_auth_available = _load_codex_auth_module().has_codex_auth_available

                if not self._get_effective_api_key() and not has_codex_auth_available():
                    raise ValueError(
                        "VLM configuration requires Codex OAuth credentials in ~/.openviking/codex_auth.json or an importable Codex CLI auth file"
                    )
            elif not self._get_effective_api_key():
                raise ValueError("VLM configuration requires 'api_key' to be set")
        return self

    def _migrate_legacy_config(self):
        """Migrate legacy config to providers structure."""
        if self.api_key and self.provider:
            if self.provider not in self.providers:
                self.providers[self.provider] = {}
            if "api_key" not in self.providers[self.provider]:
                self.providers[self.provider]["api_key"] = self.api_key
            if self.api_base and "api_base" not in self.providers[self.provider]:
                self.providers[self.provider]["api_base"] = self.api_base
            if self.extra_headers and "extra_headers" not in self.providers[self.provider]:
                self.providers[self.provider]["extra_headers"] = self.extra_headers
            if self.stream and "stream" not in self.providers[self.provider]:
                self.providers[self.provider]["stream"] = self.stream

    def _has_any_config(self) -> bool:
        """Check if any config is provided."""
        if self.api_key or self.model or self.api_base or self.provider or self.default_provider:
            return True
        if self.providers:
            return True
        return False

    def _get_effective_api_key(self) -> str | None:
        """Get effective API key."""
        if self.api_key:
            return self.api_key
        config, _ = self._match_provider()
        if config and config.get("api_key"):
            return config["api_key"]
        return None

    def _get_provider_config_by_name(self, provider_name: str) -> Dict[str, Any]:
        config = dict(self.providers.get(provider_name) or {})
        if self.api_key and "api_key" not in config:
            config["api_key"] = self.api_key
        if self.api_base and "api_base" not in config:
            config["api_base"] = self.api_base
        if self.extra_headers and "extra_headers" not in config:
            config["extra_headers"] = self.extra_headers
        if self.stream and "stream" not in config:
            config["stream"] = self.stream
        return config

    def _provider_has_usable_credentials(self, provider_name: str, config: Dict[str, Any]) -> bool:
        if config.get("api_key"):
            return True
        if provider_name == "openai-codex":
            has_codex_auth_available = _load_codex_auth_module().has_codex_auth_available

            return has_codex_auth_available()
        return False

    def _match_provider(self, model: str | None = None) -> tuple[Dict[str, Any] | None, str | None]:
        """Match provider config.

        Returns:
            (provider_config_dict, provider_name)
        """
        del model
        if self.provider:
            return self._get_provider_config_by_name(self.provider) or {}, self.provider

        if self.default_provider:
            return (
                self._get_provider_config_by_name(self.default_provider) or {},
                self.default_provider,
            )

        if len(self.providers) == 1:
            provider_name = next(iter(self.providers))
            return self._get_provider_config_by_name(provider_name), provider_name

        for provider_name in self.providers:
            config = self._get_provider_config_by_name(provider_name)
            if self._provider_has_usable_credentials(provider_name, config):
                return config, provider_name

        return None, None

    def _resolve_provider_name(self) -> str | None:
        _, name = self._match_provider()
        return name

    def get_provider_config(
        self, model: str | None = None
    ) -> tuple[Dict[str, Any] | None, str | None]:
        """Get provider config.

        Returns:
            (provider_config_dict, provider_name)
        """
        return self._match_provider(model)

    def get_vlm_instance(self) -> Any:
        """Get VLM instance."""
        if self._vlm_instance is None:
            config_dict = self._build_vlm_config_dict()
            from openviking.models.vlm import VLMFactory

            self._vlm_instance = VLMFactory.create(config_dict)
        return self._vlm_instance

    def _build_vlm_config_dict(self) -> Dict[str, Any]:
        """Build VLM instance config dict."""
        config, name = self.get_provider_config()

        # Get stream from provider config if available, fallback to self.stream
        stream = (
            config.get("stream") if config and config.get("stream") is not None else self.stream
        )

        result = {
            "model": self.model,
            "temperature": self.temperature,
            "max_retries": self.max_retries,
            "timeout": self.timeout,
            "provider": name,
            "thinking": self.thinking,
            "max_tokens": self.max_tokens,
            "stream": stream,
            "api_version": self.api_version,
        }

        if config:
            if config.get("api_key"):
                result["api_key"] = config.get("api_key")
            if config.get("api_base"):
                result["api_base"] = config.get("api_base")
            if config.get("extra_headers"):
                result["extra_headers"] = config.get("extra_headers")

        return result

    def get_completion(
        self,
        prompt: str = "",
        thinking: bool = False,
        tools: Optional[List[Dict[str, Any]]] = None,
        messages: Optional[List[Dict[str, Any]]] = None,
    ) -> Union[str, Any]:
        """Get LLM completion."""
        return self.get_vlm_instance().get_completion(
            prompt=prompt,
            thinking=thinking,
            tools=tools,
            messages=messages,
        )

    async def get_completion_async(
        self,
        prompt: str = "",
        thinking: bool = False,
        tools: Optional[List[Dict[str, Any]]] = None,
        messages: Optional[List[Dict[str, Any]]] = None,
    ) -> Union[str, Any]:
        """Get LLM completion asynchronously."""
        return await self.get_vlm_instance().get_completion_async(
            prompt=prompt,
            thinking=thinking,
            tools=tools,
            messages=messages,
        )

    def is_available(self) -> bool:
        """Check if LLM is configured."""
        if self._resolve_provider_name() == "openai-codex":
            has_codex_auth_available = _load_codex_auth_module().has_codex_auth_available

            return bool(self._get_effective_api_key() or has_codex_auth_available())
        return self._get_effective_api_key() is not None

    def get_vision_completion(
        self,
        prompt: str = "",
        images: Optional[list] = None,
        thinking: bool = False,
        tools: Optional[List[Dict[str, Any]]] = None,
        messages: Optional[List[Dict[str, Any]]] = None,
    ) -> Union[str, Any]:
        """Get LLM completion with images."""
        return self.get_vlm_instance().get_vision_completion(
            prompt=prompt,
            images=images,
            thinking=thinking,
            tools=tools,
            messages=messages,
        )

    async def get_vision_completion_async(
        self,
        prompt: str = "",
        images: Optional[list] = None,
        thinking: bool = False,
        tools: Optional[List[Dict[str, Any]]] = None,
        messages: Optional[List[Dict[str, Any]]] = None,
    ) -> Union[str, Any]:
        """Get LLM completion with images asynchronously."""
        return await self.get_vlm_instance().get_vision_completion_async(
            prompt=prompt,
            images=images,
            thinking=thinking,
            tools=tools,
            messages=messages,
        )
