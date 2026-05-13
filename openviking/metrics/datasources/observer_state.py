# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0

from __future__ import annotations

import time
from typing import Any

from openviking.metrics.core.base import ReadEnvelope
from openviking.storage.transaction import get_lock_manager
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

    def __init__(self, *, service: Any = None) -> None:
        """Store the optional service object used to resolve the active VikingDB manager."""
        self._service = service

    def read_vikingdb_state(self) -> ReadEnvelope[tuple[str, bool, int]]:
        """
        Read the collection name, health status, and approximate row count for VikingDB.

        Returns:
            A tuple of `(collection_name, healthy, count)`. Missing services or failures fall
            back to safe default values so metrics collection remains best-effort.
        """
        vikingdb = None
        if self._service is not None:
            vikingdb = getattr(self._service, "_vikingdb_manager", None) or getattr(
                self._service, "vikingdb", None
            )
        if vikingdb is None:
            return ReadEnvelope(
                ok=False,
                value=("default", False, 0),
                error_type="NotAvailable",
                error_message="vikingdb manager missing",
            )

        collection = self.normalize_str(
            getattr(vikingdb, "collection_name", "default"), default="default"
        )
        ok_env = self.safe_read_async(
            lambda: vikingdb.health_check(),
            default=False,
            runner=run_async,
        )
        count_env = self.safe_read_async(
            lambda: vikingdb.count(filter=None, ctx=None),
            default=0,
            runner=run_async,
        )
        ok = bool(ok_env.value)
        count = self.as_int(count_env.value, default=0)
        envelope_ok = bool(ok_env.ok and count_env.ok)
        return ReadEnvelope(
            ok=envelope_ok,
            value=(collection, ok, count),
            error_type=ok_env.error_type or count_env.error_type,
            error_message=ok_env.error_message or count_env.error_message,
        )
