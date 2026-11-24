# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0
"""Configuration independent from the aggregate OpenViking config package."""

from __future__ import annotations

import os
from typing import Literal
from urllib.parse import urlsplit

from pydantic import BaseModel, ConfigDict, Field, model_validator


class ExpiryRedisConfig(BaseModel):
    model_config = ConfigDict(extra="forbid", hide_input_in_errors=True)

    mode: Literal["standalone", "cluster"] = "standalone"
    endpoints: list[str] = Field(default_factory=list)
    username: str = ""
    password_env: str = ""
    password: str = Field(default="", repr=False)
    tls: bool = False
    connect_timeout_seconds: float = Field(default=1.0, gt=0)
    socket_timeout_seconds: float = Field(default=2.0, gt=0)
    request_budget_seconds: float = Field(default=5.0, gt=0)
    max_connections: int = Field(default=64, ge=1)
    max_inflight_requests: int = Field(default=128, ge=1)

    @model_validator(mode="after")
    def validate_connection(self) -> "ExpiryRedisConfig":
        if self.password and self.password_env:
            raise ValueError("Redis password and password_env are mutually exclusive")
        if self.mode == "standalone" and len(self.endpoints) > 1:
            raise ValueError("standalone Redis accepts at most one endpoint")
        schemes: set[str] = set()
        for endpoint in self.endpoints:
            normalized = (
                endpoint
                if "://" in endpoint
                else (("rediss" if self.tls else "redis") + f"://{endpoint}")
            )
            try:
                parsed = urlsplit(normalized)
                valid_address = bool(parsed.hostname and parsed.port)
            except ValueError:
                # urlsplit/.port errors can echo untrusted endpoint fragments.
                raise ValueError("invalid Redis endpoint") from None
            if parsed.scheme not in {"redis", "rediss"} or not valid_address:
                raise ValueError("invalid Redis endpoint")
            if (
                parsed.username is not None
                or parsed.password is not None
                or parsed.path not in {"", "/"}
                or parsed.query
                or parsed.fragment
            ):
                raise ValueError(
                    "Redis endpoints must not contain credentials, paths, "
                    "query parameters, or fragments; use dedicated Redis fields"
                )
            schemes.add(parsed.scheme)
        if len(schemes) > 1:
            raise ValueError("Redis cluster endpoints must use one transport scheme")
        return self

    def resolve_password(self) -> str | None:
        if self.password_env.strip():
            try:
                return os.environ[self.password_env]
            except KeyError as exc:
                raise ValueError(
                    f"Redis secret environment variable {self.password_env} is not set"
                ) from exc
        return self.password if self.password.strip() else None


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
    redis: ExpiryRedisConfig = Field(default_factory=ExpiryRedisConfig)

    def validate_for_runtime(self) -> None:
        if not self.service_name.strip():
            raise ValueError(
                "expiry.service_name is required when Redis expiry scheduling is enabled"
            )
        if not self.redis.endpoints:
            raise ValueError(
                "expiry.redis.endpoints is required when Redis expiry scheduling is enabled"
            )
        if self.redis.mode == "standalone" and len(self.redis.endpoints) != 1:
            raise ValueError("standalone Redis requires exactly one endpoint")
        if self.redis.request_budget_seconds >= self.owner_renew_interval_seconds:
            raise ValueError("Redis request budget must be shorter than owner renew interval")
