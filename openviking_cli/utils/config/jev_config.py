# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0
"""Configuration for Jev-compatible System One endpoints."""

from typing import Dict, Optional

from pydantic import BaseModel, Field, model_validator

JEV_PROVIDER_DEFAULTS: Dict[str, Dict[str, str]] = {
    "typesafe": {
        "api_base": "https://api.typesafe.ai/v1/systemone",
        "model": "jev-latest",
    },
    "vercel": {
        "api_base": "https://ai-gateway.vercel.sh/typesafe/v1/systemone",
        "model": "typesafe-ai/jev",
    },
    "openrouter": {
        "api_base": "https://openrouter.ai/api/v1/systemone",
        "model": "jev-latest",
    },
    "custom": {},
}


class JevConfig(BaseModel):
    """One Jev-compatible System One model target."""

    provider: str = Field(
        default="typesafe",
        description="Jev service provider: typesafe, vercel, openrouter, or custom",
    )
    api_key: Optional[str] = Field(default=None, description="Provider API key")
    api_base: Optional[str] = Field(
        default=None,
        description="Complete System One request URL; explicit values are used unchanged",
    )
    model: Optional[str] = Field(
        default=None,
        description="Provider model name or endpoint ID",
    )
    timeout: float = Field(default=30.0, gt=0.0, description="HTTP request timeout in seconds")
    extra_headers: Optional[Dict[str, str]] = Field(
        default=None,
        description="Additional HTTP request headers",
    )
    log_payloads: bool = Field(
        default=False,
        description="Log complete requests and responses, including Rule content",
    )

    model_config = {"extra": "forbid"}

    @model_validator(mode="after")
    def validate_config(self) -> "JevConfig":
        self.provider = self.provider.strip().lower()
        if self.provider not in JEV_PROVIDER_DEFAULTS:
            raise ValueError(
                "Jev provider must be one of "
                "['typesafe', 'vercel', 'openrouter', 'custom'], "
                f"got '{self.provider}'"
            )

        if self.api_base is not None and not self.api_base.strip():
            raise ValueError("Jev api_base must not be empty")
        if self.model is not None:
            self.model = self.model.strip()
            if not self.model:
                raise ValueError("Jev model must not be empty")

        if not self.model_fields_set:
            return self
        if not self.api_key:
            raise ValueError("Jev provider requires 'api_key'")
        if self.provider == "custom":
            if not self.api_base:
                raise ValueError("Custom Jev provider requires 'api_base'")
            if not self.model:
                raise ValueError("Custom Jev provider requires 'model'")
        return self

    def resolved_api_base(self) -> str:
        """Return the complete request URL without modifying explicit values."""
        if self.api_base is not None:
            return self.api_base
        return str(JEV_PROVIDER_DEFAULTS[self.provider]["api_base"])

    def resolved_model(self) -> str:
        """Return the configured model or the provider default."""
        if self.model is not None:
            return self.model
        return str(JEV_PROVIDER_DEFAULTS[self.provider]["model"])

    def resolved_extra_headers(self) -> Dict[str, str]:
        """Return explicitly configured request headers."""
        return dict(self.extra_headers or {})

    def is_available(self) -> bool:
        """Return whether the configured target has all required values."""
        if not self.api_key:
            return False
        try:
            return bool(self.resolved_api_base() and self.resolved_model())
        except KeyError:
            return False
