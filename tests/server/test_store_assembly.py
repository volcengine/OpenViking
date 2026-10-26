# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0

import ssl
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from openviking.scheduling.expiry.config import ExpiryRedisConfig
from openviking.scheduling.expiry.keys import ExpiryKeys
from openviking.scheduling.expiry.metrics import ExpiryMetrics
from openviking.scheduling.expiry.redis_store import RedisExpiryStore
from openviking.server.account_stores.redis_client import create_redis_client
from openviking.server.api_keys.legacy import FileStore
from openviking.server.config import AccountStoreConfig
from openviking.server.store_assembly import (
    build_account_store,
    build_api_key_manager,
)
from openviking_cli.exceptions import InvalidArgumentError
from tests.server.account_store_fakes import InMemoryAGFS


class _InlineExecutor:
    async def run(self, operation):
        return await operation()


async def test_account_store_connection_configuration():
    with pytest.raises(ValueError, match="account_store.provider must be one of"):
        AccountStoreConfig(provider="missing")
    with pytest.raises(InvalidArgumentError, match="Unknown account store provider"):
        build_account_store(
            viking_fs=SimpleNamespace(agfs=InMemoryAGFS()),
            api_key_hashing_enabled=False,
            account_store_provider="missing",
            account_store_params=None,
            account_store_watch_enabled=False,
            account_store_watch_interval_seconds=30,
        )

    redis_connection = pytest.importorskip("redis.asyncio.connection")
    handle = create_redis_client({"mode": "standalone", "endpoints": ["rediss://127.0.0.1:6379"]})
    try:
        # Constructing only the pool would miss unsupported connection arguments.
        connection = handle.client.connection_pool.make_connection()
        assert isinstance(connection, redis_connection.SSLConnection)
        assert connection.ssl_context.get().verify_mode == ssl.CERT_REQUIRED
    finally:
        await handle.close()


async def test_expiry_cluster_disables_client_level_command_retries(monkeypatch):
    cluster = pytest.importorskip("redis.asyncio.cluster")
    ping = AsyncMock(return_value=True)
    monkeypatch.setattr(cluster.RedisCluster, "ping", ping)
    store = RedisExpiryStore(
        ExpiryRedisConfig(mode="cluster", endpoints=["127.0.0.1:6379"]),
        ExpiryKeys("resource"),
        _InlineExecutor(),
        ExpiryMetrics(),
    )
    await store.open()
    other_cluster = cluster.RedisCluster(host="127.0.0.1", port=6379)
    try:
        assert store._client.retry._retries == 0
        assert other_cluster.retry._retries == 3
        assert store._client.retry is not other_cluster.retry
    finally:
        await store.close()
        await other_cluster.aclose()


@pytest.mark.parametrize("injected", [False, True])
async def test_assembled_manager_supports_account_authentication(injected):
    account_store = FileStore(SimpleNamespace(agfs=InMemoryAGFS()))
    manager = build_api_key_manager(
        "root",
        None if injected else SimpleNamespace(agfs=InMemoryAGFS()),
        account_store_provider=AccountStoreConfig(provider=" FILE ").provider,
        account_store=account_store if injected else None,
    )
    await manager.load()
    try:
        key = await manager.create_account("acme", "alice")
        assert (await manager.resolve_identity(key)).user_id == "alice"
    finally:
        await manager.close()
