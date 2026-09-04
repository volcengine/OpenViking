# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0
from typing import Any, Dict

from pydantic import BaseModel, Field, model_validator


class CacheConfig(BaseModel):
    """Global cache Provider configuration shared by RAGFS modules."""

    provider: str = Field(description="Cache Provider name")
    params: Dict[str, Any] = Field(
        default_factory=dict,
        description="Provider-owned configuration parameters",
        repr=False,
    )

    model_config = {"extra": "forbid"}

    @model_validator(mode="after")
    def validate_config(self):
        if not self.provider.strip():
            raise ValueError("cache provider must not be empty")
        return self
