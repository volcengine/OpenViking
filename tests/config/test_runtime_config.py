# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0
"""Contract tests for the runtime configuration system.

Cluster and account are two independent config models. These tests exercise the
smallest meaningful public contracts across both:

- ``RuntimeField`` opt-in and path collection, cluster-vs-account scope by model.
- Three-state PATCH merge and structural validation (mutable / create-only /
  read-only).
- The manager's account loading, atomic multi-field publish, cluster/account
  publish isolation, consumer gating, and idle eviction.
- Account field lookup with automatic loading and cluster fallback.
- The file source's legacy-plaintext read / magic-write behaviour.
"""

from __future__ import annotations

import asyncio
from typing import Optional

import pytest
from pydantic import BaseModel, Field

from openviking.config import (
    AccountConfig,
    ConfigChangeReason,
    ConfigScope,
    RuntimeConfigManager,
    ScopeKind,
)
from openviking.config.merge import apply_three_state_patch
from openviking.config.source import FileConfigSource, MemoryConfigSource, create_config_source
from openviking.config.source.base import ConfigSourceContext
from openviking.config.validate import ConfigPatchError, validate_patch
from openviking_cli.utils.config.runtime_field import (
    RuntimeField,
    collect_frozen_paths,
    collect_runtime_field_paths,
    fallback_of,
    is_dynamic,
    is_runtime_field,
)

# -- test config models -------------------------------------------------------


class VLMSection(BaseModel):
    model: Optional[str] = RuntimeField(default=None)
    temperature: float = Field(default=0.0)  # plain Field -> immutable
    model_config = {"extra": "forbid"}


class MemorySection(BaseModel):
    extraction_enabled: bool = RuntimeField(default=True)
    model_config = {"extra": "forbid"}


class SwitchSection(BaseModel):
    enabled: bool
    model_config = {"extra": "forbid"}


class ClusterConfig(BaseModel):
    """Stand-in cluster model: one mutable section, one locked section."""

    vlm: VLMSection = RuntimeField(default_factory=VLMSection)
    memory: MemorySection = RuntimeField(default_factory=MemorySection)
    server: VLMSection = Field(default_factory=VLMSection)  # locked section
    model_config = {"extra": "forbid"}


class AccountModel(BaseModel):
    """Stand-in sparse account model used by the manager tests."""

    vlm: Optional[VLMSection] = RuntimeField(default=None, fallback="vlm")
    memory: Optional[MemorySection] = RuntimeField(default=None, fallback="memory")
    switch: Optional[SwitchSection] = RuntimeField(default=None)
    model_config = {"extra": "forbid"}


class NestedFrozenChild(BaseModel):
    frozen: str = RuntimeField(default="", dynamic=False)


class NestedFrozenRoot(BaseModel):
    child: NestedFrozenChild = RuntimeField(default_factory=NestedFrozenChild)


def _build_cluster(old: ClusterConfig, cluster_override: dict) -> ClusterConfig:
    """Rebuild the cluster config from its immutable base + sparse override."""
    return ClusterConfig(**apply_three_state_patch(old.model_dump(), cluster_override))


def _build_account(override: Optional[dict]) -> AccountModel:
    """Construct (and validate) the sparse account model from its override."""
    return AccountModel(**(override or {}))


def _make_manager(source) -> tuple[RuntimeConfigManager, dict]:
    holder = {"config": ClusterConfig()}
    manager = RuntimeConfigManager(
        source,
        base_config=holder["config"],
        get_config=lambda: holder["config"],
        set_config=lambda c: holder.__setitem__("config", c),
        build_config=_build_cluster,
        build_account=_build_account,
        validate_request=lambda patch, account, creating: validate_patch(
            AccountModel if account else ClusterConfig, patch, creating=creating
        ),
    )
    return manager, holder


# -- RuntimeField / path collection -------------------------------------------


def test_runtime_field_paths_scope_by_model():
    all_paths = collect_runtime_field_paths(ClusterConfig)
    assert ("vlm", "model") in all_paths
    assert ("memory", "extraction_enabled") in all_paths
    # plain Field leaf and plain Field section are never mutable
    assert ("vlm", "temperature") not in all_paths
    assert not any(p[0] == "server" for p in all_paths)

    assert is_dynamic(ClusterConfig.model_fields["vlm"])
    assert not is_runtime_field(ClusterConfig.model_fields["server"])


def test_real_openviking_config_exposes_no_mutable_path():
    """Existing OpenVikingConfig fields opted into the surface are dynamic RuntimeFields."""
    from openviking_cli.utils.config.open_viking_config import OpenVikingConfig

    # Agent Evolution is the current cluster runtime surface. Other existing
    # cluster fields remain startup-only and use plain Field.
    surface = collect_runtime_field_paths(OpenVikingConfig)
    assert surface == {("agent_evolution",)}
    assert ("vlm",) not in surface
    assert not any(p[0] == "rerank" for p in surface)
    assert is_dynamic(OpenVikingConfig.model_fields["agent_evolution"])
    assert not is_runtime_field(OpenVikingConfig.model_fields["memory"])
    assert not is_runtime_field(OpenVikingConfig.model_fields["vlm"])
    assert not is_runtime_field(OpenVikingConfig.model_fields["rerank"])
    # Static cluster sections are rejected by the dynamic PATCH API.
    with pytest.raises(ConfigPatchError):
        validate_patch(OpenVikingConfig, {"vlm": {"model": "m"}})
    with pytest.raises(ConfigPatchError):
        validate_patch(OpenVikingConfig, {"rerank": {"enabled": True}})


def test_cluster_rebuild_preserves_explicit_and_default_field_semantics():
    from openviking.config.binding import _build_cluster
    from openviking_cli.utils.config.open_viking_config import OpenVikingConfig

    base = OpenVikingConfig.from_dict(
        {
            "embedding": {
                "dense": {
                    "provider": "openai",
                    "model": "text-embedding-3-small",
                    "api_key": "test-key",
                }
            }
        }
    )
    assert "input" not in base.embedding.dense.model_fields_set

    rebuilt = _build_cluster(base, {"agent_evolution": {"enabled": True}})
    assert rebuilt.embedding.dense.input == "multimodal"
    assert "input" not in rebuilt.embedding.dense.model_fields_set

    explicit = OpenVikingConfig.from_dict(
        {
            "embedding": {
                "dense": {
                    "provider": "openai",
                    "model": "text-embedding-3-small",
                    "api_key": "test-key",
                    "input": "multimodal",
                }
            }
        }
    )
    rebuilt_explicit = _build_cluster(
        explicit,
        {"agent_evolution": {"enabled": True}},
    )
    assert "input" in rebuilt_explicit.embedding.dense.model_fields_set


def test_account_patch_keeps_sparse_explicit_field_semantics():
    async def run():
        manager, _ = _make_manager(MemoryConfigSource())
        await manager.patch_account("ov-a", {"vlm": {"model": "account-model"}})

        account = manager._accounts["ov-a"].config
        assert account.model_fields_set == {"vlm"}
        assert account.vlm.model_fields_set == {"model"}
        assert "temperature" not in account.vlm.model_fields_set

    asyncio.run(run())


def test_collect_frozen_paths_includes_nested_create_only_fields():
    assert collect_frozen_paths(NestedFrozenRoot) == {("child", "frozen")}
    with pytest.raises(ConfigPatchError, match="child.frozen"):
        validate_patch(NestedFrozenRoot, {"child": {"frozen": "changed"}})
    validate_patch(NestedFrozenRoot, {"child": {"frozen": "initial"}}, creating=True)


# -- PATCH request validation -------------------------------------------------


def test_validate_patch_accepts_mutable_paths_and_none_delete():
    validate_patch(ClusterConfig, {"vlm": {"model": "m"}})
    validate_patch(ClusterConfig, {"vlm": {"model": None}})
    validate_patch(ClusterConfig, {"memory": {"extraction_enabled": False}})


def test_validate_patch_rejects_locked_field_and_section():
    with pytest.raises(ConfigPatchError):
        validate_patch(ClusterConfig, {"vlm": {"temperature": 1.0}})  # locked leaf
    with pytest.raises(ConfigPatchError):
        validate_patch(ClusterConfig, {"server": {"model": "x"}})  # locked section
    with pytest.raises(ConfigPatchError):
        validate_patch(ClusterConfig, {"unknown": {}})  # unknown top-level


def test_validate_patch_scope_confined_by_model():
    # AccountModel has no `server`; the same patch that is valid against a cluster
    # model with `memory` is rejected once fields aren't on the account model.
    validate_patch(AccountModel, {"vlm": {"model": "m"}})
    with pytest.raises(ConfigPatchError):
        validate_patch(AccountModel, {"server": {"model": "m"}})


# -- three-state merge --------------------------------------------------------


def test_three_state_patch_semantics():
    current = {"vlm": {"model": "old", "temperature": 1.0}, "memory": {"extraction_enabled": True}}
    patched = apply_three_state_patch(
        current,
        {"vlm": {"model": "new", "temperature": None}, "memory": None},
    )
    assert patched == {"vlm": {"model": "new"}}
    assert current["vlm"]["temperature"] == 1.0  # inputs untouched (pure function)


def test_nested_delete_preserves_parent_existence():
    delete_leaf = {"memory": {"extraction_enabled": None}}

    # Deleting a missing child must not create its missing parent.
    assert apply_three_state_patch({}, delete_leaf) == {}

    # Deleting the final child preserves an already explicit parent.
    assert apply_three_state_patch(
        {"memory": {"extraction_enabled": False}},
        delete_leaf,
    ) == {"memory": {}}

    # Siblings remain, and only a parent-level null removes the parent.
    assert apply_three_state_patch(
        {"memory": {"extraction_enabled": False, "output_language": "zh"}},
        delete_leaf,
    ) == {"memory": {"output_language": "zh"}}
    assert apply_three_state_patch({"memory": {}}, {"memory": None}) == {}

    # An explicitly empty object is different from a delete-only child patch.
    assert apply_three_state_patch({}, {"memory": {}}) == {"memory": {}}


# -- manager: cluster PATCH ---------------------------------------------------


def test_cluster_patch_publishes_and_notifies_consumer():
    async def run():
        manager, holder = _make_manager(MemoryConfigSource())
        seen = []

        async def consumer(event):
            seen.append((event.scope.key, event.changed_sections, event.new_config.vlm.model))

        manager.add_update_consumer(
            scope=ScopeKind.CLUSTER,
            sections={"vlm"},
            consumer=consumer,
        )
        event = await manager.patch_cluster({"vlm": {"model": "gpt-x"}})

        assert seen == [(None, frozenset({"vlm"}), "gpt-x")]
        assert event.reason is ConfigChangeReason.UPDATE
        assert "vlm" in event.changed_sections
        assert holder["config"].vlm.model == "gpt-x"

        # patching a locked field is rejected and publishes nothing
        before = holder["config"]
        with pytest.raises(ConfigPatchError):
            await manager.patch_cluster({"server": {"model": "nope"}})
        assert holder["config"] is before
        assert len(seen) == 1

    asyncio.run(run())


def test_cluster_patch_reset_rebuilds_from_startup_baseline():
    async def run():
        source = MemoryConfigSource()
        manager, holder = _make_manager(source)

        await manager.patch_cluster({"vlm": {"model": "temporary"}})
        assert holder["config"].vlm.model == "temporary"

        await manager.patch_cluster({"vlm": None})
        assert await source.load(ConfigScope.cluster()) == {}
        assert holder["config"].vlm.model is None

    asyncio.run(run())


def test_multi_field_patch_publishes_atomically():
    async def run():
        manager, holder = _make_manager(MemoryConfigSource())
        seen = []

        async def consumer(event):
            cfg = event.new_config  # fully published by the time a consumer runs
            seen.append((cfg.memory.extraction_enabled, cfg.vlm.model))

        manager.add_update_consumer(
            scope=ScopeKind.CLUSTER,
            sections={"vlm", "memory"},
            consumer=consumer,
        )
        await manager.patch_cluster({"vlm": {"model": "m2"}, "memory": {"extraction_enabled": False}})
        assert seen == [(False, "m2")]

    asyncio.run(run())


def test_invalid_patch_does_not_persist_or_publish():
    async def run():
        source = MemoryConfigSource()
        manager, holder = _make_manager(source)
        before = holder["config"]
        with pytest.raises(ValueError):
            await manager.patch_cluster({"vlm": {"model": 123, "temperature": "bad"}})
        # temperature is locked -> ConfigPatchError before any persist
        assert holder["config"] is before
        assert await source.load(ConfigScope.cluster()) is None

    asyncio.run(run())


# -- manager: account loading + PATCH -----------------------------------------


def test_get_account_auto_loads_missing_override_and_resolves_fallback():
    async def run():
        manager, _ = _make_manager(MemoryConfigSource())
        vlm = await manager.get_account("ov-a", "vlm")
        assert "ov-a" in manager._accounts
        assert vlm.model is None

    asyncio.run(run())


def test_get_account_rejects_unknown_field():
    async def run():
        manager, _ = _make_manager(MemoryConfigSource())
        with pytest.raises(AttributeError):
            await manager.get_account("ov-a", "unknown")

    asyncio.run(run())


def test_account_patch_sets_override_and_publishes():
    async def run():
        manager, _ = _make_manager(MemoryConfigSource())
        event = await manager.patch_account("ov-a", {"vlm": {"model": "account-model"}})
        assert event.scope.key == "ov-a"
        assert (await manager.get_account("ov-a", "vlm")).model == "account-model"

    asyncio.run(run())


def test_account_storage_wait_does_not_block_unrelated_account_load():
    class BlockingSource(MemoryConfigSource):
        def __init__(self):
            super().__init__()
            self.started = asyncio.Event()
            self.release = asyncio.Event()

        async def update(self, scope, mutate):
            if scope == ConfigScope.account("ov-a"):
                self.started.set()
                await self.release.wait()
            return await super().update(scope, mutate)

    async def run():
        source = BlockingSource()
        manager, _ = _make_manager(source)
        patch = asyncio.create_task(
            manager.patch_account("ov-a", {"vlm": {"model": "slow"}})
        )
        await source.started.wait()

        other = asyncio.create_task(manager.get_account("ov-b", "vlm"))
        assert (await asyncio.wait_for(other, timeout=1)).model is None

        source.release.set()
        await patch

    asyncio.run(run())


def test_refresh_picks_up_out_of_band_write():
    async def run():
        source = MemoryConfigSource()
        manager, _ = _make_manager(source)
        reasons = []

        async def consumer(event):
            reasons.append(event.reason)

        manager.add_update_consumer(
            scope=ScopeKind.ACCOUNT,
            sections={"vlm"},
            consumer=consumer,
        )
        await manager.get_account("ov-a", "vlm")
        # A different writer mutates the same override file directly, bypassing
        # the manager; the periodic refresh re-reads and republishes.
        await source.update(ConfigScope.account("ov-a"), lambda _: {"vlm": {"model": "oob"}})
        await manager.refresh_once()
        assert reasons == [ConfigChangeReason.UPDATE]
        assert (await manager.get_account("ov-a", "vlm")).model == "oob"

    asyncio.run(run())


def test_refresh_does_not_resurrect_unloaded_account():
    async def run():
        source = MemoryConfigSource()
        manager, _ = _make_manager(source)
        await source.update(ConfigScope.account("ov-x"), lambda _: {"vlm": {"model": "x"}})
        # Never loaded: refresh must not create the cache entry (no implicit load).
        await manager.refresh_once()
        assert "ov-x" not in manager._accounts

    asyncio.run(run())



def test_patch_waits_for_matching_consumers_and_filters_registration():
    async def run():
        manager, _ = _make_manager(MemoryConfigSource())
        release = asyncio.Event()
        started = asyncio.Event()
        seen = []

        async def matching(event):
            started.set()
            await release.wait()
            seen.append(event.new_config.vlm.model)

        async def wrong_scope(event):
            seen.append("wrong-scope")

        async def wrong_section(event):
            seen.append("wrong-section")

        manager.add_update_consumer(
            scope=ScopeKind.CLUSTER,
            sections={"vlm"},
            consumer=matching,
        )
        manager.add_update_consumer(
            scope=ScopeKind.ACCOUNT,
            sections={"vlm"},
            consumer=wrong_scope,
        )
        manager.add_update_consumer(
            scope=ScopeKind.CLUSTER,
            sections={"memory"},
            consumer=wrong_section,
        )
        patch_task = asyncio.create_task(manager.patch_cluster({"vlm": {"model": "first"}}))
        await started.wait()
        assert not patch_task.done()
        release.set()
        await patch_task
        assert seen == ["first"]

    asyncio.run(run())


def test_idle_evict_notifies_all_account_consumers(monkeypatch):
    import time

    clock = [100.0]
    monkeypatch.setattr(time, "monotonic", lambda: clock[0])

    async def run():
        source = MemoryConfigSource()
        manager, _ = _make_manager(source)
        reasons = []
        lifecycle_reasons = []

        async def consumer(event):
            reasons.append(event.reason)

        async def lifecycle_consumer(event):
            lifecycle_reasons.append(event.reason)

        manager.add_update_consumer(
            scope=ScopeKind.ACCOUNT,
            sections={"vlm"},
            consumer=consumer,
        )
        manager.add_update_consumer(
            scope=ScopeKind.ACCOUNT,
            sections={"switch"},
            consumer=lifecycle_consumer,
        )
        await manager.get_account("ov-a", "vlm")
        await manager.patch_account("ov-a", {"vlm": {"model": "a"}})
        clock[0] += 24 * 60 * 60 + 1
        await manager.refresh_once()

        assert reasons == [
            ConfigChangeReason.UPDATE,
            ConfigChangeReason.EVICT,
        ]
        assert lifecycle_reasons == [ConfigChangeReason.EVICT]
        assert (await manager.get_account("ov-a", "vlm")).model == "a"
        assert await source.load(ConfigScope.account("ov-a")) == {"vlm": {"model": "a"}}

    asyncio.run(run())


def test_delete_account_clears_source_cache_and_notifies_consumers():
    async def run():
        source = MemoryConfigSource()
        manager, _ = _make_manager(source)
        reasons = []

        async def consumer(event):
            reasons.append(event.reason)

        manager.add_update_consumer(
            scope=ScopeKind.ACCOUNT,
            sections={"vlm"},
            consumer=consumer,
        )
        await manager.patch_account("ov-a", {"vlm": {"model": "old"}})
        assert (await manager.get_account("ov-a", "vlm")).model == "old"

        await manager.delete_account("ov-a")
        assert await source.load(ConfigScope.account("ov-a")) is None
        assert "ov-a" not in manager._accounts
        assert reasons == [ConfigChangeReason.UPDATE, ConfigChangeReason.EVICT]

        # Reusing the account ID starts from a clean scope.
        assert (await manager.get_account("ov-a", "vlm")).model is None

    asyncio.run(run())


def test_refresh_does_not_keep_idle_accounts_alive(monkeypatch):
    import time

    clock = [100.0]
    monkeypatch.setattr(time, "monotonic", lambda: clock[0])

    async def run():
        manager, _ = _make_manager(MemoryConfigSource())
        await manager.get_account("ov-a", "vlm")
        clock[0] += 24 * 60 * 60 + 1  # idle past the eviction TTL
        await manager.refresh_once()  # evicts the idle account
        assert "ov-a" not in manager._accounts
        assert (await manager.get_account("ov-a", "vlm")).model is None
        assert "ov-a" in manager._accounts

    asyncio.run(run())


def test_refresh_retries_failed_load_without_another_storage_change():
    class FlakySource(MemoryConfigSource):
        fail_scope = None

        async def load(self, scope):
            if self.fail_scope is not None and scope == self.fail_scope:
                self.fail_scope = None
                raise OSError("temporary read failure")
            return await super().load(scope)

    async def run():
        source = FlakySource()
        manager, _ = _make_manager(source)
        await manager.get_account("ov-a", "vlm")
        await source.update(ConfigScope.account("ov-a"), lambda _: {"vlm": {"model": "new"}})
        source.fail_scope = ConfigScope.account("ov-a")
        await manager.refresh_once()
        assert (await manager.get_account("ov-a", "vlm")).model is None
        await manager.refresh_once()
        assert (await manager.get_account("ov-a", "vlm")).model == "new"

    asyncio.run(run())


def test_refresh_does_not_resurrect_evicted_account():
    async def run():
        source = MemoryConfigSource()
        manager, _ = _make_manager(source)
        # never loaded; a stray storage change must not create the account
        await source.update(ConfigScope.account("ov-x"), lambda _: {"vlm": {"model": "x"}})
        await manager.refresh_once()
        assert "ov-x" not in manager._accounts

    asyncio.run(run())


# -- AccountConfig field attributes + manager fallback ------------------------


def test_account_config_field_attributes():
    fields = AccountConfig.model_fields
    assert set(fields) == {"acl", "agent_evolution", "github"}
    assert is_dynamic(fields["github"])
    assert fallback_of(fields["github"]) is None
    assert is_dynamic(fields["agent_evolution"])
    assert fallback_of(fields["agent_evolution"]) == "agent_evolution"
    assert collect_frozen_paths(AccountConfig) == set()


def test_account_config_ignores_inactive_sections_during_known_patch():
    async def run():
        from openviking.config.binding import manager_over_source
        from openviking_cli.utils.config import set_openviking_config
        from openviking_cli.utils.config.open_viking_config import (
            OpenVikingConfig,
            OpenVikingConfigSingleton,
        )

        source = MemoryConfigSource()
        scope = ConfigScope.account("ov-a")
        inactive_section = {"model": "unused"}
        await source.update(scope, lambda _: {"vlm": inactive_section})

        base = OpenVikingConfig.from_dict({})
        set_openviking_config(base)
        manager = manager_over_source(source, base_config=base)
        await manager.initialize()
        try:
            assert await manager.get_account("ov-a", "acl") is None
            await manager.patch_account("ov-a", {"acl": {"enabled": True}})
            assert await source.load(scope) == {
                "vlm": inactive_section,
                "acl": {"enabled": True},
            }
        finally:
            OpenVikingConfigSingleton.reset_instance()

    asyncio.run(run())


def test_account_patch_rejects_inactive_fields_and_allows_active_fields():
    validate_patch(AccountConfig, {"github": {"token": "t"}})
    with pytest.raises(ConfigPatchError, match="github.tokne"):
        validate_patch(AccountConfig, {"github": {"tokne": "t"}})
    with pytest.raises(ConfigPatchError):
        validate_patch(AccountConfig, {"vlm": {"model": "m"}})
    with pytest.raises(ConfigPatchError):
        validate_patch(AccountConfig, {"embedding": {"dense": {"model": "m"}}}, creating=True)


def test_manager_resolves_account_override_fallback_and_no_fallback():
    async def run():
        manager, holder = _make_manager(MemoryConfigSource())
        holder["config"] = ClusterConfig(vlm=VLMSection(model="cluster-vlm"))

        assert (await manager.get_account("ov-a", "vlm")).model == "cluster-vlm"
        assert await manager.get_account("ov-a", "switch") is None

        await manager.patch_account(
            "ov-a",
            {
                "vlm": {"model": "account-vlm"},
                "switch": {"enabled": True},
            },
        )
        assert (await manager.get_account("ov-a", "vlm")).model == "account-vlm"
        assert (await manager.get_account("ov-a", "switch")).enabled is True

    asyncio.run(run())


def test_account_config_acl_agent_evolution_are_dynamic_no_fallback():
    fields = AccountConfig.model_fields
    for name in ("acl", "github"):
        assert is_dynamic(fields[name])  # writable at create + PATCH
        assert fallback_of(fields[name]) is None  # no cluster fallback
    assert fallback_of(fields["agent_evolution"]) == "agent_evolution"
    # dynamic => not frozen; a normal PATCH may set them.
    validate_patch(AccountConfig, {"acl": {"enabled": True}})
    validate_patch(AccountConfig, {"agent_evolution": {"enabled": False}})


def test_runtime_cluster_ignores_future_top_level_override_and_preserves_it():
    async def run():
        from openviking.config.binding import manager_over_source
        from openviking_cli.utils.config import set_openviking_config
        from openviking_cli.utils.config.open_viking_config import (
            OpenVikingConfig,
            OpenVikingConfigSingleton,
        )

        source = MemoryConfigSource()
        future_section = {"enabled": True, "nested": {"version": 2}}
        await source.update(ConfigScope.cluster(), lambda _: {"future_section": future_section})
        base = OpenVikingConfig.from_dict({})
        set_openviking_config(base)
        manager = manager_over_source(source, base_config=base)
        await manager.initialize()
        try:
            await manager.patch_cluster({"agent_evolution": {"enabled": True}})
            assert (await source.load(ConfigScope.cluster())) == {
                "future_section": future_section,
                "agent_evolution": {"enabled": True},
            }
        finally:
            OpenVikingConfigSingleton.reset_instance()

    asyncio.run(run())


def test_persisted_known_sections_ignore_future_nested_fields():
    from openviking.config.binding import _build_account, _build_cluster
    from openviking_cli.utils.config.open_viking_config import OpenVikingConfig

    cluster = _build_cluster(
        OpenVikingConfig.from_dict({}),
        {"agent_evolution": {"enabled": True, "future_option": 1}},
    )
    assert cluster.agent_evolution.enabled is True

    account = _build_account(
        {
            "github": {"token": "t", "future_option": True},
            "acl": {"enabled": True, "retired_field": False},
        }
    )
    assert account.github.token == "t"
    assert account.acl.enabled is True


def test_openviking_config_ignores_unknown_top_level_field():
    from openviking_cli.utils.config.open_viking_config import OpenVikingConfig

    config = OpenVikingConfig.from_dict({"future_section": {"enabled": True}})

    assert "future_section" not in config.model_dump()


def test_manager_fallback_reads_latest_cluster_snapshot():
    async def run():
        manager, _ = _make_manager(MemoryConfigSource())
        await manager.patch_cluster({"vlm": {"model": "v1"}})
        assert (await manager.get_account("ov-a", "vlm")).model == "v1"
        await manager.patch_cluster({"vlm": {"model": "v2"}})
        assert (await manager.get_account("ov-a", "vlm")).model == "v2"

    asyncio.run(run())


def test_real_agent_evolution_precedence_and_fallback():
    async def run():
        from openviking.config.binding import manager_over_source
        from openviking_cli.utils.config import set_openviking_config
        from openviking_cli.utils.config.open_viking_config import (
            OpenVikingConfig,
            OpenVikingConfigSingleton,
        )

        base = OpenVikingConfig.from_dict(
            {"agent_evolution": {"enabled": False}}
        )
        set_openviking_config(base)
        manager = manager_over_source(MemoryConfigSource(), base_config=base)
        await manager.initialize()
        try:
            assert not (await manager.get_account("ov-a", "agent_evolution")).enabled

            await manager.patch_cluster({"agent_evolution": {"enabled": True}})
            assert (await manager.get_account("ov-a", "agent_evolution")).enabled

            await manager.patch_account(
                "ov-a", {"agent_evolution": {"enabled": False}}
            )
            assert not (await manager.get_account("ov-a", "agent_evolution")).enabled

            await manager.patch_account("ov-a", {"agent_evolution": None})
            assert (await manager.get_account("ov-a", "agent_evolution")).enabled
        finally:
            OpenVikingConfigSingleton.reset_instance()

    asyncio.run(run())


# -- FileConfigSource plaintext read / magic write ----------------------------


class _FakeAGFS:
    """Minimal in-memory AGFS stand-in for FileConfigSource tests."""

    def __init__(self):
        self.files: dict[str, bytes] = {}

    async def read(self, path, *a, **k):
        from openviking.pyagfs import AGFSNotFoundError

        if path not in self.files:
            raise AGFSNotFoundError(path)
        return self.files[path]

    async def write(self, path, data, *a, **k):
        self.files[path] = data if isinstance(data, bytes) else bytes(data)
        return path

    async def ensure_parent_dirs(self, path, *a, **k):
        return {}

    async def mv(self, old_path, new_path, **kwargs):
        self.files[new_path] = self.files.pop(old_path)

    async def rm(self, path, *a, **k):
        self.files.pop(path, None)
        return {}

    async def stat(self, path, *a, **k):
        from openviking.pyagfs import AGFSNotFoundError

        if path not in self.files:
            raise AGFSNotFoundError(path)
        return {"size": len(self.files[path]), "modTime": 1}

    async def pathlock_acquire_exact(self, path, *a, **k):
        return {"lease_ref": "lease-1"}

    async def pathlock_release(self, lease, *a, **k):
        return None


def test_file_source_reads_and_writes_plaintext_json():
    async def run():
        import json

        agfs = _FakeAGFS()
        account_path = "/local/ov-a/_system/setting.json"
        agfs.files[account_path] = json.dumps({"vlm": {"model": "legacy"}}).encode("utf-8")

        source = FileConfigSource(agfs)
        scope = ConfigScope.account("ov-a")
        assert await source.load(scope) == {"vlm": {"model": "legacy"}}

        await source.update(scope, lambda cur: {**(cur or {}), "vlm": {"model": "new"}})
        # Written as plaintext JSON; AGFS applies transparent encryption at rest,
        # so this layer never adds an application-level codec.
        assert json.loads(agfs.files[account_path].decode("utf-8")) == {"vlm": {"model": "new"}}
        assert agfs.files["/local/ov-a/_system/setting.backup.json"] == json.dumps(
            {"vlm": {"model": "legacy"}}
        ).encode("utf-8")
        assert await source.load(scope) == {"vlm": {"model": "new"}}

    asyncio.run(run())


def test_file_source_cluster_scope_path_and_load():
    async def run():
        agfs = _FakeAGFS()
        source = FileConfigSource(agfs)
        scope = ConfigScope.cluster()
        assert await source.load(scope) is None  # no override yet
        await source.update(scope, lambda cur: {"memory": {"extraction_enabled": False}})
        assert "/local/_system/runtime_config/cluster.json" in agfs.files
        assert await source.load(scope) == {"memory": {"extraction_enabled": False}}

    asyncio.run(run())


def test_file_source_failed_publish_preserves_previous_file():
    class FailingAGFS(_FakeAGFS):
        fail_next_target_write = True

        async def write(self, path, data, *args, **kwargs):
            if path.endswith("/setting.json") and self.fail_next_target_write:
                self.fail_next_target_write = False
                raise OSError("publish failed")
            return await super().write(path, data, *args, **kwargs)

    async def run():
        agfs = FailingAGFS()
        path = "/local/ov-a/_system/setting.json"
        agfs.files[path] = b'{"github":{"token":"old"}}'
        source = FileConfigSource(agfs)
        with pytest.raises(OSError, match="publish failed"):
            await source.update(ConfigScope.account("ov-a"), lambda _: {"github": {"token": "new"}})
        assert await source.load(ConfigScope.account("ov-a")) == {"github": {"token": "old"}}

    asyncio.run(run())


def test_file_source_recovers_from_backup_when_publish_and_rollback_are_interrupted():
    class InterruptedAGFS(_FakeAGFS):
        async def write(self, path, data, *args, **kwargs):
            if path.endswith("/setting.json"):
                self.files[path] = b'{"github":'
                raise OSError("interrupted publish")
            return await super().write(path, data, *args, **kwargs)

    async def run():
        agfs = InterruptedAGFS()
        path = "/local/ov-a/_system/setting.json"
        agfs.files[path] = b'{"github":{"token":"old"}}'
        source = FileConfigSource(agfs)

        with pytest.raises(OSError, match="interrupted publish"):
            await source.update(
                ConfigScope.account("ov-a"),
                lambda _: {"github": {"token": "new"}},
            )

        assert agfs.files[path] == b'{"github":'
        assert await source.load(ConfigScope.account("ov-a")) == {"github": {"token": "old"}}

    asyncio.run(run())


def test_file_source_rejects_invalid_config_without_backup():
    async def run():
        agfs = _FakeAGFS()
        path = "/local/ov-a/_system/setting.json"
        agfs.files[path] = b'{"github":'

        source = FileConfigSource(agfs)

        with pytest.raises(RuntimeError, match="no valid backup"):
            await source.load(ConfigScope.account("ov-a"))

    asyncio.run(run())


def test_refresh_keeps_last_value_after_corruption_and_retries():
    async def run():
        import json

        agfs = _FakeAGFS()
        path = "/local/ov-a/_system/setting.json"
        agfs.files[path] = json.dumps({"vlm": {"model": "old"}}).encode()
        manager, _ = _make_manager(FileConfigSource(agfs))
        await manager.initialize()
        assert (await manager.get_account("ov-a", "vlm")).model == "old"

        agfs.files[path] = b'{"vlm":'
        await manager.refresh_once()
        assert (await manager.get_account("ov-a", "vlm")).model == "old"

        agfs.files[path] = json.dumps({"vlm": {"model": "new"}}).encode()
        await manager.refresh_once()
        assert (await manager.get_account("ov-a", "vlm")).model == "new"

    asyncio.run(run())


def test_file_source_supports_repeated_update_and_idempotent_delete():
    async def run():
        agfs = _FakeAGFS()
        source = FileConfigSource(agfs)
        scope = ConfigScope.account("ov-a")

        await source.update(scope, lambda _: {"acl": {"enabled": True}})
        await source.update(scope, lambda _: {"acl": {"enabled": False}})
        assert await source.load(scope) == {"acl": {"enabled": False}}

        await source.delete(scope)
        await source.delete(scope)
        assert await source.load(scope) is None
        assert "/local/ov-a/_system/setting.backup.json" not in agfs.files

    asyncio.run(run())


# -- registry / scope / startup assembly --------------------------------------


def test_registry_create_known_and_unknown():
    src = create_config_source("memory", ConfigSourceContext())
    assert isinstance(src, MemoryConfigSource)
    with pytest.raises(ValueError):
        create_config_source("does-not-exist", ConfigSourceContext())


def test_scope_rejects_bad_account_and_cluster_key():
    with pytest.raises(ValueError):
        ConfigScope(ScopeKind.ACCOUNT, "../etc")
    with pytest.raises(ValueError):
        ConfigScope(ScopeKind.CLUSTER, "unexpected-key")


def test_build_config_source_rejects_kernel_owned_file_source():
    from openviking.config import build_config_source

    # ``file`` is kernel-owned because it is the only source allowed to see AGFS.
    with pytest.raises(ValueError, match="kernel-owned"):
        build_config_source(None)


def test_plugin_source_receives_params_without_kernel_dependencies():
    from openviking.config import build_config_source, register_config_source

    captured = {}

    @register_config_source("params-only-test")
    def _build_plugin_source(ctx):
        captured["ctx"] = ctx
        return MemoryConfigSource(ctx)

    src = build_config_source(
        {"source": "params-only-test", "params": {"namespace": "ns-1"}}
    )
    assert isinstance(src, MemoryConfigSource)
    assert captured["ctx"].params == {"namespace": "ns-1"}
    assert not hasattr(captured["ctx"], "extras")


def test_build_config_source_loads_configured_plugin_module(monkeypatch):
    from openviking.config import assembly, build_config_source, register_config_source

    loaded = []

    def load_module(module_path):
        loaded.append(module_path)

        @register_config_source("module-loaded-test")
        def _build_module_source(ctx):
            return MemoryConfigSource(ctx)

    monkeypatch.setattr(assembly, "load_config_source_module", load_module)
    source = build_config_source(
        {
            "source": "module-loaded-test",
            "module": "company.openviking.config_source",
            "params": {"namespace": "module"},
        }
    )

    assert isinstance(source, MemoryConfigSource)
    assert loaded == ["company.openviking.config_source"]


def test_runtime_manager_constructs_file_source_inside_kernel():
    from openviking.config.binding import build_runtime_config_manager
    from openviking_cli.utils.config import set_openviking_config
    from openviking_cli.utils.config.open_viking_config import (
        OpenVikingConfig,
        OpenVikingConfigSingleton,
    )

    agfs = _FakeAGFS()
    base = OpenVikingConfig.from_dict({})
    set_openviking_config(base)
    try:
        manager = build_runtime_config_manager(
            agfs,
            settings={"source": "file"},
            base_config=base,
        )
        assert isinstance(manager._source, FileConfigSource)
        assert manager._source._client is agfs
    finally:
        OpenVikingConfigSingleton.reset_instance()


def test_runtime_config_settings_ignores_unknown_field():
    from openviking.config import RuntimeConfigSettings

    settings = RuntimeConfigSettings(bogus=1)

    assert settings == RuntimeConfigSettings()
