# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: Apache-2.0
from typing import Any, Dict, Optional

from pydantic import BaseModel, Field, model_validator


class VLMConfig(BaseModel):
    """VLM configuration, supports multiple provider backends."""

    model: Optional[str] = Field(default=None, description="Model name")
    api_key: Optional[str] = Field(default=None, description="API key")
    api_base: Optional[str] = Field(default=None, description="API base URL")
    temperature: float = Field(default=0.0, description="Generation temperature")
    max_retries: int = Field(default=2, description="Maximum retry attempts")

    provider: Optional[str] = Field(default=None, description="Provider type")
    backend: Optional[str] = Field(
        default=None, description="Backend provider (Deprecated, use 'provider' instead)"
    )

    providers: Dict[str, Dict[str, Any]] = Field(
        default_factory=dict,
        description="Multi-provider configuration, e.g. {'deepseek': {'api_key': 'xxx', 'api_base': 'xxx'}}",
    )

    default_provider: Optional[str] = Field(default=None, description="Default provider name")

    thinking: bool = Field(default=False, description="Enable thinking mode for VolcEngine models")

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
        return data

    @model_validator(mode="after")
    def validate_config(self):
        """Validate configuration completeness and consistency"""
        self._migrate_legacy_config()

        if self._has_any_config():
            if not self.model:
                raise ValueError("VLM configuration requires 'model' to be set")
            if not self._get_effective_api_key():
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

    def _has_any_config(self) -> bool:
        """Check if any config is provided."""
        if self.api_key or self.model or self.api_base:
            return True
        if self.providers:
            for p in self.providers.values():
                if p.get("api_key"):
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

    def _match_provider(self, model: str | None = None) -> tuple[Dict[str, Any] | None, str | None]:
        """Match provider config by model name.

        Returns:
            (provider_config_dict, provider_name)
        """
        from openviking.models.vlm.registry import PROVIDERS

        model_lower = (model or self.model or "").lower()

        if self.provider:
            p = self.providers.get(self.provider)
            if p and p.get("api_key"):
                return p, self.provider

        for spec in PROVIDERS:
            p = self.providers.get(spec.name)
            if p and any(kw in model_lower for kw in spec.keywords) and p.get("api_key"):
                return p, spec.name

        for spec in PROVIDERS:
            if spec.is_gateway:
                p = self.providers.get(spec.name)
                if p and p.get("api_key"):
                    return p, spec.name

        for spec in PROVIDERS:
            if not spec.is_gateway:
                p = self.providers.get(spec.name)
                if p and p.get("api_key"):
                    return p, spec.name

        return None, None

    def get_provider_config(
        self, model: str | None = None
    ) -> tuple[Dict[str, Any] | None, str | None, "Any | None"]:
        """Get provider config and spec.

        Returns:
            (provider_config_dict, provider_name, ProviderSpec)
        """
        from openviking.models.vlm.registry import find_by_name, find_gateway

        config, name = self._match_provider(model)
        if config and name:
            spec = find_by_name(name)
            gateway = find_gateway(name, config.get("api_key"), config.get("api_base"))
            return config, name, gateway or spec
        return None, None, None

    def get_vlm_instance(self) -> Any:
        """Get VLM instance."""
        if self._vlm_instance is None:
            config_dict = self._build_vlm_config_dict()
            from openviking.models.vlm import VLMFactory

            self._vlm_instance = VLMFactory.create(config_dict)
        return self._vlm_instance

    def _build_vlm_config_dict(self) -> Dict[str, Any]:
        """Build VLM instance config dict."""
        config, name, spec = self.get_provider_config()

        result = {
            "model": self.model,
            "temperature": self.temperature,
            "max_retries": self.max_retries,
            "provider": name,
            "thinking": self.thinking,
        }

        if config:
            result["api_key"] = config.get("api_key")
            result["api_base"] = config.get("api_base")
            result["extra_headers"] = config.get("extra_headers")

        if spec and not result.get("api_base") and spec.default_api_base:
            result["api_base"] = spec.default_api_base

        return result

    def get_completion(self, prompt: str, thinking: bool = False) -> str:
        """Get LLM completion."""
        return self.get_vlm_instance().get_completion(prompt, thinking)

    async def get_completion_async(
        self, prompt: str, thinking: bool = False, max_retries: int = 0
    ) -> str:
        """Get LLM completion asynchronously, max_retries=0 means no retry."""
        return await self.get_vlm_instance().get_completion_async(prompt, thinking, max_retries)

    def is_available(self) -> bool:
        """Check if LLM is configured."""
        return self._get_effective_api_key() is not None

    def get_vision_completion(
        self,
        prompt: str,
        images: list,
        thinking: bool = False,
    ) -> str:
        """Get LLM completion with images."""
        return self.get_vlm_instance().get_vision_completion(prompt, images, thinking)

    async def get_vision_completion_async(
        self,
        prompt: str,
        images: list,
        thinking: bool = False,
    ) -> str:
        """Get LLM completion with images asynchronously."""
        return await self.get_vlm_instance().get_vision_completion_async(prompt, images, thinking)
