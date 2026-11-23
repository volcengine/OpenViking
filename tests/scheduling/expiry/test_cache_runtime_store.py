# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0

import shutil
import socket
import subprocess
import time

import pytest

from openviking.scheduling.expiry.api import ExpiryRegistration, ItemIdentity
from openviking.scheduling.expiry.cache_runtime_store import CacheRuntimeExpiryStore
from openviking.scheduling.expiry.config import ExpiryConfig
from openviking.scheduling.expiry.keys import HASH_NAME, HASH_SEED, IDENTITY_ENCODING, ExpiryKeys
from openviking.scheduling.expiry.metrics import ExpiryMetrics
from openviking.scheduling.expiry.models import (
    BucketRef,
    NamespaceSpec,
    OwnerRole,
    SourceCursor,
    StoreOperationError,
)
from openviking.scheduling.expiry.runtime import ExpiryRuntime, ExpiryRuntimeRoles
from openviking.utils.agfs_utils import RagfsBindingConfig, create_agfs_client
from openviking_cli.utils.config.agfs_config import AGFSConfig
from openviking_cli.utils.config.cache_config import CacheConfig


class _InlineExecutor:
    async def run(self, operation):
        return await operation()


class _CacheRuntime:
    default_ttl_seconds = 0

    def __init__(self):
        self.registered = {}
        self.pings = 0
        self.closes = 0
        self.script_calls = []
        self.set_calls = []
        self.get_value = None
        self.hgetall_calls = []
        self.hset_calls = []
        self.zadd_calls = []
        self.zrangebyscore_calls = []
        self.zcard_calls = []
        self.server_time_for_key_calls = []

    def register_script(self, script_id, redis_lua):
        """Record one registered Lua script."""
        self.registered[script_id] = redis_lua

    async def ping(self):
        """Record a Runtime health check."""
        self.pings += 1

    async def close(self):
        """Record one Runtime close request."""
        self.closes += 1

    async def execute_script(self, script_id, keys, args):
        """Return minimal results for remaining business scripts."""
        self.script_calls.append((script_id, keys, args))
        if script_id == "expiry.v1.read_zset_page":
            return b'{"items":[["item", "10"]],"next":false}'
        return 1

    async def get(self, key):
        """Return no stored scalar value from the fake Runtime."""
        return self.get_value

    async def set(self, key, value, **kwargs):
        """Record one scalar write and report that it applied."""
        self.set_calls.append((key, value, kwargs))
        return True

    async def hgetall(self, key):
        """Record one hash read and return an empty hash."""
        self.hgetall_calls.append(key)
        return {}

    async def hset(self, key, values):
        """Record one batch hash write and report all fields as new."""
        self.hset_calls.append((key, values))
        return len(values)

    async def zadd(self, key, entries):
        """Record one batch sorted-set write and report all members as new."""
        self.zadd_calls.append((key, entries))
        return len(entries)

    async def zrangebyscore(self, key, min_score, max_score, limit, **kwargs):
        """Record one scored range query and return one literal member."""
        self.zrangebyscore_calls.append((key, min_score, max_score, limit, kwargs))
        if key.startswith("expiry:consumer:registry:"):
            return [("session:0:0", 10.0)]
        return [("item", 10.0)]

    async def zcard(self, key):
        """Record one sorted-set cardinality read."""
        self.zcard_calls.append(key)
        return 0

    async def server_time_for_key(self, key):
        """Record one key-routed time read and return a valid millisecond clock."""
        self.server_time_for_key_calls.append(key)
        return 1_700_000_000_123

    async def zrem(self, key, members):
        """Report that every requested sorted-set member was removed."""
        return len(members)


@pytest.fixture
def redis_url(tmp_path):
    if shutil.which("redis-server") is None:
        pytest.skip("redis-server is required")
    with socket.socket() as reserved:
        reserved.bind(("127.0.0.1", 0))
        port = reserved.getsockname()[1]
    process = subprocess.Popen(
        ["redis-server", "--port", str(port), "--save", "", "--appendonly", "no"],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    try:
        deadline = time.monotonic() + 5
        while time.monotonic() < deadline:
            try:
                with socket.create_connection(("127.0.0.1", port), timeout=0.1):
                    break
            except OSError:
                time.sleep(0.05)
        else:
            raise RuntimeError("redis-server did not start")
        yield f"redis://127.0.0.1:{port}"
    finally:
        process.terminate()
        process.wait(timeout=5)


async def test_store_registers_scripts_and_keeps_runtime_open():
    runtime = _CacheRuntime()
    store = CacheRuntimeExpiryStore(
        runtime,
        ExpiryKeys("test"),
        _InlineExecutor(),
        ExpiryMetrics(),
    )

    await store.open()
    await store.close()

    assert runtime.pings == 1
    assert set(runtime.registered) == {
        "expiry.v1.ensure_metadata",
        "expiry.v1.upsert_resource",
        "expiry.v1.publish_minute",
        "expiry.v1.cleanup_source_page",
        "expiry.v1.defer_registration",
        "expiry.v1.cas_zset_member",
        "expiry.v1.compare_owner",
        "expiry.v1.read_zset_page",
    }
    assert runtime.closes == 0


def test_runtime_injects_the_selected_cache_runtime():
    cache_runtime = object()

    runtime = ExpiryRuntime(
        ExpiryConfig(service_name="test", cache={"instance": "expiry"}),
        cache_runtime=cache_runtime,
        roles=ExpiryRuntimeRoles(),
    )

    assert runtime._store._cache_runtime is cache_runtime


async def test_store_uses_runtime_for_namespace_paging_and_owner_lease():
    runtime = _CacheRuntime()
    store = CacheRuntimeExpiryStore(
        runtime,
        ExpiryKeys("test"),
        _InlineExecutor(),
        ExpiryMetrics(),
    )
    await store.open()

    await store.ensure_namespace(NamespaceSpec(1, "fnv1a64", 1, "v1"))
    page = await store.read_source_page(
        0,
        lower_exclusive_ms=0,
        upper_inclusive_ms=10,
        after=None,
        limit=1,
    )
    next_page = await store.read_source_page(
        0,
        lower_exclusive_ms=0,
        upper_inclusive_ms=10,
        after=SourceCursor(10, "item"),
        limit=1,
    )
    await store.read_registry_page(0, after=None, limit=1)
    now_ms = await store.read_time_ms()
    owner = await store.try_acquire_owner(OwnerRole.SCHEDULER, 0, "token", 120)

    assert page.items[0].item_id == "item"
    assert next_page.items[0].item_id == "item"
    assert now_ms == 1_700_000_000_123
    assert owner is not None
    runtime.get_value = b"token"
    with pytest.raises(StoreOperationError) as error:
        await store.stage_items(owner, BucketRef("session", 0, 0), [""])
    assert error.value.code == "invalid_identity"
    assert runtime.hgetall_calls == [ExpiryKeys("test").namespace()]
    assert runtime.hset_calls == [
        (
            ExpiryKeys("test").namespace(),
            {
                "shard_count": b"1",
                "hash_name": b"fnv1a64",
                "hash_seed": b"1",
                "identity_encoding": b"v1",
                "state": b"INITIALIZING",
            },
        ),
        (ExpiryKeys("test").namespace(), {"state": b"READY"}),
    ]
    assert runtime.set_calls[0][1:] == (b"0", {"if_not_exists": True})
    assert runtime.set_calls[1][1] == b"token"
    assert runtime.set_calls[1][2] == {"ttl_seconds": 120.0, "if_not_exists": True}
    assert runtime.zrangebyscore_calls == [
        (ExpiryKeys("test").source(0), 0, 10, 2, {"min_exclusive": True}),
        (
            ExpiryKeys("test").registry(0),
            -1,
            1_700_000_000_123,
            2,
            {"min_exclusive": True},
        ),
    ]
    assert runtime.server_time_for_key_calls == [
        ExpiryKeys("test").registry(0),
        ExpiryKeys("test").namespace(),
    ]
    assert [call[0] for call in runtime.script_calls] == [
        "expiry.v1.read_zset_page",
    ]


@pytest.mark.integration
async def test_store_runs_expiry_flow_through_native_cache_runtime(tmp_path, redis_url):
    client = create_agfs_client(
        RagfsBindingConfig(
            agfs=AGFSConfig(path=str(tmp_path), backend="memory"),
            cache=CacheConfig(
                instances={
                    "default": {"provider": "redis", "params": {"endpoints": [redis_url]}},
                    "expiry": {
                        "provider": "redis",
                        "params": {
                            "endpoints": [redis_url],
                            "default_ttl_seconds": 0,
                        },
                    },
                }
            ),
            required_cache_instances={"Expiry": "expiry"},
        )
    )
    store = CacheRuntimeExpiryStore(
        client.cache_runtime("expiry"),
        ExpiryKeys("test"),
        _InlineExecutor(),
        ExpiryMetrics(),
    )
    try:
        await store.open()
        await store.ensure_namespace(NamespaceSpec(1, HASH_NAME, HASH_SEED, IDENTITY_ENCODING))
        registration = ExpiryRegistration(
            ItemIdentity("session", "local", "account", "user"),
            due_at_ms=int(time.time() * 1000),
            timeout_seconds=60,
        )
        await store.ensure_metadata("item", registration)
        assert (await store.read_metadata("item")).identity == registration.identity
        await store.upsert_resource(0, "item", registration)
        source = await store.read_source_page(
            0,
            lower_exclusive_ms=0,
            upper_inclusive_ms=(1 << 53) - 1,
            after=None,
            limit=1,
        )
        assert source.items[0].item_id == "item"

        owner = await store.try_acquire_owner(OwnerRole.SCHEDULER, 0, "token", 120)
        assert owner is not None
        minute = int(time.time() * 1000) // 60_000 * 60_000
        bucket = BucketRef("session", 0, minute)
        await store.stage_items(owner, bucket, ["item"])
        published = await store.publish_minute(
            owner,
            expected_progress_ms=0,
            minute_ms=minute,
            buckets=[bucket],
        )
        assert published.status == "published"
        claimed = await store.claim(
            bucket,
            (await store.read_bucket_page(bucket, attempt=0, limit=1)).items[0],
            attempt=0,
            started_at_seconds=int(time.time()),
        )
        assert claimed is not None
        assert await store.ack(claimed)
        assert await store.remove_registration_if_empty(bucket)
        assert await store.release_owner(owner)
        await store.close()

        expiry_runtime = ExpiryRuntime(
            ExpiryConfig(
                service_name="test",
                shard_count=1,
                cache={"instance": "expiry"},
            ),
            cache_runtime=client.cache_runtime("expiry"),
            roles=ExpiryRuntimeRoles(),
        )
        try:
            await expiry_runtime.start_storage()
        finally:
            await expiry_runtime.close()
    finally:
        await store.close()
        client.close()
