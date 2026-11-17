# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0
import re
from typing import Any, Dict

from pydantic import BaseModel, ConfigDict, Field, model_validator


_CACHE_INSTANCE_NAME_RE = re.compile(r"^[A-Za-z0-9_-]+$")


def validate_cache_instance_name(instance: str, field_name: str = "cache instance") -> str:
    """Validate and return one named cache runtime selector."""
    if not _CACHE_INSTANCE_NAME_RE.fullmatch(instance):
        raise ValueError(
            f"{field_name} must match [A-Za-z0-9_-]+"
        )
    return instance


class CacheInstanceConfig(BaseModel):
    """Configuration for one named cache runtime provider."""

    model_config = ConfigDict(extra="forbid")

    provider: str = Field(description="Cache Provider name")
    params: Dict[str, Any] = Field(
        default_factory=dict,
        description="Provider-owned configuration parameters",
        repr=False,
    )

    @model_validator(mode="after")
    def validate_config(self):
        """Validate the configured cache provider name."""
        self.provider = self.provider.strip()
        if not self.provider:
            raise ValueError("cache provider must not be empty")
        if self.provider not in {"redis", "dynamic"}:
            raise ValueError("cache provider must be redis or dynamic")
        return self


class CacheConfig(BaseModel):
    """Named cache runtime configuration shared by RAGFS modules."""

    model_config = ConfigDict(extra="forbid")

    instances: Dict[str, CacheInstanceConfig] = Field(
        description="Named cache runtime provider configurations",
    )

    @model_validator(mode="after")
    def validate_config(self):
        """Validate cache instance names and require the default runtime."""
        if "default" not in self.instances:
            raise ValueError("cache.instances must contain a default entry")
        for name in self.instances:
            validate_cache_instance_name(name, "cache instance name")
        return self
