"""Sentinel 仅负责选择新主，Expiry 自有 CAS/重试合同保持不变。"""

import pytest

from openviking.scheduling.expiry.config import ExpiryRedisConfig
from openviking.scheduling.expiry.redis_store import create_redis_client


def test_expiry_sentinel_connection_assembly():
    config = ExpiryRedisConfig(
        mode="sentinel", endpoints=["localhost:26379"],
        master_name="test-master", db=3, password="test-only",
    )
    client = create_redis_client(config)
    assert client.connection_pool.service_name == "test-master"
    assert client.connection_pool.connection_kwargs["db"] == 3


def test_expiry_sentinel_requires_named_master():
    with pytest.raises(ValueError):
        ExpiryRedisConfig(mode="sentinel", endpoints=["localhost:26379"])
