# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0
"""Direct Redis client configuration retained for Directory TTL."""

from __future__ import annotations

import os
from typing import Any, Literal
from urllib.parse import urlsplit

from pydantic import BaseModel, ConfigDict, Field, model_validator


class DirectoryTTLRedisConfig(BaseModel):
    """Validate the standalone Redis connection owned by Directory TTL."""

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
    def validate_connection(self) -> "DirectoryTTLRedisConfig":
        """Validate direct Redis endpoints and mutually exclusive credentials."""
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
        """Return the configured password without exposing it in validation errors."""
        if self.password_env.strip():
            try:
                return os.environ[self.password_env]
            except KeyError as exc:
                raise ValueError(
                    f"Redis secret environment variable {self.password_env} is not set"
                ) from exc
        return self.password if self.password.strip() else None


def create_directory_ttl_redis_client(config: DirectoryTTLRedisConfig, *, retry=None):
    """Build the direct Redis client used only by Directory TTL."""
    import redis.asyncio as redis_async

    endpoint = config.endpoints[0]
    if "://" not in endpoint:
        endpoint = ("rediss" if config.tls else "redis") + "://" + endpoint
    kwargs: dict[str, Any] = {
        "username": config.username or None,
        "password": config.resolve_password(),
        "decode_responses": True,
        "socket_connect_timeout": config.connect_timeout_seconds,
        "socket_timeout": config.socket_timeout_seconds,
        "max_connections": config.max_connections,
    }
    if retry is not None:
        kwargs["retry"] = retry
    if config.mode == "standalone":
        return redis_async.Redis.from_url(endpoint, **kwargs)

    from redis.asyncio.cluster import ClusterNode
    from redis.backoff import NoBackoff
    from redis.retry import Retry

    from openviking.scheduling.redis_cluster import SeededRedisCluster

    nodes = []
    use_tls = config.tls
    for raw in config.endpoints:
        normalized = raw if "://" in raw else (("rediss" if config.tls else "redis") + f"://{raw}")
        parsed = urlsplit(normalized)
        use_tls = parsed.scheme == "rediss"
        nodes.append(ClusterNode(parsed.hostname or "", parsed.port or 6379))
    kwargs.setdefault("retry", Retry(NoBackoff(), 0))
    return SeededRedisCluster(
        startup_nodes=nodes,
        ssl=use_tls,
        cluster_error_retry_attempts=0,
        **kwargs,
    )
