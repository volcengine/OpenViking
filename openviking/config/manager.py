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
- **account** — a per-account cache of the constructed Account configuration
  and its last-access time. Account and Cluster configurations remain distinct.
  ``get_account(account_id, field)`` returns the Account value, except for the
  deprecated whole-section fallback retained for existing fields.

Both models are built and validated by caller-supplied hooks, so the manager
imports neither ``OpenVikingConfig`` nor ``AccountConfig`` directly.
"""

from __future__ import annotations

import asyncio
import threading
import time
from concurrent.futures import Future as ConcurrentFuture
from dataclasses import dataclass
from enum import Enum
from typing import Any, Awaitable, Callable, Generic, Iterable, Optional, TypeVar

from openviking.config.merge import apply_three_state_patch, diff_sections
from openviking.config.scope import ConfigScope, ScopeKind
from openviking.config.source.base import ConfigSource
from openviking.service.task_tracker_concurrency import KeyedAsyncLockPool, run_to_completion
from openviking_cli.utils.config.runtime_field import fallback_of, resolve_fallback
from openviking_cli.utils.logger import get_logger

logger = get_logger(__name__)

# C = cluster config type; A = per-account config type.
C = TypeVar("C")
A = TypeVar("A")
T = TypeVar("T")
_NO_OLD_SETTINGS = object()


@dataclass(frozen=True)
class AccountConfigView(Generic[C, A]):
    """Expose one atomically captured Account/Cluster publication pair.

    Used only by synchronous selectors, never as a request context. Returned
    models are shared, read-only configuration values. Business resolvers should
    combine them explicitly instead of adding generic framework fallback rules.
    """

    _account: A
    _cluster: C

    @property
    def account(self) -> A:
        """Return the captured Account configuration."""
        return self._account

    @property
    def cluster(self) -> C:
        """Return the captured cluster configuration."""
        return self._cluster

    def get(self, field: str) -> Any:
        """Return an Account field with legacy whole-section fallback.

        This compatibility API never merges nested values. New business
        resolvers should read ``account`` and ``cluster`` explicitly.
        """
        fields = getattr(type(self._account), "model_fields", {})
        if field not in fields:
            raise AttributeError(field)
        value = getattr(self._account, field)
        if value is not None:
            return value
        target = fallback_of(fields[field])
        return resolve_fallback(self._cluster, target) if target is not None else None


@dataclass(frozen=True)
class AccountCandidateContext(Generic[C, A]):
    """Read-only inputs for synchronous, side-effect-free domain validation.

    ``old_view`` is supplied only for an update PATCH, built from the latest
    document passed to the source's mutation callback (including absent settings
    on an existing Account). Both views share one captured Cluster publication.
    Creation, initial loading and refresh validate only the candidate; loading a
    persisted document is not a local update request. ``changed_sections`` names
    PATCH keys, or is ``None`` when all validators must run.
    """

    new_view: AccountConfigView[C, A]
    old_view: Optional[AccountConfigView[C, A]]
    changed_sections: Optional[frozenset[str]]
    creating: bool


@dataclass(frozen=True)
class AccountCandidateValidator(Generic[C, A]):
    """Validate every candidate; sections describe the domain's dependencies.

    Validators may use context.changed_sections to scope PATCH transition
    rules, but must always validate the complete candidate's effective values.
    """

    sections: frozenset[str]
    validate: Callable[[AccountCandidateContext[C, A]], None]


REFRESH_INTERVAL_SECS = 30.0
# Accounts unused for this long are evicted and stop being polled.
ACCOUNT_IDLE_TTL_SECS = 24 * 60 * 60.0


@dataclass(frozen=True)
class ConfigChangeEvent:
    """Emitted after a new config is published.

    ``scope`` names the changed persisted settings document (cluster-wide or
    one account).
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
    """One account's cached state protected by the publication lock.

    ``config`` is the constructed, already-validated account model;
    ``last_access`` drives idle eviction.
    """

    config: A
    last_access: float


@dataclass(frozen=True)
class _PreparedPatch:
    """Candidate and persisted document prepared for one scope mutation."""

    persisted_settings: dict
    candidate: Any


@dataclass(frozen=True)
class _PendingNotification:
    """One notification ordered after the previous event for its scope."""

    event: ConfigChangeEvent
    predecessor: Optional[ConcurrentFuture[None]]
    completion: ConcurrentFuture[None]


class RuntimeConfigManager(Generic[C, A]):
    """Coordinate scoped config patching/publication over a :class:`ConfigSource`.

    ``base_config`` is the caller-owned immutable startup baseline.
    ``get_config`` / ``set_config`` bridge to the process-wide cluster singleton;
    ``build_config`` rebuilds the cluster model from the baseline plus a complete
    Cluster settings document; ``build_account`` constructs an Account model from
    its own settings document. Supplying these as hooks keeps the manager free of
    concrete config-model imports and business-specific defaulting rules.
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
        account_candidate_validators: Iterable[AccountCandidateValidator[C, A]] = (),
    ) -> None:
        self._source = source
        # Immutable startup baseline. Cluster overrides are always rebuilt from
        # this object, never from the previously published effective config.
        self._base_config = base_config
        self._get_config = get_config
        self._set_config = set_config
        # build_config(old_cluster, cluster_override) -> new validated cluster config.
        self._build_config = build_config
        # build_account(account_settings) -> validated Account configuration.
        # Account/Cluster composition belongs to business resolvers. Existing
        # RuntimeField fallback remains a whole-section compatibility read only.
        self._build_account = build_account
        # validate_request(patch, is_account_scope, creating) -> None (structural gate).
        self._validate_request = validate_request
        # Domain validators run before persistence and never mutate publications.
        self._account_candidate_validators = tuple(account_candidate_validators)
        # Per-account cache; publication_lock protects cross-loop snapshots.
        self._accounts: dict[str, _AccountEntry[A]] = {}
        self._persisted_settings: dict[ConfigScope, Optional[dict]] = {}
        self._consumers: list[_ConsumerRegistration] = []
        # Per-scope local locks serialize same-node concurrent PATCHes so two
        # requests mutating the same scope can't interleave read-merge-write.
        self._scope_locks = KeyedAsyncLockPool[tuple]()
        self._publication_lock = threading.RLock()
        self._notification_tails: dict[ConfigScope, ConcurrentFuture[None]] = {}
        self._refresh_task_lock = threading.Lock()
        self._refresh_task: Optional[asyncio.Task] = None
        self._refresh_task_loop: Optional[asyncio.AbstractEventLoop] = None

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
        await self._refresh_scope(ConfigScope.cluster(), reason=ConfigChangeReason.UPDATE)

    async def replace_base_config(self, base_config: C) -> ConfigChangeEvent:
        """Replace the startup baseline without persisting runtime settings."""
        return await run_to_completion(lambda: self._replace_base_config(base_config))

    async def _replace_base_config(self, base_config: C) -> ConfigChangeEvent:
        scope = ConfigScope.cluster()
        async with self._lock_for(scope):
            with self._publication_lock:
                self._base_config = base_config
                persisted_settings = self._persisted_settings.get(scope)
                candidate = self._build_candidate(scope, persisted_settings)
                event = self._publish(
                    scope,
                    persisted_settings,
                    candidate,
                    reason=ConfigChangeReason.UPDATE,
                )
                notification = self._chain_notification(event)
        await self._notify_in_order(notification)
        return event

    # -- account loading -----------------------------------------------------

    async def _ensure_loaded(self, account_id: str) -> None:
        """Load and validate one Account configuration, once.

        Idempotent and concurrency-safe per Account. Absence of a file records
        "loaded, no settings"; a read/decrypt/validate failure raises and does
        not fall back to another Account's configuration.
        """
        with self._publication_lock:
            entry = self._accounts.get(account_id)
            if entry is not None:
                entry.last_access = time.monotonic()
                return
        scope = ConfigScope.account(account_id)
        async with self._lock_for(scope):
            with self._publication_lock:
                if account_id in self._accounts:
                    self._accounts[account_id].last_access = time.monotonic()
                    return
            persisted_settings = await self._source.load(scope)
            with self._publication_lock:
                self._accounts[account_id] = _AccountEntry(
                    config=self._build_account_candidate(persisted_settings),
                    last_access=time.monotonic(),
                )
                self._persisted_settings[scope] = persisted_settings

    async def get_account(self, account_id: str, field: str) -> Any:
        """Load an Account field, retaining deprecated whole-section fallback."""
        return await self.resolve_account(account_id, lambda view: view.get(field))

    async def resolve_account(
        self, account_id: str, resolver: Callable[[AccountConfigView[C, A]], T]
    ) -> T:
        """Select a domain value from one publication pair.

        Selectors must be synchronous and perform no I/O or configuration
        mutations. Loading and cancellation have the same contract as get_account.
        """
        return await run_to_completion(lambda: self._resolve_account(account_id, resolver))

    async def _resolve_account(
        self, account_id: str, resolver: Callable[[AccountConfigView[C, A]], T]
    ) -> T:
        while True:
            await self._ensure_loaded(account_id)
            with self._publication_lock:
                entry = self._accounts.get(account_id)
                if entry is not None:
                    view = AccountConfigView(entry.config, self._get_config())
                    break
        result = resolver(view)
        import inspect

        if inspect.isawaitable(result):
            if inspect.iscoroutine(result):
                result.close()
            raise TypeError("account configuration resolver must be synchronous")
        return result

    async def get_settings(self, scope: ConfigScope) -> dict:
        """Read the settings stored for exactly one scope."""
        import copy

        return copy.deepcopy(await self._source.load(scope) or {})

    def validate_initial_settings(self, account_id: str, settings: dict) -> None:
        """Validate creation-time settings without persisting them."""
        ConfigScope.account(account_id)
        if self._validate_request is not None:
            self._validate_request(settings, True, True)
        self._build_account_candidate(settings, creating=True)

    # -- PATCH ---------------------------------------------------------------

    async def patch_cluster(self, patch: dict) -> ConfigChangeEvent:
        """Apply a three-state PATCH to the Cluster settings and publish."""
        scope = ConfigScope.cluster()
        return await run_to_completion(lambda: self._patch(scope, patch))

    async def patch_account(
        self, account_id: str, patch: dict, *, creating: bool = False
    ) -> ConfigChangeEvent:
        """Apply a three-state PATCH to one Account configuration and publish."""
        scope = ConfigScope.account(account_id)
        return await run_to_completion(lambda: self._patch(scope, patch, creating=creating))

    async def delete_account(self, account_id: str) -> ConfigChangeEvent:
        """Delete one Account configuration and evict all manager-owned state."""
        scope = ConfigScope.account(account_id)
        return await run_to_completion(lambda: self._delete_account(scope))

    async def _delete_account(self, scope: ConfigScope) -> ConfigChangeEvent:
        assert scope.key is not None
        async with self._lock_for(scope):
            await self._source.delete(scope)
            with self._publication_lock:
                event = self._publish(
                    scope,
                    None,
                    None,
                    reason=ConfigChangeReason.EVICT,
                )
                notification = self._chain_notification(event)
        await self._notify_in_order(notification)
        return event

    async def _patch(
        self, scope: ConfigScope, patch: dict, *, creating: bool = False
    ) -> ConfigChangeEvent:
        # Phase 1: validate only the request boundary. Candidate validation
        # happens after the current document has been merged.
        self._validate_patch_request(scope, patch, creating)

        # Phase 2: serialize the scope's read/merge/validate/write operation.
        async with self._lock_for(scope):
            prepared = await self._persist_validated_patch(scope, patch, creating=creating)

            # Phase 3: publish only the already-built candidate. Account and
            # Cluster publications are independent; a Cluster pointer change
            # never rebuilds the AccountConfig candidate.
            with self._publication_lock:
                event = self._publish(
                    scope,
                    prepared.persisted_settings,
                    prepared.candidate,
                    reason=ConfigChangeReason.UPDATE,
                )
                notification = self._chain_notification(event)

        # Phase 4: consumers run outside write locks, in publication order.
        await self._notify_in_order(notification)
        return event

    def _validate_patch_request(
        self,
        scope: ConfigScope,
        patch: dict,
        creating: bool,
    ) -> None:
        """Validate the request boundary before entering scope serialization."""
        if self._validate_request is not None:
            self._validate_request(patch, scope.kind is ScopeKind.ACCOUNT, creating)

    async def _persist_validated_patch(
        self,
        scope: ConfigScope,
        patch: dict,
        *,
        creating: bool,
    ) -> _PreparedPatch:
        """Prepare one candidate and persist its explicit scope document."""
        prepared: Optional[_PreparedPatch] = None

        def mutate(current: Optional[dict]) -> dict:
            nonlocal prepared
            prepared = self._prepare_patch(scope, current, patch, creating=creating)
            return prepared.persisted_settings

        persisted_settings = await self._source.update(scope, mutate)
        if prepared is None:
            raise RuntimeError("config source update did not invoke its mutate callback")
        if prepared.persisted_settings is not persisted_settings:
            prepared = _PreparedPatch(
                persisted_settings=persisted_settings,
                candidate=prepared.candidate,
            )
        return prepared

    def _prepare_patch(
        self,
        scope: ConfigScope,
        current: Optional[dict],
        patch: dict,
        *,
        creating: bool,
    ) -> _PreparedPatch:
        """Merge, transition-check, construct and validate one PATCH candidate."""
        persisted_settings = apply_three_state_patch(current, patch)
        if self._validate_request is not None and not creating:
            self._validate_resets(
                current or {},
                patch,
                scope.kind is ScopeKind.ACCOUNT,
            )
        candidate = self._build_candidate(
            scope,
            persisted_settings,
            changed_sections=frozenset(patch),
            old_settings=current,
            creating=creating,
        )
        return _PreparedPatch(persisted_settings=persisted_settings, candidate=candidate)

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

    def _build_candidate(
        self,
        scope: ConfigScope,
        persisted_settings: Optional[dict],
        *,
        changed_sections: Optional[frozenset[str]] = None,
        old_settings: Any = _NO_OLD_SETTINGS,
        creating: bool = False,
    ) -> Any:
        """Build and validate the candidate publication for one scope."""
        if scope.kind is ScopeKind.CLUSTER:
            return self._build_config(self._base_config, persisted_settings or {})
        return self._build_account_candidate(
            persisted_settings,
            changed_sections=changed_sections,
            old_settings=old_settings,
            creating=creating,
        )

    def _build_account_candidate(
        self,
        settings: Optional[dict],
        *,
        changed_sections: Optional[frozenset[str]] = None,
        old_settings: Any = _NO_OLD_SETTINGS,
        creating: bool = False,
    ) -> A:
        """Build an Account model and run injected cross-publication checks."""
        account = self._build_account(settings)
        # The source may contain changes rejected by a previous refresh.
        # Validate the complete candidate; changed_sections only scopes
        # transition checks inside validators, never candidate validity.
        validators = self._account_candidate_validators
        if not validators:
            return account
        cluster = self._get_config()
        context = AccountCandidateContext(
            new_view=AccountConfigView(account, cluster),
            old_view=(
                AccountConfigView(self._build_account(old_settings), cluster)
                if not creating and old_settings is not _NO_OLD_SETTINGS
                else None
            ),
            changed_sections=changed_sections,
            creating=creating,
        )
        for validator in validators:
            validator.validate(context)
        return account

    def _publish(
        self,
        scope: ConfigScope,
        persisted_settings: Optional[dict],
        candidate: Any,
        *,
        reason: ConfigChangeReason,
    ) -> ConfigChangeEvent:
        """Swap an already-built candidate into publication state."""
        if scope.kind is ScopeKind.CLUSTER:
            if candidate is None:
                raise ValueError("cluster publication requires a candidate")
            old = self._get_config()
            self._set_config(candidate)
            self._persisted_settings[scope] = persisted_settings
            return ConfigChangeEvent(
                scope=scope,
                changed_sections=diff_sections(old, candidate),
                old_config=old,
                new_config=candidate,
                reason=reason,
            )
        assert scope.key is not None
        account_id = scope.key
        old_entry = self._accounts.get(account_id)
        old_config = old_entry.config if old_entry is not None else None
        if candidate is None:
            self._accounts.pop(account_id, None)
            self._persisted_settings.pop(scope, None)
        else:
            self._accounts[account_id] = _AccountEntry(
                config=candidate,
                last_access=(old_entry.last_access if old_entry else time.monotonic()),
            )
            self._persisted_settings[scope] = persisted_settings
        return self._account_event(account_id, old_config, candidate, reason=reason)

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

    def _chain_notification(
        self, event: ConfigChangeEvent
    ) -> Optional[_PendingNotification]:
        """Link an event to the preceding notification for the same scope."""
        is_evict = event.reason is ConfigChangeReason.EVICT
        if not event.changed_sections and not is_evict:
            return None
        completion: ConcurrentFuture[None] = ConcurrentFuture()
        pending = _PendingNotification(
            event=event,
            predecessor=self._notification_tails.get(event.scope),
            completion=completion,
        )
        self._notification_tails[event.scope] = completion
        return pending

    async def _notify_in_order(
        self, pending: Optional[_PendingNotification]
    ) -> None:
        """Deliver one event after its scope predecessor, without write locks."""
        if pending is None:
            return
        if pending.predecessor is not None:
            await asyncio.wrap_future(pending.predecessor)
        try:
            await self._notify(pending.event)
        finally:
            pending.completion.set_result(None)
            with self._publication_lock:
                if self._notification_tails.get(pending.event.scope) is pending.completion:
                    self._notification_tails.pop(pending.event.scope, None)

    async def _notify(self, event: ConfigChangeEvent) -> None:
        """Await matching consumers in registration order for one scope."""
        is_evict = event.reason is ConfigChangeReason.EVICT
        if not event.changed_sections and not is_evict:
            return
        for registration in self._consumers:
            if registration.scope is not event.scope.kind:
                continue
            if not is_evict and registration.sections.isdisjoint(event.changed_sections):
                continue
            try:
                await registration.consumer(event)
            except Exception:
                logger.exception("Config change consumer failed")

    def _lock_for(self, scope: ConfigScope):
        key = (scope.kind.value, scope.key)
        return self._scope_locks.acquire(key)

    # -- periodic refresh ----------------------------------------------------

    def start_refresh_loop(self, interval_secs: float = REFRESH_INTERVAL_SECS) -> None:
        loop = asyncio.get_running_loop()
        with self._refresh_task_lock:
            if self._refresh_task is None or self._refresh_task.done():
                self._refresh_task = loop.create_task(self._refresh_loop(interval_secs))
                self._refresh_task_loop = loop

    async def stop_refresh_loop(self) -> None:
        with self._refresh_task_lock:
            task = self._refresh_task
            owner_loop = self._refresh_task_loop
        if task is None or owner_loop is None:
            return

        async def cancel_and_wait() -> None:
            task.cancel()
            try:
                await task
            except asyncio.CancelledError:
                pass

        if asyncio.get_running_loop() is owner_loop:
            await cancel_and_wait()
        else:
            future = asyncio.run_coroutine_threadsafe(cancel_and_wait(), owner_loop)
            await asyncio.wrap_future(future)
        with self._refresh_task_lock:
            if self._refresh_task is task:
                self._refresh_task = None
                self._refresh_task_loop = None

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
        """Reload all active configuration scopes once."""
        await run_to_completion(self._refresh_once)

    async def _refresh_once(self) -> None:
        """Reload cluster + hot accounts once, publishing any observed change.

        Every active scope is reloaded and diffed against the last published
        settings document (``_refresh_scope``); the manager owns change detection, so the
        source stays stateless. Cold accounts (idle > TTL) are evicted and stop
        being reloaded. On load or publish failure the last effective value is
        kept, and the next refresh retries the active scope.
        """
        with self._publication_lock:
            account_ids = list(self._accounts)
        for account_id in account_ids:
            await self._evict_account_if_cold(account_id)
        with self._publication_lock:
            scopes = [ConfigScope.cluster()] + [
                ConfigScope.account(account_id) for account_id in self._accounts
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
            persisted_settings = await self._source.load(scope)
            with self._publication_lock:
                if (
                    scope in self._persisted_settings
                    and self._persisted_settings[scope] == persisted_settings
                ):
                    return
                candidate = self._build_candidate(
                    scope,
                    persisted_settings,
                    changed_sections=None,
                    old_settings=_NO_OLD_SETTINGS,
                    creating=False,
                )
                if scope.kind is ScopeKind.ACCOUNT:
                    assert scope.key is not None
                    if scope.key not in self._accounts:
                        # A refresh must not resurrect an evicted/never-loaded account.
                        return
                event = self._publish(scope, persisted_settings, candidate, reason=reason)
                notification = self._chain_notification(event)
        await self._notify_in_order(notification)

    async def _evict_account_if_cold(self, account_id: str) -> None:
        scope = ConfigScope.account(account_id)
        async with self._lock_for(scope):
            with self._publication_lock:
                entry = self._accounts.get(account_id)
                if (
                    entry is None
                    or time.monotonic() - entry.last_access <= ACCOUNT_IDLE_TTL_SECS
                ):
                    return
                self._accounts.pop(account_id)
                self._persisted_settings.pop(scope, None)
                event = self._account_event(
                    account_id,
                    entry.config,
                    None,
                    reason=ConfigChangeReason.EVICT,
                )
                notification = self._chain_notification(event)
        await self._notify_in_order(notification)
