# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0

"""Tests for the api-keys cache TTL + cache-miss reload behavior (issue #2351).

These tests bypass real AGFS by overriding ``_read_json`` / ``_write_json`` on
``LegacyAPIKeyManager`` with an in-memory dict, so they isolate the cache
invalidation logic from the storage backend (which the existing
``test_api_key_manager.py`` covers via a real OpenVikingService).
"""

import asyncio
import json
from typing import Optional
from unittest.mock import MagicMock

import pytest

from openviking.server.api_keys import APIKeyManager
from openviking.server.api_keys import legacy as legacy_module
from openviking_cli.exceptions import UnauthenticatedError

ROOT_KEY = "test-root-key-abcdef1234567890abcdef1234567890"


class _FakeAGFS:
    """In-memory AGFS replacement; only the methods the manager uses."""

    def __init__(self, store: Optional[dict] = None):
        self.store: dict = store if store is not None else {}
        self.read_count = 0

    async def _read_json(self, path: str):
        self.read_count += 1
        if path not in self.store:
            return None
        return json.loads(self.store[path])

    async def _write_json(self, path: str, data: dict):
        self.store[path] = json.dumps(data)

    async def _ensure_parent_dirs_async(self, path: str):
        return None


def _make_manager(fake: _FakeAGFS) -> APIKeyManager:
    """Construct an APIKeyManager whose AGFS calls are redirected to ``fake``."""
    # Build a manager with a stubbed VikingFS (we override the I/O methods so
    # the real AsyncAGFSClient is never called).
    viking_fs = MagicMock()
    viking_fs.agfs = MagicMock()
    mgr = APIKeyManager(root_key=ROOT_KEY, viking_fs=viking_fs)
    # Patch the legacy I/O. The manager exposes _legacy via a property.
    mgr._legacy._read_json = fake._read_json  # type: ignore[assignment]
    mgr._legacy._write_json = fake._write_json  # type: ignore[assignment]
    mgr._legacy._ensure_parent_dirs_async = fake._ensure_parent_dirs_async  # type: ignore[assignment]
    return mgr


# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_unknown_key_triggers_one_reload():
    """A cache-miss on a key that exists in AGFS should trigger one reload and succeed."""
    fake = _FakeAGFS()

    # Instance A: create an account+user. This populates AGFS.
    mgr_a = _make_manager(fake)
    await mgr_a.load()
    user_key = await mgr_a.create_account("acme", "alice")

    # Instance B: a *fresh* manager loads later (not yet aware of acme/alice).
    # Simulate "loaded earlier than the create" by loading before the data was
    # written — already done above for mgr_a; for mgr_b we just need to
    # discard whatever AGFS state is there at load time.
    mgr_b = _make_manager(fake)
    # Snapshot the store, load with empty store, then restore.
    saved = dict(fake.store)
    fake.store.clear()
    await mgr_b.load()
    fake.store.update(saved)

    # Pre-condition: instance B doesn't know the key yet.
    with pytest.raises(UnauthenticatedError):
        mgr_b.resolve(user_key)

    reads_before = fake.read_count
    # The async path with refresh should reload + succeed.
    identity = await mgr_b.resolve_with_refresh(user_key)
    assert identity.account_id == "acme"
    assert identity.user_id == "alice"
    # And the reload actually hit AGFS.
    assert fake.read_count > reads_before


@pytest.mark.asyncio
async def test_ttl_expires_entry(monkeypatch):
    """When TTL elapses, the next resolve_with_refresh reloads from AGFS."""
    fake = _FakeAGFS()
    mgr = _make_manager(fake)
    await mgr.load()
    user_key = await mgr.create_account("acme", "alice")

    # Sanity: a known key resolves without further reload.
    reads_before = fake.read_count
    await mgr.resolve_with_refresh(user_key)
    # No miss, no expiry → no reload.
    assert fake.read_count == reads_before

    # Advance the monotonic clock past the TTL.
    real_monotonic = legacy_module.time.monotonic
    offset = legacy_module.ACCOUNTS_CACHE_TTL_SECONDS + 5.0
    monkeypatch.setattr(legacy_module.time, "monotonic", lambda: real_monotonic() + offset)

    reads_before = fake.read_count
    await mgr.resolve_with_refresh(user_key)
    # TTL elapsed → reload happened (AGFS reads bumped).
    assert fake.read_count > reads_before


@pytest.mark.asyncio
async def test_local_write_invalidates_immediately():
    """A locally-created account is resolvable on the same instance with no TTL wait.

    This also exercises the negative-cache invalidation: if a key was looked
    up *before* the account existed, the failed lookup shouldn't poison the
    next lookup post-creation.
    """
    fake = _FakeAGFS()
    mgr = _make_manager(fake)
    await mgr.load()

    # We want to construct a key that will be created next, but we don't know
    # what create_account will mint. So instead: try a bogus key first, then
    # create, then try the real key. The bogus key seeds the negative cache;
    # invalidate_cache should still let the real key resolve immediately.
    fake_attempt = "deadbeef" * 8  # 64 hex chars, won't collide with real key
    with pytest.raises(UnauthenticatedError):
        await mgr.resolve_with_refresh(fake_attempt)

    # Now actually create.
    user_key = await mgr.create_account("acme", "alice")

    # Same instance, same process: should resolve without going through TTL.
    identity = await mgr.resolve_with_refresh(user_key)
    assert identity.account_id == "acme"
    assert identity.user_id == "alice"


@pytest.mark.asyncio
async def test_concurrent_misses_dedupe_reload():
    """10 concurrent misses for the same unknown key should trigger only one reload."""
    fake = _FakeAGFS()

    # Set up AGFS to contain an account that mgr_b doesn't know about yet.
    mgr_a = _make_manager(fake)
    await mgr_a.load()
    user_key = await mgr_a.create_account("acme", "alice")

    mgr_b = _make_manager(fake)
    saved = dict(fake.store)
    fake.store.clear()
    await mgr_b.load()
    fake.store.update(saved)

    # Spy on the storage layer.
    real_load = mgr_b._legacy.load
    call_count = {"n": 0}

    async def counting_load():
        call_count["n"] += 1
        # Tiny await so concurrent callers can pile up at the lock.
        await asyncio.sleep(0.01)
        await real_load()

    mgr_b._legacy.load = counting_load  # type: ignore[assignment]

    results = await asyncio.gather(
        *[mgr_b.resolve_with_refresh(user_key) for _ in range(10)],
        return_exceptions=True,
    )

    # All ten succeed (they may resolve before or after the reload, but must
    # not raise).
    for r in results:
        assert not isinstance(r, BaseException), r
        assert r.account_id == "acme"
        assert r.user_id == "alice"

    # Exactly one forced reload despite 10 concurrent misses.
    assert call_count["n"] == 1, f"expected 1 reload, got {call_count['n']}"


@pytest.mark.asyncio
async def test_known_invalid_key_does_not_reload_every_time():
    """A key that *isn't* in AGFS shouldn't reload AGFS on every request."""
    fake = _FakeAGFS()
    mgr = _make_manager(fake)
    await mgr.load()
    # Don't create the key in AGFS — it stays unknown.
    bad_key = "a" * 64

    real_load = mgr._legacy.load
    reload_count = {"n": 0}

    async def counting_load():
        reload_count["n"] += 1
        await real_load()

    mgr._legacy.load = counting_load  # type: ignore[assignment]

    # Hammer with the same bad key 20 times.
    for _ in range(20):
        with pytest.raises(UnauthenticatedError):
            await mgr.resolve_with_refresh(bad_key)

    # First miss reloads; subsequent ones hit the negative cache and skip.
    # Allow at most a small constant — anything not bounded is a regression.
    assert reload_count["n"] <= 2, f"unbounded reloads on known-bad key: {reload_count['n']}"


# ---------------------------------------------------------------------------
# Sanity checks for the underlying primitives.
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_load_is_idempotent():
    """Calling load() twice must not duplicate the prefix index."""
    fake = _FakeAGFS()
    mgr = _make_manager(fake)
    await mgr.load()
    user_key = await mgr.create_account("acme", "alice")

    prefix_size_before = sum(len(v) for v in mgr._legacy._prefix_index.values())
    await mgr._legacy.load()
    prefix_size_after = sum(len(v) for v in mgr._legacy._prefix_index.values())

    assert prefix_size_before == prefix_size_after
    # And the key still resolves.
    assert mgr.resolve(user_key).account_id == "acme"


@pytest.mark.asyncio
async def test_invalidate_cache_clears_negative_cache():
    """invalidate_cache clears _unknown_key_reload_at so a freshly-created key resolves."""
    fake = _FakeAGFS()
    mgr = _make_manager(fake)
    await mgr.load()

    bad_key = "z" * 64
    with pytest.raises(UnauthenticatedError):
        await mgr.resolve_with_refresh(bad_key)
    assert bad_key in mgr._legacy._unknown_key_reload_at

    mgr._legacy.invalidate_cache()
    assert mgr._legacy._unknown_key_reload_at == {}
