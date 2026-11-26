# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0
"""Configuration independent from the aggregate OpenViking config package."""

from __future__ import annotations

import re
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator

_CACHE_INSTANCE_NAME_RE = re.compile(r"^[A-Za-z0-9_-]+$")


class ExpiryCacheConfig(BaseModel):
    """Select the named CacheRuntime used by distributed expiry scheduling."""

    model_config = ConfigDict(extra="forbid", hide_input_in_errors=True)

    instance: str = Field(min_length=1)

    @field_validator("instance")
    @classmethod
    def validate_instance(cls, value: str) -> str:
        """Validate the CacheRuntime selector against named cache instance rules."""
        if not _CACHE_INSTANCE_NAME_RE.fullmatch(value):
            raise ValueError("expiry.cache.instance must match [A-Za-z0-9_-]+")
        return value


class ExpiryConfig(BaseModel):
    model_config = ConfigDict(extra="forbid", hide_input_in_errors=True)

    service_name: str = ""
    publisher_separate_process: bool = True
    shard_count: int = Field(default=128, ge=1, le=512)
    session_consumer_concurrency: int = Field(default=8, ge=1)
    precision_seconds: Literal[60] = 60
    owner_ttl_seconds: Literal[120] = 120
    owner_renew_interval_seconds: Literal[20] = 20
    scan_batch_size: int = Field(default=128, ge=1, le=4096)
    cleanup_batch_size: int = Field(default=128, ge=1, le=4096)
    registry_page_size: int = Field(default=128, ge=1, le=4096)
    processing_timeout_seconds: int = Field(default=120, ge=1)
    callback_poll_seconds: float = Field(default=0.5, gt=0)
    shutdown_timeout_seconds: float = Field(default=30.0, gt=0)
    cache: ExpiryCacheConfig | None = None
    request_budget_seconds: float = Field(default=5.0, gt=0)
    max_inflight_requests: int = Field(default=128, ge=1)

    def validate_for_runtime(self) -> None:
        """Validate fields required when one process starts expiry scheduling."""
        if not self.service_name.strip():
            raise ValueError(
                "expiry.service_name is required when expiry scheduling is enabled"
            )
        if self.cache is None:
            raise ValueError("expiry.cache.instance is required when expiry scheduling is enabled")
        if self.request_budget_seconds >= self.owner_renew_interval_seconds:
            raise ValueError("expiry request budget must be shorter than owner renew interval")
