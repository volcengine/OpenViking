# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0
"""Runtime config manager: merge, publish, invalidation.

The manager owns everything that is storage-independent: three-state PATCH
merge, per-scope copy-on-write publish, old/new diff, and consumer notification.
It depends only on the :class:`ConfigSource` abstraction and never imports a
concrete source implementation.

Two scopes are managed side by side, mirroring the two independent config models
(the cluster ``OpenVikingConfig`` and the per-account ``AccountConfig``):

- **cluster** — a single process-wide config object, bridged through the
  ``get_config`` / ``set_config`` / ``build_config`` hooks so the manager stays
  agnostic to the concrete cluster model.
- **account** — a per-account cache of ``(sparse override, constructed account
  config, last-access time)``. ``get_account(account_id, field)`` loads the
  account on demand and resolves the field's declared cluster fallback.

Both models are built and validated by caller-supplied hooks, so the manager
imports neither ``OpenVikingConfig`` nor ``AccountConfig`` directly.
"""

from __future__ import annotations

import asyncio
import time
import weakref
from dataclasses import dataclass
from enum import Enum
from typing import Any, Awaitable, Callable, Generic, Optional, TypeVar

from openviking.config.merge import apply_three_state_patch, diff_sections
from openviking.config.scope import ConfigScope, ScopeKind
from openviking.config.source.base import ConfigSource
from openviking.service.task_tracker_concurrency import OwnerLoopDispatcher, run_to_completion
from openviking_cli.utils.config.runtime_field import fallback_of, resolve_fallback
from openviking_cli.utils.logger import get_logger

logger = get_logger(__name__)

# C = cluster config type; A = per-account config type.
C = TypeVar("C")
A = TypeVar("A")
T = TypeVar("T")


@dataclass(frozen=True)
class AccountConfigView(Generic[C, A]):
    """Resolve fields against a captured account/cluster publication pair.

    Used only by synchronous selectors, never as a request context. Returned
    models are shared, read-only configuration values.
    """

    _account: A
    _cluster: C

    @property
    def account(self) -> A:
        """Return the captured sparse account configuration."""
        return self._account

    @property
    def cluster(self) -> C:
        """Return the captured cluster configuration."""
        return self._cluster

    def get(self, field: str) -> Any:
        fields = getattr(type(self._account), "model_fields", {})
        if field not in fields:
            raise AttributeError(field)
        value = getattr(self._account, field)
        if value is not None:
            return value
        target = fallback_of(fields[field])
        return resolve_fallback(self._cluster, target) if target is not None else None

REFRESH_INTERVAL_SECS = 30.0
# Accounts unused for this long are evicted and stop being polled.
ACCOUNT_IDLE_TTL_SECS = 24 * 60 * 60.0


@dataclass(frozen=True)
class ConfigChangeEvent:
    """Emitted after a new config is published.

    ``scope`` names the changed override (cluster-wide or one account).
    ``old_config`` / ``new_config`` are the objects for that scope (the cluster
    config, or that account's :class:`AccountConfig`; ``None`` when the account
    scope was cleared or evicted). ``reason`` distinguishes ordinary config
    publication from manager-owned account lifecycle events.
    """

    scope: ConfigScope
    changed_sections: frozenset[str]
    old_config: Any
    new_config: Any
    reason: "ConfigChangeReason"


class ConfigChangeReason(str, Enum):
    UPDATE = "update"
    EVICT = "evict"


ConfigChangeConsumer = Callable[[ConfigChangeEvent], Awaitable[None]]


@dataclass(frozen=True)
class _ConsumerRegistration:
    scope: ScopeKind
    sections: frozenset[str]
    consumer: ConfigChangeConsumer


@dataclass
class _AccountEntry(Generic[A]):
    """One account's cached state under the manager's owner loop.

    ``config`` is the constructed, already-validated account model;
    ``last_access`` drives idle eviction.
    """

    config: A
    last_access: float


class RuntimeConfigManager(Generic[C, A]):
    """Coordinate override merge/publish/invalidation over a :class:`ConfigSource`.

    ``base_config`` is the caller-owned immutable startup baseline.
    ``get_config`` / ``set_config`` bridge to the process-wide cluster singleton;
    ``build_config`` rebuilds the cluster model from the baseline plus a complete
    cluster override; ``build_account`` constructs an account model from its
    sparse override. Supplying these as hooks keeps the manager free of any
    concrete config-model import.
    """

    def __init__(
        self,
        source: ConfigSource,
        *,
        base_config: C,
        get_config: Callable[[], C],
        set_config: Callable[[C], None],
        build_config: Callable[[C, dict], C],
        build_account: Callable[[Optional[dict]], A],
        validate_request: Optional[Callable[[dict, bool, bool], None]] = None,
    ) -> None:
        self._source = source
        self._dispatcher = OwnerLoopDispatcher()
        # Immutable startup baseline. Cluster overrides are always rebuilt from
        # this object, never from the previously published effective config.
        self._base_config = base_config
        self._get_config = get_config
        self._set_config = set_config
        # build_config(old_cluster, cluster_override) -> new validated cluster config.
        self._build_config = build_config
        # build_account(sparse_override) -> constructed & validated account config.
        # The manager persists and builds from the *sparse* override only; fallback
        # is never materialized. AccountConfig's own @model_validator(after) runs at
        # construction, but it only cross-checks fields the account set explicitly
        # (see _check_embedding_vectordb_dim), so it never needs the cluster here.
        self._build_account = build_account
        # validate_request(patch, is_account_scope, creating) -> None (structural gate).
        self._validate_request = validate_request
        # Per-account cache; only touched on the owner loop.
        self._accounts: dict[str, _AccountEntry[A]] = {}
        self._overrides: dict[ConfigScope, Optional[dict]] = {}
        self._consumers: list[_ConsumerRegistration] = []
        # Per-scope local locks serialize same-node concurrent PATCHes so two
        # requests mutating the same scope can't interleave read-merge-write.
        self._scope_locks: weakref.WeakValueDictionary[tuple, asyncio.Lock] = (
            weakref.WeakValueDictionary()
        )
        self._publish_lock = asyncio.Lock()
        self._notification_lock = asyncio.Lock()
        self._refresh_task: Optional[asyncio.Task] = None

    # -- consumers ------------------------------------------------------------

    def add_update_consumer(
        self,
        *,
        scope: ScopeKind,
        sections: set[str] | frozenset[str],
        consumer: ConfigChangeConsumer,
    ) -> None:
        """Register a consumer for one scope kind and one or more sections."""
        if not sections:
            raise ValueError("config consumer must register at least one section")
        self._consumers.append(
            _ConsumerRegistration(
                scope=scope,
                sections=frozenset(sections),
                consumer=consumer,
            )
        )

    # -- cluster loading -----------------------------------------------------

    async def initialize(self) -> None:
        """Load cluster defaults before serving requests."""
        self._dispatcher.bind_current_loop()
        await self._refresh_scope(ConfigScope.cluster(), reason=ConfigChangeReason.UPDATE)

    async def replace_base_config(self, base_config: C) -> ConfigChangeEvent:
        """Replace the startup baseline without persisting a runtime override."""
        return await self._dispatcher.run(
            lambda: run_to_completion(lambda: self._replace_base_config(base_config))
        )

    async def _replace_base_config(self, base_config: C) -> ConfigChangeEvent:
        scope = ConfigScope.cluster()
        async with self._lock_for(scope):
            async with self._publish_lock:
                self._base_config = base_config
                event = self._publish(
                    scope,
                    self._overrides.get(scope),
                    reason=ConfigChangeReason.UPDATE,
                )
        await self._notify(event)
        return event

    # -- account loading -----------------------------------------------------

    async def _ensure_loaded(self, account_id: str) -> None:
        """Load one account's override and construct its config, once.

        Idempotent and concurrency-safe per account. Absence of a file records
        "loaded, no override"; a read/decrypt/validate failure raises and does
        not fall back to another account's config.
        """
        entry = self._accounts.get(account_id)
        if entry is not None:
            entry.last_access = time.monotonic()
            return
        scope = ConfigScope.account(account_id)
        async with self._lock_for(scope):
            if account_id in self._accounts:
                self._accounts[account_id].last_access = time.monotonic()
                return
            override = await self._source.load(scope)
            async with self._publish_lock:
                self._accounts[account_id] = _AccountEntry(
                    config=self._build_account(override),
                    last_access=time.monotonic(),
                )
                self._overrides[scope] = override

    async def get_account(self, account_id: str, field: str) -> Any:
        """Load an account on demand and return one effective config field."""
        return await self.resolve_account(account_id, lambda view: view.get(field))

    async def resolve_account(
        self, account_id: str, resolver: Callable[[AccountConfigView[C, A]], T]
    ) -> T:
        """Select a domain value from one publication pair.

        Selectors must be synchronous and perform no I/O or configuration
        mutations. Loading and cancellation have the same contract as get_account.
        """
        return await self._dispatcher.run(
            lambda: run_to_completion(lambda: self._resolve_account(account_id, resolver))
        )

    async def _resolve_account(
        self, account_id: str, resolver: Callable[[AccountConfigView[C, A]], T]
    ) -> T:
        await self._ensure_loaded(account_id)
        # No await between capturing the publications and running the selector.
        view = AccountConfigView(self._accounts[account_id].config, self._get_config())
        result = resolver(view)
        import inspect

        if inspect.isawaitable(result):
            if inspect.iscoroutine(result):
                result.close()
            raise TypeError("account configuration resolver must be synchronous")
        return result

    async def get_settings(self, scope: ConfigScope) -> dict:
        """Read explicit overrides, never the merged configuration."""
        import copy

        return copy.deepcopy(await self._dispatcher.run(lambda: self._source.load(scope)) or {})

    def validate_initial_settings(self, account_id: str, settings: dict) -> None:
        """Validate creation-time settings without persisting them."""
        ConfigScope.account(account_id)
        if self._validate_request is not None:
            self._validate_request(settings, True, True)
        # Construct-to-validate: section-internal and cross-section validators run.
        self._build_account(settings)

    # -- PATCH ---------------------------------------------------------------

    async def patch_cluster(self, patch: dict) -> ConfigChangeEvent:
        """Apply a three-state PATCH to the cluster override and publish."""
        scope = ConfigScope.cluster()
        return await self._dispatcher.run(
            lambda: run_to_completion(lambda: self._patch(scope, patch))
        )

    async def patch_account(
        self, account_id: str, patch: dict, *, creating: bool = False
    ) -> ConfigChangeEvent:
        """Apply a three-state PATCH to one account override and publish."""
        scope = ConfigScope.account(account_id)
        return await self._dispatcher.run(
            lambda: run_to_completion(lambda: self._patch(scope, patch, creating=creating))
        )

    async def delete_account(self, account_id: str) -> ConfigChangeEvent:
        """Delete one account override and evict all manager-owned state."""
        scope = ConfigScope.account(account_id)
        return await self._dispatcher.run(
            lambda: run_to_completion(lambda: self._delete_account(scope))
        )

    async def _delete_account(self, scope: ConfigScope) -> ConfigChangeEvent:
        assert scope.key is not None
        async with self._lock_for(scope):
            await self._source.delete(scope)
            async with self._publish_lock:
                entry = self._accounts.pop(scope.key, None)
                self._overrides.pop(scope, None)
                event = self._account_event(
                    scope.key,
                    entry.config if entry is not None else None,
                    None,
                    reason=ConfigChangeReason.EVICT,
                )
        await self._notify(event)
        return event

    async def _patch(
        self, scope: ConfigScope, patch: dict, *, creating: bool = False
    ) -> ConfigChangeEvent:
        is_account = scope.kind is ScopeKind.ACCOUNT
        if self._validate_request is not None:
            self._validate_request(patch, is_account, creating)
        async with self._lock_for(scope):

            def mutate(current: Optional[dict]) -> dict:
                override = apply_three_state_patch(current, patch)
                if self._validate_request is not None and not creating:
                    self._validate_resets(current or {}, patch, is_account)
                # Construct-to-validate before persisting.
                self._build_candidate(scope, override)
                return override

            override = await self._source.update(scope, mutate)
            async with self._publish_lock:
                event = self._publish(scope, override, reason=ConfigChangeReason.UPDATE)
        await self._notify(event)
        return event

    def _validate_resets(self, current: dict, patch: dict, is_account: bool) -> None:
        """A parent reset must not erase creation-only overrides beneath it."""

        def removed(node: dict, changes: dict) -> dict:
            result = {}
            for key, value in changes.items():
                if value is None and key in node:
                    result[key] = node[key]
                elif isinstance(value, dict) and isinstance(node.get(key), dict):
                    nested = removed(node[key], value)
                    if nested:
                        result[key] = nested
            return result

        assert self._validate_request is not None
        self._validate_request(removed(current, patch), is_account, False)

    # -- copy-on-write publish -----------------------------------------------

    def _build_candidate(self, scope: ConfigScope, override: Optional[dict]) -> Any:
        """Build (and thereby validate) the new object for a scope's override."""
        if scope.kind is ScopeKind.CLUSTER:
            return self._build_config(self._base_config, override or {})
        return self._build_account(override)

    def _publish(
        self,
        scope: ConfigScope,
        override: Optional[dict],
        *,
        reason: ConfigChangeReason,
    ) -> ConfigChangeEvent:
        """Swap in the new object for a scope and return the change event.

        Cluster publish swaps the singleton pointer; account publish replaces the
        cache entry. Either way business code only ever sees a fully-built object,
        never a partially-mutated one.
        """
        if scope.kind is ScopeKind.CLUSTER:
            old = self._get_config()
            new = self._build_config(self._base_config, override or {})
            self._set_config(new)
            self._overrides[scope] = override
            return ConfigChangeEvent(
                scope=scope,
                changed_sections=diff_sections(old, new),
                old_config=old,
                new_config=new,
                reason=reason,
            )
        assert scope.key is not None
        account_id = scope.key
        old_entry = self._accounts.get(account_id)
        old_config = old_entry.config if old_entry is not None else None
        new_config = self._build_account(override)
        self._accounts[account_id] = _AccountEntry(
            config=new_config,
            last_access=(old_entry.last_access if old_entry else time.monotonic()),
        )
        self._overrides[scope] = override
        return self._account_event(account_id, old_config, new_config, reason=reason)

    def _account_event(
        self,
        account_id: str,
        old_config: Any,
        new_config: Any,
        *,
        reason: ConfigChangeReason,
    ) -> ConfigChangeEvent:
        return ConfigChangeEvent(
            scope=ConfigScope.account(account_id),
            changed_sections=diff_sections(old_config, new_config),
            old_config=old_config,
            new_config=new_config,
            reason=reason,
        )

    async def _notify(self, event: ConfigChangeEvent) -> None:
        """Await matching consumers in registration order after publication."""
        is_evict = event.reason is ConfigChangeReason.EVICT
        if not event.changed_sections and not is_evict:
            return
        async with self._notification_lock:
            for registration in self._consumers:
                if registration.scope is not event.scope.kind:
                    continue
                if not is_evict and registration.sections.isdisjoint(event.changed_sections):
                    continue
                try:
                    await registration.consumer(event)
                except Exception:
                    logger.exception("Config change consumer failed")

    def _lock_for(self, scope: ConfigScope) -> asyncio.Lock:
        key = (scope.kind.value, scope.key)
        lock = self._scope_locks.get(key)
        if lock is None:
            lock = asyncio.Lock()
            self._scope_locks[key] = lock
        return lock

    # -- periodic refresh ----------------------------------------------------

    def start_refresh_loop(self, interval_secs: float = REFRESH_INTERVAL_SECS) -> None:
        if self._refresh_task is None or self._refresh_task.done():
            self._refresh_task = asyncio.create_task(self._refresh_loop(interval_secs))

    async def stop_refresh_loop(self) -> None:
        task = self._refresh_task
        self._refresh_task = None
        if task is not None:
            task.cancel()
            try:
                await task
            except asyncio.CancelledError:
                pass

    async def _refresh_loop(self, interval_secs: float) -> None:
        while True:
            try:
                await asyncio.sleep(interval_secs)
                await self.refresh_once()
            except asyncio.CancelledError:
                raise
            except Exception:
                logger.exception("Runtime config refresh iteration failed")

    async def refresh_once(self) -> None:
        """Run one refresh on the configuration owner's loop."""
        await self._dispatcher.run(self._refresh_once)

    async def _refresh_once(self) -> None:
        """Reload cluster + hot accounts once, publishing any observed change.

        Every active scope is reloaded and diffed against the last published
        override (``_refresh_scope``); the manager owns change detection, so the
        source stays stateless. Cold accounts (idle > TTL) are evicted and stop
        being reloaded. On load or publish failure the last effective value is
        kept, and the next refresh retries the active scope.
        """
        async with self._publish_lock:
            evictions = self._evict_cold_accounts()
        for event in evictions:
            await self._notify(event)
        scopes = [ConfigScope.cluster()] + [
            ConfigScope.account(account_id) for account_id in list(self._accounts)
        ]
        for scope in scopes:
            try:
                await self._refresh_scope(scope, reason=ConfigChangeReason.UPDATE)
            except Exception:
                logger.warning(
                    "Runtime config refresh for %s failed; keeping last value",
                    scope,
                    exc_info=True,
                )

    async def _refresh_scope(
        self,
        scope: ConfigScope,
        *,
        reason: ConfigChangeReason,
    ) -> None:
        async with self._lock_for(scope):
            override = await self._source.load(scope)
            if scope in self._overrides and self._overrides[scope] == override:
                return
            async with self._publish_lock:
                if scope.kind is ScopeKind.ACCOUNT:
                    assert scope.key is not None
                    if scope.key not in self._accounts:
                        # A refresh must not resurrect an evicted/never-loaded account.
                        return
                event = self._publish(scope, override, reason=reason)
        await self._notify(event)

    def _evict_cold_accounts(self) -> list[ConfigChangeEvent]:
        now = time.monotonic()
        cold = [
            account_id
            for account_id, entry in self._accounts.items()
            if now - entry.last_access > ACCOUNT_IDLE_TTL_SECS
        ]
        events: list[ConfigChangeEvent] = []
        for account_id in cold:
            entry = self._accounts.pop(account_id)
            self._overrides.pop(ConfigScope.account(account_id), None)
            events.append(
                self._account_event(
                    account_id,
                    entry.config,
                    None,
                    reason=ConfigChangeReason.EVICT,
                )
            )
        return events
