# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0

import asyncio
from types import SimpleNamespace

import pytest

from openviking_cli.expiry_bootstrap import run_publisher


class _CacheClient:
    def __init__(self):
        self.handle = object()
        self.closed = False

    def cache_runtime(self, instance):
        assert instance == "expiry"
        return self.handle

    def close(self):
        self.closed = True


async def test_publisher_uses_selected_runtime_and_closes_binding_client(monkeypatch, tmp_path):
    client = _CacheClient()
    created = []

    class _Runtime:
        def __init__(self, config, *, cache_runtime, roles):
            assert config.cache.instance == "expiry"
            assert cache_runtime is client.handle
            assert roles.publish is True
            self.closed = False
            created.append(self)

        async def start_storage(self):
            pass

        async def start_roles(self):
            pass

        async def wait(self):
            await asyncio.Future()

        async def close(self):
            self.closed = True

    monkeypatch.setattr(
        "openviking.utils.agfs_utils.build_runtime_ragfs_binding_config",
        lambda config: (object(), None),
    )
    monkeypatch.setattr("openviking.utils.agfs_utils.create_agfs_client", lambda config: client)
    monkeypatch.setattr("openviking.scheduling.expiry.runtime.ExpiryRuntime", _Runtime)

    stop = asyncio.Event()
    stop.set()
    config = SimpleNamespace(expiry=SimpleNamespace(cache=SimpleNamespace(instance="expiry")))
    await run_publisher(config, stop, tmp_path / "startup.json")

    assert len(created) == 1
    assert created[0].closed is True
    assert client.closed is True


async def test_publisher_keeps_binding_client_open_when_runtime_close_fails(monkeypatch, tmp_path):
    client = _CacheClient()

    class _Runtime:
        def __init__(self, config, *, cache_runtime, roles):
            assert cache_runtime is client.handle

        async def start_storage(self):
            pass

        async def start_roles(self):
            pass

        async def wait(self):
            await asyncio.Future()

        async def close(self):
            raise RuntimeError("shard_not_stopped")

    monkeypatch.setattr(
        "openviking.utils.agfs_utils.build_runtime_ragfs_binding_config",
        lambda config: (object(), None),
    )
    monkeypatch.setattr("openviking.utils.agfs_utils.create_agfs_client", lambda config: client)
    monkeypatch.setattr("openviking.scheduling.expiry.runtime.ExpiryRuntime", _Runtime)

    stop = asyncio.Event()
    stop.set()
    config = SimpleNamespace(expiry=SimpleNamespace(cache=SimpleNamespace(instance="expiry")))

    with pytest.raises(RuntimeError, match="shard_not_stopped"):
        await run_publisher(config, stop, tmp_path / "startup.json")

    assert client.closed is False
