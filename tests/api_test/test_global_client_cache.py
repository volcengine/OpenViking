"""Regression tests for the shared VikingClient cache in openviking_hooks.

``_global_clients`` used to key on ``id(config)``. The hooks rebuild the config object
on every request, so ``id()`` changed every time and the cache grew without bound while
never serving a hit.

Dropping ``config`` from the key fixes the growth but breaks binding: the client copies
``server_url``, ``api_key``, ``account_id`` and ``admin_user_id`` off the config it was
created with, so a second caller passing a different config would silently talk to the
first caller's endpoint with the first caller's credentials.

The key is therefore a value fingerprint of exactly those fields: equal configs share a
client, unequal configs do not, and identity churn creates no new entries.
"""

import asyncio
import sys
import types
from pathlib import Path
from types import SimpleNamespace

import pytest

# This test lives at the root of ``tests/api_test`` because that is the directory
# the api-test workflow collects (``cd tests/api_test && pytest .``); ``bot/tests``
# is not reached by any workflow.  ``vikingbot`` lives under ``bot/`` and is not
# installed by the repo-wide test environment.  At this depth ``parents[2]`` is
# still the repository root (tests/api_test/<this file> -> tests -> repo).
_REPO_ROOT = Path(__file__).resolve().parents[2]
for _path in (_REPO_ROOT, _REPO_ROOT / "bot"):
    if str(_path) not in sys.path:
        sys.path.insert(0, str(_path))

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


def _cfg(server_url="http://localhost:1933", api_key="k-1", account_id="acc",
         admin_user_id="admin", auth_mode="api_key", api_key_type="user"):
    return SimpleNamespace(
        server_url=server_url,
        api_key=api_key,
        account_id=account_id,
        admin_user_id=admin_user_id,
        effective_auth_mode=auth_mode,
        api_key_type=api_key_type,
    )


@pytest.fixture(autouse=True)
def _reset_cache():
    """Wipe the module-level cache before every test."""
    openviking_hooks._global_clients.clear()
    yield
    openviking_hooks._global_clients.clear()


@pytest.fixture
def fake_client_factory(monkeypatch):
    """Patch ``VikingClient.create`` so tests do not hit the network."""
    created: list[dict] = []

    async def fake_create(workspace_id=None, *, config=None, connection=None, **_kw):
        created.append({"workspace_id": workspace_id, "config": config, "connection": connection})
        # Distinct instance per call so we can tell which one was returned.
        return SimpleNamespace(
            workspace_id=workspace_id, config=config, connection=connection,
            _call_index=len(created),
        )

    monkeypatch.setattr(openviking_hooks, "VikingClient", SimpleNamespace(create=fake_create))
    return created


@pytest.mark.asyncio
async def test_equal_configs_share_one_client(fake_client_factory):
    """A rebuilt-but-equal config must hit the cache instead of minting a client."""
    client_a = await openviking_hooks.get_global_client("ws-1", config=_cfg())
    client_b = await openviking_hooks.get_global_client("ws-1", config=_cfg())
    client_c = await openviking_hooks.get_global_client("ws-1", config=_cfg())

    assert client_a is client_b is client_c
    assert len(fake_client_factory) == 1


@pytest.mark.asyncio
async def test_identity_churn_does_not_grow_the_cache(fake_client_factory):
    """Twenty fresh objects carrying the same values must leave one entry."""
    for _ in range(20):
        await openviking_hooks.get_global_client("ws-x", config=_cfg())

    assert len(openviking_hooks._global_clients) == 1
    assert len(fake_client_factory) == 1


@pytest.mark.asyncio
async def test_different_server_url_gets_a_different_client(fake_client_factory):
    """The client pins the endpoint; a second endpoint must not reuse it."""
    client_a = await openviking_hooks.get_global_client("ws-1", config=_cfg(server_url="http://a:1"))
    client_b = await openviking_hooks.get_global_client("ws-1", config=_cfg(server_url="http://b:1"))

    assert client_a is not client_b
    assert len(fake_client_factory) == 2
    assert fake_client_factory[1]["config"].server_url == "http://b:1"


@pytest.mark.asyncio
async def test_different_api_key_gets_a_different_client(fake_client_factory):
    """The client pins the credential; tenants must not share a client."""
    client_a = await openviking_hooks.get_global_client("ws-1", config=_cfg(api_key="k-a"))
    client_b = await openviking_hooks.get_global_client("ws-1", config=_cfg(api_key="k-b"))

    assert client_a is not client_b
    assert len(fake_client_factory) == 2


@pytest.mark.asyncio
async def test_different_identity_gets_a_different_client(fake_client_factory):
    """account_id / admin_user_id select who the client acts as."""
    client_a = await openviking_hooks.get_global_client("ws-1", config=_cfg(account_id="acc-a"))
    client_b = await openviking_hooks.get_global_client("ws-1", config=_cfg(account_id="acc-b"))
    client_c = await openviking_hooks.get_global_client("ws-1", config=_cfg(admin_user_id="other"))

    assert client_a is not client_b is not client_c
    assert len(fake_client_factory) == 3


@pytest.mark.asyncio
async def test_separates_workspaces_and_loops(fake_client_factory):
    """Both non-config dimensions still separate clients."""
    client_a = await openviking_hooks.get_global_client("ws-a")
    client_b = await openviking_hooks.get_global_client("ws-b")

    assert client_a is not client_b
    assert len(openviking_hooks._global_clients) == 2


@pytest.mark.asyncio
async def test_handles_none_workspace_and_none_config(fake_client_factory):
    """``None`` is normalized to stable key parts."""
    client_a = await openviking_hooks.get_global_client(None)
    client_b = await openviking_hooks.get_global_client(None)

    assert client_a is client_b
    keys = list(openviking_hooks._global_clients.keys())
    assert len(keys) == 1
    workspace_part, loop_part, config_part = keys[0]
    assert workspace_part == "__default__"
    assert loop_part == id(asyncio.get_running_loop())
    assert config_part is None


@pytest.mark.asyncio
async def test_config_fingerprint_ignores_object_identity(fake_client_factory):
    """The key carries values, never ``id()`` of the config object."""
    cfg = _cfg()
    await openviking_hooks.get_global_client("ws-shape", config=cfg)

    key = next(iter(openviking_hooks._global_clients))
    assert len(key) == 3
    workspace_part, loop_part, config_part = key
    assert workspace_part == "ws-shape"
    assert loop_part == id(asyncio.get_running_loop())
    assert config_part == openviking_hooks._config_cache_key(cfg)
    assert str(id(cfg)) not in repr(config_part)
