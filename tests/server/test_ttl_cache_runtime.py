# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0

"""TTL adapter CacheRuntime wiring regression tests."""

from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from openviking.server.config import TTLAdapterConfig
from openviking.ttl.wiring import build_ttl_adapter


def test_ttl_adapter_uses_injected_cache_runtime():
    """Require TTL adapter construction to use the supplied CacheRuntime."""
    cache_runtime = Mock()

    adapter = build_ttl_adapter(
        TTLAdapterConfig(enabled=True, config={"resource_id": "ttl-test"}),
        cache_runtime=cache_runtime,
    )

    assert adapter.cache_runtime is cache_runtime
    assert cache_runtime.register_script.call_count == 5


def test_ttl_adapter_rejects_legacy_direct_redis_config():
    """Reject the deleted Python-owned Redis connection configuration."""
    with pytest.raises(ValueError, match="ttl_adapter.config.redis"):
        TTLAdapterConfig(enabled=True, config={"resource_id": "ttl-test", "redis": {}})


async def test_ttl_runtime_wiring_selects_configured_cache_runtime(monkeypatch):
    """Select the configured RAGFS Runtime before constructing the adapter."""
    from openviking.server.config import ServerConfig
    from openviking.service.core import OpenVikingService
    from openviking.ttl import wiring

    cache_runtime = Mock()
    viking_fs = Mock()
    service = SimpleNamespace(
        _viking_fs=viking_fs,
        _compile_service=SimpleNamespace(lifecycle=SimpleNamespace(resolve_groups=None)),
        _compile_scheduler=None,
        _server_config=ServerConfig(
            ttl_adapter={
                "enabled": True,
                "cache": {"instance": "ttl"},
                "config": {"resource_id": "ttl-test"},
            }
        ),
        _ttl_adapter=None,
        cache_runtime=Mock(return_value=cache_runtime),
        runtime_config_manager=object(),
        viking_fs=viking_fs,
    )
    manager = SimpleNamespace(get_user_group_ids=Mock())
    monkeypatch.setattr(wiring, "_adapter", None)

    OpenVikingService.wire_runtime(service, manager)

    service.cache_runtime.assert_called_once_with("ttl")
    assert service._ttl_adapter.cache_runtime is cache_runtime
