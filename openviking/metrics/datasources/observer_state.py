# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0

from __future__ import annotations

import time
from typing import Any

from openviking.metrics.core.base import ReadEnvelope
from openviking.server.identity import AccountNamespacePolicy, RequestContext, Role
from openviking.storage.transaction import get_lock_manager
from openviking_cli.session.user_id import UserIdentifier
from openviking_cli.utils import run_async

from .base import DomainStatsMetricDataSource, StateMetricDataSource


class ObserverStateDataSource(DomainStatsMetricDataSource):
    """
    Read observer-backed component objects from the in-memory debug service.

    The datasource does not compute health itself; it exposes the raw observer component objects
    so downstream collectors can derive health and error counts consistently.
    """

    def __init__(self, *, service: Any = None) -> None:
        """
        Store the optional service object used to reach the in-memory debug observer.

        Passing `service=None` keeps the datasource usable in tests and degraded environments
        where the debug service is intentionally absent.
        """
        self._service = service

    def read_component_states(self) -> ReadEnvelope[dict[str, Any]]:
        """
        Read the current observer component objects exposed by the debug service.

        Returns:
            A mapping of component names to observer-backed objects, or an empty mapping when
            the debug observer is not currently available.
        """

        def _read() -> dict[str, Any]:
            observer = None
            if self._service is not None:
                observer = getattr(getattr(self._service, "debug", None), "observer", None)
            if observer is None:
                return {}
            components = {
                self.normalize_str("queue"): observer.queue,
                self.normalize_str("models"): observer.models,
                self.normalize_str("lock"): observer.lock,
                self.normalize_str("retrieval"): observer.retrieval,
            }
            try:
                components[self.normalize_str("vikingdb")] = observer.vikingdb(ctx=None)
            except Exception:
                components[self.normalize_str("vikingdb")] = None
            return self.as_dict(components)

        return self.safe_read(_read, default={})


class LockStateDataSource(StateMetricDataSource):
    """
    Read lock-manager counters needed by lock-related state collectors.

    The datasource inspects active lock handles and derives a stale-handle count using the
    current in-process timeout heuristic.
    """

    def read_lock_state(self) -> ReadEnvelope[tuple[int, int, int]]:
        """
        Read active and stale lock counts from the global transaction lock manager.

        Returns:
            A tuple of `(active_locks, waiting_locks, stale_handles)` where waiting locks are
            currently not tracked separately and therefore remain `0`.
        """

        def _read() -> tuple[int, int, int]:
            lock_manager = get_lock_manager()
            handles = lock_manager.get_active_handles()
            active = 0
            stale = 0
            now = time.time()
            for handle in handles.values():
                try:
                    active += len(getattr(handle, "locks", []) or [])
                    last = getattr(handle, "last_active_at", None)
                    if last is not None and (now - float(last)) > 600:
                        stale += 1
                except Exception:
                    continue
            return active, 0, stale

        return self.safe_read(_read, default=(0, 0, 0))


class VikingDBStateDataSource(StateMetricDataSource):
    """
    Read health and size signals from the active VikingDB manager, when available.

    The datasource composes multiple best-effort reads into a single tuple so collectors can emit
    VikingDB health and vector-count gauges from one normalized snapshot.
    """

    def __init__(self, *, service: Any = None, app: Any = None) -> None:
        """Store optional handles used to resolve VikingDB manager and account inventory."""
        self._service = service
        self._app = app

    def _read_default_identity(self) -> tuple[str, str]:
        """Read configured default (account_id, user_id), falling back to `default` values."""
        try:
            from openviking_cli.utils.config import get_openviking_config

            config = get_openviking_config()
            account_id = config.default_account or "default"
            user_id = config.default_user or "default"
        except Exception:
            account_id = "default"
            user_id = "default"
        return account_id, user_id

    def _make_ctx(self, *, account_id: str, user_id: str) -> RequestContext:
        """Build a RequestContext scoped to one account/user pair."""
        user = UserIdentifier(account_id=account_id, user_id=user_id, agent_id="metrics")
        ctx = RequestContext(user=user, role=Role.USER, namespace_policy=AccountNamespacePolicy())
        return ctx

    def _iter_metric_identities(self) -> list[tuple[str, str]]:
        """
        Return account/user pairs for fan-out collection.

        The configured default identity is always included first. When API key manager is
        available (server api_key mode), each known account contributes one user identity
        (the first listed user) so metrics can emit one per-account series per scrape.
        """
        default_account, default_user = self._read_default_identity()
        identities: dict[str, str] = {str(default_account): str(default_user)}

        manager = getattr(getattr(self._app, "state", None), "api_key_manager", None)
        if manager is None:
            return list(identities.items())
        if not hasattr(manager, "get_accounts") or not hasattr(manager, "get_users"):
            return list(identities.items())

        try:
            accounts = manager.get_accounts() or []
        except Exception:
            return list(identities.items())

        for item in accounts:
            account_id = self.normalize_str(getattr(item, "get", lambda *_: None)("account_id"))
            if not account_id:
                continue
            user_id = default_user
            try:
                users = manager.get_users(account_id, limit=1, expose_key=False)
            except TypeError:
                users = manager.get_users(account_id, limit=1)
            except Exception:
                users = []
            if users:
                first_user = users[0]
                user_id = self.normalize_str(
                    getattr(first_user, "get", lambda *_: None)("user_id"), default=default_user
                )
            identities[account_id] = user_id
        return list(identities.items())

    def read_vikingdb_state(self) -> ReadEnvelope[list[tuple[str, str, bool, int]]]:
        """
        Read the collection name, health status, and approximate row count for VikingDB.

        Returns:
            A list of tuples `(account_id, collection_name, healthy, count)`. Missing services or
            failures fall back to safe default values so metrics collection remains best-effort.
        """
        vikingdb = None
        if self._service is not None:
            vikingdb = getattr(self._service, "_vikingdb_manager", None) or getattr(
                self._service, "vikingdb", None
            )
        if vikingdb is None:
            return ReadEnvelope(
                ok=False,
                value=[("default", "default", False, 0)],
                error_type="NotAvailable",
                error_message="vikingdb manager missing",
            )

        identities = self._iter_metric_identities()
        collection = self.normalize_str(
            getattr(vikingdb, "collection_name", "default"), default="default"
        )
        results: list[tuple[str, str, bool, int]] = []
        any_success = False
        first_error_type: str | None = None
        first_error_message: str | None = None

        health_env = self.safe_read_async(
            lambda: vikingdb.health_check(),
            default=False,
            runner=run_async,
        )
        base_health_ok = bool(health_env.value)

        for account_id, user_id in identities:
            ctx = self._make_ctx(account_id=account_id, user_id=user_id)
            count_env = self.safe_read_async(
                lambda _ctx=ctx: vikingdb.count(filter=None, ctx=_ctx),
                default=0,
                runner=run_async,
            )
            sample_ok = base_health_ok and count_env.ok
            count = self.as_int(count_env.value, default=0)
            results.append((account_id, collection, sample_ok, count))
            if health_env.ok and count_env.ok:
                any_success = True
            else:
                if first_error_type is None:
                    first_error_type = health_env.error_type or count_env.error_type
                if first_error_message is None:
                    first_error_message = health_env.error_message or count_env.error_message

        return ReadEnvelope(
            ok=any_success,
            value=results,
            error_type=first_error_type,
            error_message=first_error_message,
        )
