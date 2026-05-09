# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0

"""
Shared collector-side type definitions.

This module separates two concerns:
- Collection semantics, which belong to `MetricCollector` in `metrics.base`
- Refresh-management semantics, which belong to `Refreshable` here

The split keeps the public collector API simple (`collect` / `receive`) while still allowing
the scrape manager to recognize which collectors support timeout, TTL, and SWR-style control.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import ClassVar

from openviking.metrics.account_dimension import (
    metric_supports_account_dimension,
    resolve_metric_account_label,
)
from openviking.metrics.core.base import MetricCollector, ReadEnvelope


class DataSourceReadError(RuntimeError):
    """
    Describe a normalized datasource failure surfaced through a collector read path.

    Collectors raise this wrapper when a `ReadEnvelope` reports failure so stale/fallback hooks
    can receive a regular exception object with preserved datasource error metadata.
    """

    def __init__(
        self,
        *,
        source_name: str,
        error_type: str | None = None,
        error_message: str | None = None,
    ) -> None:
        text = f"{source_name} read failed"
        if error_type:
            text = f"{text}: {error_type}"
        if error_message:
            text = f"{text}: {error_message}"
        super().__init__(text)


@dataclass(frozen=True, slots=True)
class CollectorConfig:
    """
    Runtime refresh-control configuration for refresh-managed collectors.

    Attributes:
        ttl_seconds: Optional refresh time-to-live. When set, `CollectorManager` may skip
            repeated collections within the TTL window and apply SWR-style background refresh
            after expiry when previous data exists.
        timeout_seconds: Per-collection time budget enforced by `CollectorManager` when the
            collector is executed as part of the scrape pipeline.
    """

    ttl_seconds: float | None = None
    timeout_seconds: float = 0.5


class Refreshable(ABC):
    """
    Marker interface for collectors that participate in refresh-controlled scrape execution.

    A `Refreshable` collector is still a normal `MetricCollector`, but it additionally exposes
    a `config` attribute so `CollectorManager` can apply timeout, TTL, and SWR policies.
    Event-only collectors intentionally do not implement this interface.
    """

    config: CollectorConfig


class CollectorMetricWriter:
    """
    Collector-scoped registry writer that injects the resolved `account_id` label.

    The writer centralizes account-dimension support checks and final label resolution so
    concrete collectors do not need to duplicate that policy logic.
    """

    def __init__(self, registry, *, owner_account_id: str | None = None) -> None:
        """
        Bind the writer to one registry instance and an optional owner-account fallback value.

        `owner_account_id` is mainly used by background or task-based collectors that cannot rely
        on an active HTTP request context.
        """
        self._registry = registry
        self._owner_account_id = owner_account_id

    def __getattr__(self, name: str):
        """Forward unsupported attributes to the underlying registry instance transparently."""
        return getattr(self._registry, name)

    def inc_counter(
        self,
        name: str,
        *,
        amount: float = 1.0,
        labels: dict[str, str] | None = None,
        label_names: tuple[str, ...] = (),
        account_id: str | None = None,
    ) -> None:
        """Increment a counter after normalizing any account-aware labels for the target metric."""
        final_labels, final_label_names = self._with_account_labels(
            metric_name=name,
            labels=labels,
            label_names=label_names,
            explicit_account_id=account_id,
        )
        self._registry.inc_counter(
            name,
            amount=amount,
            labels=final_labels,
            label_names=final_label_names,
        )

    def set_gauge(
        self,
        name: str,
        value: float,
        *,
        labels: dict[str, str] | None = None,
        label_names: tuple[str, ...] = (),
        account_id: str | None = None,
    ) -> None:
        """Set a gauge after normalizing any account-aware labels for the target metric."""
        final_labels, final_label_names = self._with_account_labels(
            metric_name=name,
            labels=labels,
            label_names=label_names,
            explicit_account_id=account_id,
        )
        self._registry.set_gauge(
            name,
            value,
            labels=final_labels,
            label_names=final_label_names,
        )

    def observe_histogram(
        self,
        name: str,
        value: float,
        *,
        labels: dict[str, str] | None = None,
        label_names: tuple[str, ...] = (),
        buckets=(),
        account_id: str | None = None,
    ) -> None:
        """Observe a histogram sample after normalizing account-aware labels for the metric."""
        final_labels, final_label_names = self._with_account_labels(
            metric_name=name,
            labels=labels,
            label_names=label_names,
            explicit_account_id=account_id,
        )
        kwargs = {
            "labels": final_labels,
            "label_names": final_label_names,
        }
        if buckets:
            kwargs["buckets"] = buckets
        self._registry.observe_histogram(name, value, **kwargs)

    def gauge_delete_matching(
        self,
        name: str,
        *,
        match_labels: dict[str, str],
        account_id: str | None = None,
    ) -> None:
        """Delete matching gauge series while preserving the current account partition."""
        if not metric_supports_account_dimension(name):
            self._registry.gauge_delete_matching(name, match_labels=dict(match_labels))
            return
        final_account_id = resolve_metric_account_label(
            metric_name=name,
            explicit_account_id=account_id,
            owner_account_id=self._owner_account_id,
        )
        labels = dict(match_labels)
        labels["account_id"] = final_account_id
        self._registry.gauge_delete_matching(name, match_labels=labels)

    def _with_account_labels(
        self,
        *,
        metric_name: str,
        labels: dict[str, str] | None,
        label_names: tuple[str, ...],
        explicit_account_id: str | None,
    ) -> tuple[dict[str, str], tuple[str, ...]]:
        """
        Return labels and label names augmented with the resolved account dimension when allowed.

        Unsupported metrics bypass `account_id` injection entirely so their exported label shape
        stays identical to the original family contract.
        """
        final_labels = dict(labels or {})
        if not metric_supports_account_dimension(metric_name):
            final_label_names = tuple(sorted(set(label_names) | set(final_labels.keys())))
            return final_labels, final_label_names
        if "account_id" in final_labels:
            explicit_account_id = final_labels.pop("account_id")
        final_labels["account_id"] = resolve_metric_account_label(
            metric_name=metric_name,
            explicit_account_id=explicit_account_id,
            owner_account_id=self._owner_account_id,
        )
        final_label_names = tuple(sorted(set(label_names) | set(final_labels.keys())))
        return final_labels, final_label_names


class EventMetricCollector(MetricCollector, ABC):
    """
    Base category for event-driven collectors.

    These collectors primarily implement `receive(...)` and translate dispatched events into
    registry writes. They do not need refresh-control metadata because they are not scheduled
    by `CollectorManager`.
    """

    KIND: ClassVar[str] = "event"
    SUPPORTED_EVENTS: ClassVar[frozenset[str]] = frozenset()

    @classmethod
    def kind(cls) -> str:
        """Return the collector category identifier."""
        return cls.KIND

    def receive(self, event_name: str, payload: dict, registry) -> None:
        """
        Normalize and gate one incoming event before delegating to the subclass hook.

        The shared event-collector entrypoint performs the logic that all event collectors
        should agree on:
        - normalize the event name into its string form
        - ignore events this collector does not claim to support
        - ignore malformed payloads that are not dictionaries
        - delegate supported events to the concrete collector hook
        """
        normalized_event = str(event_name)
        if normalized_event not in self.SUPPORTED_EVENTS:
            return
        if not isinstance(payload, dict):
            return
        self.receive_hook(
            normalized_event,
            payload,
            CollectorMetricWriter(
                registry,
                owner_account_id=str(payload.get("owner_account_id") or "") or None,
            ),
        )

    @abstractmethod
    def receive_hook(self, event_name: str, payload: dict, registry) -> None:
        """Handle one supported event payload after the shared receive-path checks succeed."""


class StateMetricCollector(MetricCollector, Refreshable, ABC):
    """
    Base category for pull-based state collectors.

    State collectors read current in-process or subsystem state during `collect(...)` and are
    typically executed in the scrape pipeline. Because they participate in that pipeline, they
    also implement `Refreshable` and therefore expose `CollectorConfig`.
    """

    KIND: ClassVar[str] = "state"
    STALE_ON_ERROR: ClassVar[bool] = False

    @classmethod
    def kind(cls) -> str:
        """Return the collector category identifier."""
        return cls.KIND

    def collect(self, registry) -> None:
        """
        Read state input and delegate metric emission through subclass hooks.

        Concrete state collectors define how state is fetched and how the fetched input is
        translated into registry writes. Read failures are delegated to `collect_error_hook(...)`
        so collectors can either propagate or publish stale/failure metrics.
        """
        writer = CollectorMetricWriter(registry)
        try:
            metric_input = self.read_metric_input()
        except Exception as error:
            self.collect_error_hook(writer, error)
            return
        if isinstance(metric_input, ReadEnvelope):
            if not metric_input.ok:
                self.collect_error_hook(
                    writer,
                    DataSourceReadError(
                        source_name=self.collector_name(),
                        error_type=metric_input.error_type,
                        error_message=metric_input.error_message,
                    ),
                )
                return
            metric_input = metric_input.value
        self.collect_hook(writer, metric_input)

    @abstractmethod
    def read_metric_input(self):
        """Read the current state snapshot or intermediate input needed for metric emission."""

    @abstractmethod
    def collect_hook(self, registry, metric_input) -> None:
        """Translate the successfully-read state input into registry writes."""

    def collect_error_hook(self, registry, error: Exception) -> None:
        """Handle state-input read failures; the default behavior is to re-raise the error."""
        if self.STALE_ON_ERROR:
            self.collect_stale_hook(registry, error)
            return
        raise error

    def collect_stale_hook(self, registry, error: Exception) -> None:
        """Handle stale-on-error fallback; subclasses may override when stale export is allowed."""
        raise error

    def replace_gauge_series(
        self,
        registry,
        metric_name: str,
        value: float,
        *,
        match_labels: dict,
        labels: dict | None = None,
        label_names: tuple[str, ...] = (),
    ) -> None:
        """Replace the full gauge series selected by `match_labels` with one fresh value."""
        registry.gauge_delete_matching(str(metric_name), match_labels=match_labels)
        if labels is None:
            registry.set_gauge(str(metric_name), float(value))
            return
        registry.set_gauge(
            str(metric_name),
            float(value),
            labels=labels,
            label_names=label_names,
        )


class DomainStatsMetricCollector(MetricCollector, Refreshable, ABC):
    """
    Base category for pull-based domain-statistics collectors.

    These collectors compute higher-level aggregate metrics from existing subsystem state,
    often including validity semantics such as stale-or-invalid output on failure. They are
    refresh-managed because their collection cost and failure behavior may need TTL/SWR control.
    """

    KIND: ClassVar[str] = "domain_stats"

    @classmethod
    def kind(cls) -> str:
        """Return the collector category identifier."""
        return cls.KIND

    def collect(self, registry) -> None:
        """
        Read domain-statistics input and delegate metric emission through subclass hooks.

        Concrete domain collectors define how aggregate input is read and how it becomes
        registry output. Read failures are delegated to `collect_error_hook(...)` so collectors
        can emit stale/invalid gauges when that matches their contract.
        """
        writer = CollectorMetricWriter(registry)
        try:
            metric_input = self.read_metric_input()
        except Exception as error:
            self.collect_error_hook(writer, error)
            return
        if isinstance(metric_input, ReadEnvelope):
            if not metric_input.ok:
                self.collect_error_hook(
                    writer,
                    DataSourceReadError(
                        source_name=self.collector_name(),
                        error_type=metric_input.error_type,
                        error_message=metric_input.error_message,
                    ),
                )
                return
            metric_input = metric_input.value
        self.collect_hook(writer, metric_input)

    @abstractmethod
    def read_metric_input(self):
        """Read the aggregate domain-statistics input required for one collection cycle."""

    @abstractmethod
    def collect_hook(self, registry, metric_input) -> None:
        """Translate the successfully-read domain-statistics input into registry writes."""

    def collect_error_hook(self, registry, error: Exception) -> None:
        """Handle domain-statistics read failures; the default behavior is to re-raise."""
        raise error

    def replace_global_gauges(
        self,
        registry,
        *,
        metric_names: list[str],
        values: list[float],
        labels: dict,
        label_names: tuple[str, ...],
    ) -> None:
        """Replace all existing global gauge series for the provided metric names in one pass."""
        for metric_name, value in zip(metric_names, values, strict=True):
            registry.gauge_delete_matching(str(metric_name), match_labels={})
            registry.set_gauge(
                str(metric_name),
                float(value),
                labels=labels,
                label_names=label_names,
            )

    def inc_counter_from_cumulative(
        self,
        *,
        registry,
        metric_name: str,
        key: tuple,
        current_value: int,
        labels: dict | None = None,
        label_names: tuple[str, ...] = (),
    ) -> None:
        """Convert cumulative counters into monotonic Prometheus increments for one key."""
        last_seen = getattr(self, "_cumulative_last_seen", None)
        if last_seen is None:
            last_seen = {}
            # Lazily create per-collector state; stored on the instance to avoid shared globals.
            self._cumulative_last_seen = last_seen

        prev = last_seen.get(key)
        current = int(current_value)
        if prev is None:
            delta = current
        elif current >= int(prev):
            delta = current - int(prev)
        else:
            delta = current
        last_seen[key] = current
        if delta > 0:
            registry.inc_counter(
                str(metric_name),
                amount=float(delta),
                labels=labels,
                label_names=label_names,
            )


class ProbeMetricCollector(MetricCollector, Refreshable, ABC):
    """
    Base category for readiness/health probe collectors.

    Probe collectors convert subsystem readiness checks into metrics. They inherit
    `Refreshable` because probes may touch slower or less reliable dependencies and therefore
    benefit from centralized timeout and TTL handling.
    """

    KIND: ClassVar[str] = "probe"

    @classmethod
    def kind(cls) -> str:
        """Return the collector category identifier."""
        return cls.KIND

    def collect(self, registry) -> None:
        """
        Read probe input and delegate metric emission through subclass hooks.

        Probe collectors frequently need failure/staleness fallbacks, so the shared collect path
        centralizes read/dispatch flow while allowing concrete collectors to override
        `collect_error_hook(...)` for probe-specific validity behavior.
        """
        writer = CollectorMetricWriter(registry)
        try:
            metric_input = self.read_metric_input()
        except Exception as error:
            self.collect_error_hook(writer, error)
            return
        if isinstance(metric_input, ReadEnvelope):
            if not metric_input.ok:
                self.collect_error_hook(
                    writer,
                    DataSourceReadError(
                        source_name=self.collector_name(),
                        error_type=metric_input.error_type,
                        error_message=metric_input.error_message,
                    ),
                )
                return
            metric_input = metric_input.value
        self.collect_hook(writer, metric_input)

    @abstractmethod
    def read_metric_input(self):
        """Read the readiness/health probe input required for one collection cycle."""

    @abstractmethod
    def collect_hook(self, registry, metric_input) -> None:
        """Translate the successfully-read probe input into registry writes."""

    def collect_error_hook(self, registry, error: Exception) -> None:
        self.collect_stale_hook(registry, error)

    def collect_stale_hook(self, registry, error: Exception) -> None:
        """Ignore probe read failures by default after `collect_error_hook` delegates here."""
        return None

    def replace_gauge(
        self,
        registry,
        metric_name: str,
        value: float,
        *,
        match_labels: dict,
        labels: dict | None = None,
        label_names: tuple[str, ...] = (),
    ) -> None:
        """Replace the selected gauge series with one fresh readiness-style value."""
        registry.gauge_delete_matching(str(metric_name), match_labels=match_labels)
        if labels is None:
            registry.set_gauge(str(metric_name), float(value))
            return
        registry.set_gauge(
            str(metric_name),
            float(value),
            labels=labels,
            label_names=label_names,
        )

    def set_readiness_flags(
        self,
        registry,
        *,
        metrics: dict[str, str],
        state: dict,
    ) -> None:
        """Set a fixed mapping of readiness metrics from boolean values in the probe state."""
        for metric_name, key in metrics.items():
            registry.set_gauge(
                str(metric_name),
                1.0 if bool(state.get(key, False)) else 0.0,
            )

    def set_readiness_flags_on_error(
        self,
        registry,
        *,
        metric_names: list[str],
    ) -> None:
        """Drive the provided readiness metrics to zero when the probe itself fails."""
        for metric_name in metric_names:
            registry.set_gauge(str(metric_name), 0.0)

    def set_labeled_readiness(
        self,
        registry,
        metric_name: str,
        *,
        items: dict[str, bool],
        label_key: str,
        label_names: tuple[str, ...],
        valid_label: str | None = None,
    ) -> None:
        """Emit one readiness gauge series per named item, optionally attaching a validity flag."""
        for name, ok in items.items():
            labels = {label_key: str(name)}
            if valid_label is not None:
                labels["valid"] = str(valid_label)
            registry.set_gauge(
                str(metric_name),
                1.0 if ok else 0.0,
                labels=labels,
                label_names=label_names,
            )

    def delete_gauge_series_for_labels(
        self,
        registry,
        metric_name: str,
        *,
        label_key: str,
        values: list[str],
    ) -> None:
        """Delete all gauge series whose label values are listed in `values`."""
        for value in values:
            registry.gauge_delete_matching(str(metric_name), match_labels={label_key: str(value)})
