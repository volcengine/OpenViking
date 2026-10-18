# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0
"""Topology-aware asyncio Redis client construction for account stores."""

from __future__ import annotations

import os
from dataclasses import dataclass
from typing import Any
from urllib.parse import urlparse

from openviking_cli.exceptions import InvalidArgumentError
from openviking_cli.utils.config.agfs_config import RedisCacheConfig


def _secret(value: str, env_name: str, field_name: str) -> str | None:
    if value and env_name:
        raise InvalidArgumentError(f"{field_name} and {field_name}_env cannot both be configured")
    if env_name:
        resolved = os.environ.get(env_name)
        if not resolved:
            raise InvalidArgumentError(
                f"{field_name}_env points to an unset or empty environment variable"
            )
        return resolved
    return value or None


def _tls_options(
    config: RedisCacheConfig,
    scheme: str,
    *,
    include_ssl_flag: bool,
) -> dict[str, Any]:
    if scheme == "rediss":
        options = {
            "ssl_cert_reqs": "none" if config.tls_insecure_skip_verify else "required",
        }
        if include_ssl_flag:
            options["ssl"] = True
        return options
    return {}


def _endpoint_parts(config: RedisCacheConfig) -> list[tuple[str, int]]:
    result: list[tuple[str, int]] = []
    for endpoint in config.endpoints:
        parsed = urlparse(endpoint)
        result.append((parsed.hostname or "", parsed.port or 6379))
    return result


@dataclass
class RedisClientHandle:
    """A Redis client plus Sentinel discovery clients owned by the factory."""

    client: Any
    sentinel_clients: tuple[Any, ...] = ()

    async def close(self) -> None:
        try:
            close = getattr(self.client, "aclose", None)
            if close is not None:
                await close()
        finally:
            for sentinel in self.sentinel_clients:
                close = getattr(sentinel, "aclose", None)
                if close is not None:
                    await close()


def create_redis_client(raw_params: dict[str, object]) -> RedisClientHandle:
    """Create a redis-py client for standalone, cluster, or Sentinel Redis."""
    try:
        config = RedisCacheConfig.model_validate(raw_params)
    except Exception as exc:
        raise InvalidArgumentError(f"Invalid Redis cache configuration: {exc}") from exc

    password = _secret(config.password, config.password_env, "cache.params.password")
    sentinel_password = _secret(
        config.sentinel_password,
        config.sentinel_password_env,
        "cache.params.sentinel_password",
    )
    scheme = urlparse(config.endpoints[0]).scheme
    common: dict[str, Any] = {
        "decode_responses": True,
        "socket_connect_timeout": config.connect_timeout_ms / 1000,
        "socket_timeout": config.command_timeout_ms / 1000,
        "username": config.username or None,
        "password": password,
        "max_connections": config.pool_size,
    }

    if config.mode == "standalone":
        from redis.asyncio import Redis

        return RedisClientHandle(
            Redis.from_url(
                config.endpoints[0],
                db=config.db,
                **common,
                **_tls_options(config, scheme, include_ssl_flag=False),
            )
        )

    if config.mode == "cluster":
        from redis.asyncio.cluster import ClusterNode, RedisCluster

        return RedisClientHandle(
            RedisCluster(
                startup_nodes=[ClusterNode(host, port) for host, port in _endpoint_parts(config)],
                db=0,
                **common,
                **_tls_options(config, scheme, include_ssl_flag=True),
            )
        )

    from redis.asyncio.sentinel import Sentinel

    sentinel_common = {
        "decode_responses": True,
        "socket_connect_timeout": config.connect_timeout_ms / 1000,
        "socket_timeout": config.command_timeout_ms / 1000,
        **_tls_options(config, scheme, include_ssl_flag=True),
    }
    sentinel = Sentinel(
        _endpoint_parts(config),
        sentinel_kwargs={
            **sentinel_common,
            "username": config.sentinel_username or None,
            "password": sentinel_password,
        },
        **common,
        **_tls_options(config, scheme, include_ssl_flag=True),
        db=config.db,
    )
    return RedisClientHandle(
        sentinel.master_for(
            config.master_name or "",
            **common,
            **_tls_options(config, scheme, include_ssl_flag=True),
        ),
        tuple(sentinel.sentinels),
    )
