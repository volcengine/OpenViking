# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0
"""TTL aliases retain sparse runtime PATCH semantics across config reloads."""

from types import SimpleNamespace

import pytest

from openviking.config.binding import manager_over_source
from openviking.config.scope import ConfigScope, ScopeKind
from openviking.config.source import MemoryConfigSource
from openviking.config.ttl import resolve_ttl_config
from openviking.config.validate import ConfigPatchError, normalize_config_keys, validate_patch
from openviking_cli.utils.config import TTLConfig, get_openviking_config, set_openviking_config
from openviking_cli.utils.config.open_viking_config import OpenVikingConfig


@pytest.fixture
def runtime():
    original = get_openviking_config()

    def build(ttl=None):
        source = MemoryConfigSource()
        manager = manager_over_source(
            source, base_config=original.model_copy(update={"ttl": ttl or TTLConfig()})
        )
        return SimpleNamespace(runtime_config_manager=manager), manager, source

    try:
        yield build
    finally:
        set_openviking_config(original)


@pytest.mark.asyncio
async def test_alias_and_field_name_patch_update_one_value_and_null_restores_baseline(runtime):
    fs, manager, source = runtime(TTLConfig(global_default={"mode": "days", "ttl_days": 30}))
    await manager.initialize()
    await manager.patch_cluster({"ttl": {"global": {"mode": "days", "ttl_days": 20}}})
    await manager.patch_cluster({"ttl": {"global_default": {"ttl_days": 15}}})
    assert (await resolve_ttl_config(fs, "acct")).global_default.ttl_days == 15
    await manager.patch_account(
        "acct", {"ttl": {"global_default": {"mode": "days", "ttl_days": 7}}}
    )
    await manager.patch_account("acct", {"ttl": {"global": {"ttl_days": 5}}})
    assert (await resolve_ttl_config(fs, "acct")).global_default.ttl_days == 5
    reloaded = manager_over_source(source, base_config=manager.base_config)
    await reloaded.initialize()
    assert (
        await resolve_ttl_config(SimpleNamespace(runtime_config_manager=reloaded), "acct")
    ).global_default.ttl_days == 5
    assert (await reloaded.get_settings(ConfigScope.account("acct")))["ttl"] == {
        "global": {"mode": "days", "ttl_days": 5}
    }
    await manager.patch_account("acct", {"ttl": {"global_default": None}})
    assert (await resolve_ttl_config(fs, "acct")).global_default.ttl_days == 15
    await manager.patch_cluster({"ttl": {"global_default": None}})
    assert (await resolve_ttl_config(fs, "acct")).global_default.ttl_days == 30


def test_alias_does_not_bypass_runtime_write_restrictions():
    with pytest.raises(ConfigPatchError):
        validate_patch(OpenVikingConfig, {"storage": {"vectordb": {"project": "changed"}}})
    with pytest.raises(ConfigPatchError, match="either"):
        normalize_config_keys(OpenVikingConfig, {"ttl": {"global": None, "global_default": None}})


@pytest.mark.asyncio
@pytest.mark.parametrize("scope", ["user_events", "peer_events", "sessions"])
@pytest.mark.parametrize("mode", ["disabled", "inherit"])
async def test_account_policy_mode_replaces_incompatible_cluster_fields(runtime, scope, mode):
    fs, manager, source = runtime(TTLConfig(**{scope: {"mode": "days", "ttl_days": 30}}))
    await manager.initialize()
    await manager.patch_account("acct", {"ttl": {scope: {"mode": mode}}})
    policy = getattr(await resolve_ttl_config(fs, "acct"), scope)
    assert policy.mode == mode
    assert policy.ttl_days is None
    reloaded = manager_over_source(source, base_config=manager.base_config)
    await reloaded.initialize()
    policy = getattr(
        await resolve_ttl_config(SimpleNamespace(runtime_config_manager=reloaded), "acct"),
        scope,
    )
    assert policy.mode == mode
    assert policy.ttl_days is None
    await manager.patch_account("acct", {"ttl": {scope: None}})
    assert getattr(await resolve_ttl_config(fs, "acct"), scope).ttl_days == 30


@pytest.mark.asyncio
@pytest.mark.parametrize("account", [False, True])
async def test_policy_switches_are_atomic_and_keep_other_directories(runtime, account):
    fs, manager, _ = runtime()
    first = "viking://user/u1/memories/events"
    sibling = "viking://user/u1/peers/p1/memories/events"

    async def patch(value):
        if account:
            return await manager.patch_account("acct", {"ttl": value})
        return await manager.patch_cluster({"ttl": value})

    await manager.initialize()
    await patch(
        {
            "directories": {
                first: {"mode": "days", "ttl_days": 30},
                sibling: {"mode": "days", "ttl_days": 7},
            }
        }
    )
    with pytest.raises(ValueError):
        await patch({"directories": {first: {"mode": "absolute", "ttl_absolute": -1}}})
    effective = await resolve_ttl_config(fs, "acct")
    assert effective.directories[first].ttl_absolute is None
    assert effective.directories[first].ttl_days == 30
    assert effective.directories[sibling].ttl_days == 7
    await patch({"directories": {first: {"mode": "disabled"}}})
    assert (await resolve_ttl_config(fs, "acct")).directories[first].mode == "disabled"
    await patch({"user_events": {"ttl_days": 14}})
    assert (await resolve_ttl_config(fs, "acct")).user_events.ttl_days == 14


@pytest.mark.asyncio
async def test_initial_and_persisted_shorthand_use_the_same_ttl_merge(runtime):
    fs, manager, source = runtime()
    settings = {"ttl": {"sessions": {"ttl_days": 30}}}
    await manager.initialize()
    manager.validate_initial_settings("new-account", settings)
    await source.update(ConfigScope.account("existing-account"), lambda _: settings)

    effective = await resolve_ttl_config(fs, "existing-account")

    assert effective.sessions.mode == "days"
    assert effective.sessions.ttl_days == 30


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("cluster", "account"),
    [
        ({}, {}),
        (
            {"ttl": {"global_default": {"ttl_days": 7}}},
            {"ttl": {"sessions": {"ttl_days": 15}}},
        ),
        (
            {"ttl": {"sessions": {"mode": "absolute", "ttl_absolute": 4102444800}}},
            {"ttl": {"sessions": {"mode": "disabled"}}},
        ),
        (
            {"ttl": {"sessions": {"ttl_days": 7}}},
            {"ttl": {"sessions": None, "global": None}},
        ),
        ({"ttl": {"global": None}}, {"ttl": None}),
        ({"ttl": None}, {}),
        (
            {"ttl": {"directories": {"viking://user/u/sessions": {"ttl_days": 7}}}},
            {"ttl": {"directories": {"viking://user/u/sessions/": {"ttl_days": 15}}}},
        ),
        ({"ttl": {"future_option": True}}, {"ttl": {"future_option": True}}),
    ],
)
async def test_current_source_ttl_matches_runtime_resolution(runtime, cluster, account):
    fs, manager, source = runtime(TTLConfig(global_default={"mode": "days", "ttl_days": 30}))
    await source.update(ConfigScope.cluster(), lambda _: cluster)
    await source.update(ConfigScope.account("acct"), lambda _: account)
    await manager.initialize()
    cached = await resolve_ttl_config(fs, "acct")
    current = await resolve_ttl_config(fs, "acct", fresh=True)
    assert current == cached


@pytest.mark.asyncio
async def test_current_source_ttl_does_not_publish_or_fall_back_to_cached_settings(
    runtime, monkeypatch
):
    fs, manager, source = runtime(TTLConfig(global_default={"mode": "days", "ttl_days": 30}))
    await source.update(ConfigScope.cluster(), lambda _: {"ttl": {"global": {"ttl_days": 7}}})
    notifications = []

    async def notify(event):
        notifications.append(event)

    await manager.initialize()
    cached = await resolve_ttl_config(fs, "acct")
    for scope in (ScopeKind.CLUSTER, ScopeKind.ACCOUNT):
        manager.add_update_consumer(scope=scope, sections={"ttl"}, consumer=notify)
    await source.update(
        ConfigScope.account("acct"), lambda _: {"ttl": {"sessions": {"ttl_days": 15}}}
    )
    current = await resolve_ttl_config(fs, "acct", fresh=True)
    assert current.global_default.ttl_days == 7
    assert current.sessions.ttl_days == 15
    await source.delete(ConfigScope.cluster())
    await source.delete(ConfigScope.account("acct"))
    assert (await resolve_ttl_config(fs, "acct", fresh=True)) == manager.base_config.ttl
    assert (await resolve_ttl_config(fs, "acct")) == cached
    assert not notifications

    async def fail_load(scope):
        raise OSError("config source unavailable")

    monkeypatch.setattr(source, "load", fail_load)
    with pytest.raises(OSError, match="config source unavailable"):
        await resolve_ttl_config(fs, "acct", fresh=True)
    assert (await resolve_ttl_config(fs, "acct")) == cached
