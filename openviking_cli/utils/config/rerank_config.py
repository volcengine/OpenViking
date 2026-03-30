# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: Apache-2.0
from typing import Optional

from pydantic import BaseModel, Field, model_validator


class RerankConfig(BaseModel):
    """Configuration for rerank API (VikingDB or OpenAI-compatible providers)."""

    provider: str = Field(
        default="vikingdb", description="Rerank provider: 'vikingdb', 'openai', or 'litellm'"
    )

    # VikingDB fields
    ak: Optional[str] = Field(default=None, description="VikingDB Access Key")
    sk: Optional[str] = Field(default=None, description="VikingDB Secret Key")
    host: str = Field(
        default="api-vikingdb.vikingdb.cn-beijing.volces.com", description="VikingDB API host"
    )
    model_name: str = Field(default="doubao-seed-rerank", description="Rerank model name")
    model_version: str = Field(default="251028", description="Rerank model version")

    # OpenAI-compatible fields
    api_key: Optional[str] = Field(
        default=None, description="Bearer token for OpenAI-compatible providers"
    )
    api_base: Optional[str] = Field(default=None, description="Custom endpoint URL")
    model: Optional[str] = Field(
        default=None, description="Model name for OpenAI-compatible providers"
    )

    threshold: float = Field(
        default=0.1, description="Relevance threshold (score > threshold is relevant)"
    )

    model_config = {"extra": "forbid"}

    @model_validator(mode="after")
    def validate_provider_fields(self) -> "RerankConfig":
        allowed = ["vikingdb", "openai", "litellm"]
        if self.provider not in allowed:
            raise ValueError(f"Rerank provider must be one of {allowed}, got '{self.provider}'")
        if self.provider == "openai":
            if not self.api_key or not self.api_base:
                raise ValueError(
                    "OpenAI-compatible rerank provider requires 'api_key' and 'api_base'"
                )
        if self.provider == "litellm":
            if not self.model:
                raise ValueError("LiteLLM rerank provider requires 'model'")
        return self

    def is_available(self) -> bool:
        """Check if rerank is configured."""
        if self.provider == "openai":
            return self.api_key is not None and self.api_base is not None
        if self.provider == "litellm":
            return self.model is not None
        return self.ak is not None and self.sk is not None
