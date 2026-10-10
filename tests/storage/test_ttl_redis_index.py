"""Real Redis contracts for stable URI scores and version-safe acknowledgement."""

import os
from uuid import uuid4

import pytest

from openviking.service.ttl_cleanup import TTLCleanup, ACK
from openviking_cli.utils.config.ttl_config import TTLCleanupConfig
from tests.storage.test_transfer_merge_binding import root_ctx


@pytest.mark.asyncio
async def test_redis_renewed_version_survives_old_ack():
    url = os.environ.get("TTL_TEST_REDIS_URL")
    if not url:
        pytest.skip("Set TTL_TEST_REDIS_URL to an isolated test Redis")
    from redis.asyncio import Redis

    client = Redis.from_url(url, decode_responses=True)
    worker = TTLCleanup(
        None,
        TTLCleanupConfig(enabled=True, index_url=url, index_namespace="ttl-test:" + uuid4().hex),
        client=client,
    )
    uri = "viking://user/default/memories/events/a.md"
    ctx = root_ctx()
    key = worker._key(ctx.account_id, ctx.user.user_id, uri.rsplit("/", 1)[0])
    try:
        await worker.register(uri, 100, ctx)
        await worker.register(uri, 200, ctx)
        await worker.register(uri, 150, ctx)
        assert await client.zscore(key, uri) == 200
        assert await client.eval(ACK, 1, key, uri, 100) == 0
        assert await client.zscore(key, uri) == 200
        assert await client.eval(ACK, 1, key, uri, 200) == 1
        assert await client.zcard(key) == 0
    finally:
        await client.delete(key, worker.catalog)
        await client.aclose()
