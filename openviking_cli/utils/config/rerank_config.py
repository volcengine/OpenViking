# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0
from typing import Optional

from pydantic import BaseModel, Field, model_validator


class RerankConfig(BaseModel):
    """Configuration for rerank API. Supports VikingDB, Cohere, OpenAI-compatible, and LiteLLM providers."""

    provider: Optional[str] = Field(
        default=None,
        description="Rerank provider: 'vikingdb', 'cohere', 'openai', or 'litellm'. Auto-detected from config if omitted.",
    )

    # VikingDB fields
    ak: Optional[str] = Field(default=None, description="VikingDB Access Key")
    sk: Optional[str] = Field(default=None, description="VikingDB Secret Key")
    host: str = Field(
        default="api-vikingdb.vikingdb.cn-beijing.volces.com", description="VikingDB API host"
    )
    model_name: str = Field(default="doubao-seed-rerank", description="Rerank model name")
    model_version: str = Field(default="251028", description="Rerank model version")

    # Shared / OpenAI-compatible / Cohere fields
    api_key: Optional[str] = Field(
        default=None, description="API key (Cohere Bearer token or OpenAI-compatible providers)"
    )
    api_base: Optional[str] = Field(default=None, description="Custom endpoint URL")
    model: Optional[str] = Field(
        default=None, description="Model name for OpenAI-compatible or LiteLLM providers"
    )

    threshold: float = Field(
        default=0.1, description="Relevance threshold (score > threshold is relevant)"
    )

    model_config = {"extra": "forbid"}

    def _effective_provider(self) -> Optional[str]:
        """Auto-detect provider from config fields when not explicitly set."""
        if self.provider:
            return self.provider.lower()
        if self.api_key and self.api_base:
            return "openai"
        if self.api_key:
            return "cohere"
        if self.ak and self.sk:
            return "vikingdb"
        return None

    @model_validator(mode="after")
    def validate_provider_fields(self) -> "RerankConfig":
        provider = self._effective_provider()
        if provider and provider not in ["vikingdb", "cohere", "openai", "litellm"]:
            raise ValueError(
                f"Rerank provider must be one of ['vikingdb', 'cohere', 'openai', 'litellm'], got '{provider}'"
            )
        if provider == "openai":
            if not self.api_key or not self.api_base:
                raise ValueError(
                    "OpenAI-compatible rerank provider requires 'api_key' and 'api_base'"
                )
        if provider == "litellm":
            if not self.model:
                raise ValueError("LiteLLM rerank provider requires 'model'")
        return self

    def is_available(self) -> bool:
        """Check if rerank is configured."""
        p = self._effective_provider()
        if p == "cohere":
            return self.api_key is not None
        if p == "openai":
            return self.api_key is not None and self.api_base is not None
        if p == "litellm":
            return self.model is not None
        if p == "vikingdb":
            return self.ak is not None and self.sk is not None
        return False
