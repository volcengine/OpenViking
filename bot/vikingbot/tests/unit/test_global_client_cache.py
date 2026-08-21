"""Regression tests for the shared VikingClient cache in openviking_hooks.

Bug #10: ``_global_clients`` used a 3-tuple key of
``(workspace_id, running_loop_id, id(config))``.  Because ``id(config)``
changes on every fresh config object, calling ``get_global_client`` with
the same workspace + loop but a fresh config instance created a new entry
every time, leaking clients indefinitely.  Even if the same config was
reused, ``id()`` is the in-memory address — different processes or
non-deterministically GC'd objects would still miss the cache.

The fix removes ``id(config)`` from the key, leaving
``(workspace_id, running_loop_id)``.  The cache now keys only on the
factors that genuinely require a new client (per-loop, per-workspace).
"""

import asyncio
import sys
import types
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

# Avoid pulling in the full vikingbot.config.loader chain in unit tests.
sys.path.insert(0, str(Path(__file__).resolve().parents[4]))

try:
    import vikingbot.config.loader  # noqa: F401
except Exception:
    config_module = types.ModuleType("vikingbot.config")
    loader_module = types.ModuleType("vikingbot.config.loader")
    loader_module.load_config = lambda: None
    config_module.load_config = loader_module
    sys.modules.setdefault("vikingbot.config", config_module)
    sys.modules.setdefault("vikingbot.config.loader", loader_module)

from vikingbot.hooks.builtins import openviking_hooks  # noqa: E402


@pytest.fixture(autouse=True)
def _reset_cache():
    """Wipe the module-level cache before every test."""
    openviking_hooks._global_clients.clear()
    yield
    openviking_hooks._global_clients.clear()


@pytest.fixture
def fake_client_factory(monkeypatch):
    """Patch ``VikingClient.create`` so tests do not hit the network."""
    created: list[tuple] = []

    async def fake_create(workspace_id=None, *, config=None, connection=None, **_kw):
        created.append(
            {
                "workspace_id": workspace_id,
                "config": config,
                "connection": connection,
            }
        )
        # Distinct id per call so we can tell which instance was returned.
        return SimpleNamespace(
            workspace_id=workspace_id,
            config=config,
            connection=connection,
            _call_index=len(created),
        )

    monkeypatch.setattr(openviking_hooks, "VikingClient", SimpleNamespace(create=fake_create))
    return created


@pytest.mark.asyncio
async def test_get_global_client_reuses_client_for_same_workspace_and_loop(
    fake_client_factory,
):
    """Same workspace + same loop + different configs must reuse the cached client.

    Before the fix, ``id(config)`` in the cache key meant every fresh
    config object produced a new cache entry (and a new VikingClient).
    After the fix, ``config`` is forwarded to the existing client only on
    cache miss.
    """
    ws = "ws-1"
    cfg_a = SimpleNamespace(name="config-a")
    cfg_b = SimpleNamespace(name="config-b")
    cfg_c = SimpleNamespace(name="config-c")

    client_a = await openviking_hooks.get_global_client(ws, config=cfg_a)
    client_b = await openviking_hooks.get_global_client(ws, config=cfg_b)
    client_c = await openviking_hooks.get_global_client(ws, config=cfg_c)

    # Same client across all three calls.
    assert client_a is client_b is client_c

    # VikingClient.create was called exactly once.
    assert len(fake_client_factory) == 1
    assert fake_client_factory[0]["config"] is cfg_a  # first config wins


@pytest.mark.asyncio
async def test_get_global_client_cache_size_does_not_grow_with_repeated_configs(
    fake_client_factory,
):
    """Calling with a rotating sequence of config objects must not leak entries."""
    for i in range(20):
        cfg = SimpleNamespace(name=f"config-{i}")
        await openviking_hooks.get_global_client("ws-x", config=cfg)

    assert len(openviking_hooks._global_clients) == 1
    assert len(fake_client_factory) == 1


@pytest.mark.asyncio
async def test_get_global_client_separates_workspaces(fake_client_factory):
    """Different workspaces still get their own clients."""
    client_a = await openviking_hooks.get_global_client("ws-a")
    client_b = await openviking_hooks.get_global_client("ws-b")

    assert client_a is not client_b
    assert len(openviking_hooks._global_clients) == 2
    assert len(fake_client_factory) == 2


@pytest.mark.asyncio
async def test_get_global_client_handles_none_workspace(fake_client_factory):
    """``workspace_id=None`` is normalized to a stable default key."""
    client_a = await openviking_hooks.get_global_client(None, config=SimpleNamespace())
    client_b = await openviking_hooks.get_global_client(None, config=SimpleNamespace())

    assert client_a is client_b
    # Only one cache entry under the "__default__" workspace slot.
    keys = list(openviking_hooks._global_clients.keys())
    assert keys[0][0] == "__default__"
    assert len(keys) == 1


@pytest.mark.asyncio
async def test_get_global_client_cache_key_shape(fake_client_factory):
    """After the fix, the cache key is exactly (workspace, loop) — 2-tuple."""
    await openviking_hooks.get_global_client("ws-shape", config=SimpleNamespace())

    keys = list(openviking_hooks._global_clients.keys())
    assert len(keys) == 1
    key = keys[0]
    assert len(key) == 2, (
        f"Cache key should be a 2-tuple (workspace, loop_id); got {key!r}. "
        f"Re-introducing id(config) would leak entries on every config rebuild."
    )
    workspace_part, loop_part = key
    assert workspace_part == "ws-shape"
    assert loop_part == id(asyncio.get_running_loop())