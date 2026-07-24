# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0

"""Multi-instance API-key cache freshness regressions for issue #2351."""

import asyncio
from copy import deepcopy
from unittest.mock import MagicMock

import pytest

from openviking.server.api_keys import APIKeyManager
from openviking.server.api_keys.legacy import ACCOUNTS_CACHE_TTL_SECONDS
from openviking.server.config import ServerConfig
from openviking.server.identity import Role
from openviking_cli.exceptions import UnauthenticatedError

ROOT_KEY = "test-root-key-abcdef1234567890abcdef1234567890"


class FakeSharedStorage:
    """The JSON surface used by two managers sharing one AGFS backend."""

    def __init__(self) -> None:
        self.values: dict[str, dict] = {}

    async def read_json(self, path: str):
        value = self.values.get(path)
        return deepcopy(value) if value is not None else None

    async def write_json(self, path: str, value: dict) -> None:
        self.values[path] = deepcopy(value)


def _manager(
    storage: FakeSharedStorage,
    *,
    accounts_cache_ttl_seconds: float = ACCOUNTS_CACHE_TTL_SECONDS,
) -> APIKeyManager:
    viking_fs = MagicMock()
    viking_fs.agfs = MagicMock()
    manager = APIKeyManager(
        root_key=ROOT_KEY,
        viking_fs=viking_fs,
        accounts_cache_ttl_seconds=accounts_cache_ttl_seconds,
    )
    manager._legacy._read_json = storage.read_json
    manager._legacy._write_json = storage.write_json
    return manager


def test_server_cache_ttl_configuration_is_positive():
    assert ServerConfig().api_key_cache_ttl_seconds == ACCOUNTS_CACHE_TTL_SECONDS

    with pytest.raises(ValueError):
        ServerConfig(api_key_cache_ttl_seconds=0)


async def test_configured_cache_ttl_controls_staleness():
    manager = _manager(FakeSharedStorage(), accounts_cache_ttl_seconds=120.0)
    await manager.load()

    assert manager._legacy._loaded_at is not None
    manager._legacy._loaded_at -= 119.0
    assert manager._legacy._cache_is_stale() is False

    manager._legacy._loaded_at -= 2.0
    assert manager._legacy._cache_is_stale() is True


async def test_peer_created_key_refreshes_on_first_cache_miss():
    """A key created after a peer loaded becomes usable on its next request."""
    storage = FakeSharedStorage()
    writer = _manager(storage)
    reader = _manager(storage)
    await writer.load()
    await reader.load()

    account_id = "peer_created"
    api_key = await writer.create_account(account_id, "alice")

    with pytest.raises(UnauthenticatedError):
        reader.resolve(api_key)

    identity = await reader.resolve_with_refresh(api_key)
    assert identity.account_id == account_id
    assert identity.user_id == "alice"


async def test_distinct_invalid_keys_share_global_miss_refresh_bound(
    monkeypatch: pytest.MonkeyPatch,
):
    """Attacker-controlled key cardinality cannot cause one reload per key."""
    manager = _manager(FakeSharedStorage())
    await manager.load()

    real_load = manager._legacy.load
    reload_count = 0

    async def counted_load() -> None:
        nonlocal reload_count
        reload_count += 1
        await real_load()

    monkeypatch.setattr(manager._legacy, "load", counted_load)

    for index in range(20):
        with pytest.raises(UnauthenticatedError):
            await manager.resolve_with_refresh(f"invalid-key-{index}")

    assert reload_count == 1


async def test_concurrent_peer_key_misses_deduplicate_reload(
    monkeypatch: pytest.MonkeyPatch,
):
    storage = FakeSharedStorage()
    writer = _manager(storage)
    reader = _manager(storage)
    await writer.load()
    await reader.load()

    account_id = "concurrent_peer"
    api_key = await writer.create_account(account_id, "alice")

    real_load = reader._legacy.load
    reload_count = 0

    async def counted_load() -> None:
        nonlocal reload_count
        reload_count += 1
        await asyncio.sleep(0.01)
        await real_load()

    monkeypatch.setattr(reader._legacy, "load", counted_load)

    identities = await asyncio.gather(*(reader.resolve_with_refresh(api_key) for _ in range(10)))

    assert reload_count == 1
    assert {identity.account_id for identity in identities} == {account_id}
    assert {identity.user_id for identity in identities} == {"alice"}


async def test_ttl_refresh_observes_peer_key_rotation():
    storage = FakeSharedStorage()
    writer = _manager(storage)
    await writer.load()
    account_id = "rotated_peer"
    old_key = await writer.create_account(account_id, "alice")

    reader = _manager(storage)
    await reader.load()
    assert reader.resolve(old_key).account_id == account_id

    new_key = await writer.regenerate_key(account_id, "alice")
    assert reader._legacy._loaded_at is not None
    reader._legacy._loaded_at -= ACCOUNTS_CACHE_TTL_SECONDS + 1

    identity = await reader.resolve_with_refresh(new_key)
    assert identity.account_id == account_id
    with pytest.raises(UnauthenticatedError):
        reader.resolve(old_key)


async def test_failed_reload_keeps_last_complete_cache(
    monkeypatch: pytest.MonkeyPatch,
):
    manager = _manager(FakeSharedStorage())
    await manager.load()
    account_id = "cached_account"
    api_key = await manager.create_account(account_id, "alice")

    async def failed_read(_path: str):
        raise RuntimeError("storage unavailable")

    monkeypatch.setattr(manager._legacy, "_read_json", failed_read)

    with pytest.raises(RuntimeError, match="storage unavailable"):
        await manager._legacy.load()

    assert manager.resolve(api_key).account_id == account_id


async def test_root_key_bypasses_stale_shared_storage(
    monkeypatch: pytest.MonkeyPatch,
):
    """Root access remains available when only shared account storage fails."""
    manager = _manager(FakeSharedStorage())
    await manager.load()
    assert manager._legacy._loaded_at is not None
    manager._legacy._loaded_at -= ACCOUNTS_CACHE_TTL_SECONDS + 1

    async def failed_read(_path: str):
        raise RuntimeError("storage unavailable")

    monkeypatch.setattr(manager._legacy, "_read_json", failed_read)

    identity = await manager.resolve_with_refresh(ROOT_KEY)
    assert identity.role is Role.ROOT
