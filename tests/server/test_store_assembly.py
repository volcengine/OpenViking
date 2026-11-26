# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0

from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from openviking.scheduling.expiry.config import ExpiryRedisConfig
from openviking.scheduling.expiry.keys import ExpiryKeys
from openviking.scheduling.expiry.metrics import ExpiryMetrics
from openviking.scheduling.expiry.redis_store import RedisExpiryStore
from openviking.scheduling.expiry.redis_store import create_redis_client as create_expiry_client
from openviking.server.account_stores.file import FileStore
from openviking.server.account_stores.redis_client import create_redis_client
from openviking.server.config import AccountStoreConfig
from openviking.server.identity import Role
from openviking.server.store_assembly import build_api_key_manager
from tests.server.account_store_fakes import InMemoryAGFS


class _InlineExecutor:
    async def run(self, operation):
        return await operation()


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


@pytest.mark.parametrize("factory", ["expiry", "account_store"])
async def test_cluster_client_recovers_after_an_outage(monkeypatch, factory):
    cluster = pytest.importorskip("redis.asyncio.cluster")
    from redis.asyncio.connection import Connection
    from redis.exceptions import ConnectionError as RedisConnectionError
    from redis.exceptions import RedisClusterException

    replies = {"CLUSTER SLOTS": [[0, 16383, ["10.0.0.5", 6379, "node"]]], "COMMAND": {}}
    reachable = True

    async def send_packed_command(connection, *args, **kwargs):
        if not reachable:
            raise RedisConnectionError("connection refused")

    async def parse_response(node, connection, command, **kwargs):
        return replies.get(command, True)

    monkeypatch.setattr(Connection, "send_packed_command", send_packed_command)
    monkeypatch.setattr(cluster.ClusterNode, "parse_response", parse_response)
    if factory == "expiry":
        client = create_expiry_client(
            ExpiryRedisConfig(mode="cluster", endpoints=["redis:6379"], max_connections=2)
        )
    else:
        client = create_redis_client(
            {"mode": "cluster", "endpoints": ["redis://redis:6379"], "pool_size": 2}
        ).client
    try:
        assert await client.ping()
        reachable = False
        # More failures than the pool has connections.
        for _ in range(5):
            with pytest.raises((RedisConnectionError, RedisClusterException)):
                await client.ping()
        reachable = True
        assert await client.ping()
    finally:
        await client.aclose()


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
        identity = await manager.resolve_identity(key)
        assert (identity.account_id, identity.user_id, identity.role) == (
            "acme",
            "alice",
            Role.ADMIN,
        )
        if injected:
            user = await account_store.get_user("acme", "alice")
            assert user is not None
            assert user["role"] == Role.ADMIN
    finally:
        await manager.close()
