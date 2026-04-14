# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0

from __future__ import annotations

from typing import Any, Callable, ClassVar

from openviking.metrics.core.base import MetricDataSource, ReadEnvelope


class EventMetricDataSource(MetricDataSource):
    """
    Shared base for datasources that emit push-style in-process metrics events.

    Event datasources publish normalized payloads into the metrics event router instead of
    writing to the registry directly, keeping business code decoupled from collector internals.
    """

    KIND: ClassVar[str] = "event"

    @classmethod
    def kind(cls) -> str:
        """Return the datasource category identifier used by diagnostics and tests."""
        return cls.KIND

    @staticmethod
    def _emit(event_name: str, payload: dict) -> None:
        """
        Emit one normalized metrics event through the global best-effort event router.

        Missing metrics initialization is handled by the router path so callers can fire events
        without adding explicit metrics-enabled guards around business logic.
        """
        from openviking.metrics.global_api import try_dispatch_event

        try_dispatch_event(str(event_name), dict(payload))


class StateMetricDataSource(MetricDataSource):
    """
    Base category for datasources that expose pull-style point-in-time subsystem state.

    These datasources usually snapshot in-process managers or trackers and return read envelopes
    that let collectors export stale or default values when reads fail.
    """

    KIND: ClassVar[str] = "state"

    @classmethod
    def kind(cls) -> str:
        """Return the datasource category identifier."""
        return cls.KIND


class DomainStatsMetricDataSource(MetricDataSource):
    """
    Base category for datasources that expose aggregated domain-level statistics.

    Domain-stat datasources differ from raw state reads in that they return higher-level summary
    structures already grouped by business concepts such as component, model, or queue family.
    """

    KIND: ClassVar[str] = "domain_stats"

    @classmethod
    def kind(cls) -> str:
        """Return the datasource category identifier."""
        return cls.KIND


class ProbeMetricDataSource(MetricDataSource):
    """
    Base category for datasources that expose readiness or health probe state.

    Probe helpers wrap subsystem-specific checks into normalized dictionaries so collector code
    can emit consistent readiness metrics without repeating error handling.
    """

    KIND: ClassVar[str] = "probe"

    @classmethod
    def kind(cls) -> str:
        """Return the datasource category identifier."""
        return cls.KIND

    def safe_bool_probe(
        self,
        name: str,
        fn: Callable[[], Any],
        *,
        default: bool = False,
    ) -> ReadEnvelope[dict[str, bool]]:
        """Read one boolean probe and wrap the result under the provided probe name."""
        probe_name = str(name)
        return self.safe_read(lambda: {probe_name: bool(fn())}, default={probe_name: bool(default)})

    def safe_tuple_probe(
        self,
        key: str,
        fn: Callable[[], tuple[str, bool]],
        *,
        default_name: str = "unknown",
    ) -> ReadEnvelope[dict[str, tuple[str, bool]]]:
        """Read one named probe result and normalize it into a keyed `(name, ok)` tuple map."""
        k = str(key)

        def _read() -> dict[str, tuple[str, bool]]:
            name, ok = fn()
            return {k: (str(name), bool(ok))}

        return self.safe_read(
            _read,
            default={k: (str(default_name), False)},
        )

    def safe_value_probe(
        self,
        fn: Callable[[], Any],
        *,
        default: Any,
    ) -> ReadEnvelope[Any]:
        """Read one probe value using the generic `safe_read` fallback behavior."""
        return self.safe_read(fn, default=default)
