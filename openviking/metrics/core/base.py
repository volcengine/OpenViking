# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0

"""
Core abstract interfaces for the metrics subsystem.

These interfaces define the minimal contracts used across the metrics architecture:
- DataSource: produces metrics inputs (events or read APIs), without writing the registry.
- Collector: translates inputs into MetricRegistry writes.
- Exporter: triggers refresh (best-effort) and renders metrics to an exposition format.

The intent of this module is to keep the top-level contracts small and stable:
- `MetricDataSource` describes how metric inputs are produced.
- `MetricCollector` describes the two supported collection styles:
  pull via `collect(...)` and push via `receive(...)`.
- `MetricExporter` describes how metrics are rendered for an external system such as Prometheus.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import Any, Callable, Generic, TypeVar

T = TypeVar("T")


@dataclass(frozen=True, slots=True)
class ReadEnvelope(Generic[T]):
    """
    Wrap the result of a datasource read together with normalized failure metadata.

    Datasources use this envelope to return a best-effort value even when the underlying read
    raises, allowing collectors to export stale or default values while still surfacing the
    failure type and message for diagnostics.
    """

    ok: bool
    value: T
    error_type: str | None = None
    error_message: str | None = None


class MetricDataSource(ABC):
    """
    Base interface for metric DataSources.

    A DataSource is responsible for providing metrics inputs:
    - For event-style metrics, it emits events (handled by an event router).
    - For pull-style metrics, it exposes read methods used by DomainStats/State/Probe collectors.

    DataSources must not write MetricRegistry directly; collectors are the only writers.
    """

    @classmethod
    @abstractmethod
    def kind(cls) -> str:
        """Return the datasource category identifier (e.g. `event`, `state`, `probe`)."""

    def source_name(self) -> str:
        """
        Return a stable human-readable name for the data source.

        The default implementation uses the concrete class name, which is sufficient for
        diagnostics, logging, and lightweight introspection. Subclasses may override this
        when they need a more explicit or externally stable identifier.
        """
        return self.__class__.__name__

    def safe_read(self, fn: Callable[[], T], *, default: T) -> ReadEnvelope[T]:
        """
        Execute a synchronous read function and convert failures into a `ReadEnvelope`.

        The helper lets datasource implementations keep best-effort read semantics concise while
        returning a deterministic default value when the underlying subsystem raises.
        """
        try:
            return ReadEnvelope(ok=True, value=fn())
        except Exception as e:
            return ReadEnvelope(
                ok=False,
                value=default,
                error_type=type(e).__name__,
                error_message=str(e),
            )

    def safe_read_async(
        self,
        coro_fn: Callable[[], Any],
        *,
        default: T,
        runner: Callable[[Any], Any],
    ) -> ReadEnvelope[T]:
        """
        Execute an async-style read through a caller-provided runner and wrap the result safely.

        This keeps async read orchestration outside the datasource while preserving the same
        best-effort error envelope used by `safe_read`.
        """
        return self.safe_read(lambda: runner(coro_fn()), default=default)

    @staticmethod
    def as_int(value: Any, *, default: int = 0) -> int:
        """Convert an arbitrary value to `int`, falling back to a deterministic default on error."""
        try:
            return int(value)
        except Exception:
            return int(default)

    @staticmethod
    def as_dict(value: Any) -> dict:
        """Return the value when it is a dictionary, otherwise return an empty dictionary."""
        return value if isinstance(value, dict) else {}

    @staticmethod
    def normalize_str(value: Any, *, default: str = "unknown") -> str:
        """Normalize arbitrary input into a non-empty string with a caller-provided fallback."""
        text = str(value or "").strip()
        return text if text else str(default)


class MetricCollector(ABC):
    """
    Base interface for metric collectors.

    A collector is the only layer that is allowed to write into MetricRegistry.
    Collectors may be triggered on-demand (e.g., before `/metrics` export) and should:
    - be fast and resilient (best-effort, do not crash the exporter)
    - avoid high-cardinality labels

    A collector supports two input modes:
    - `collect(...)`: pull-style collection, where the collector actively reads state and
      writes samples into the registry.
    - `receive(...)`: push-style collection, where the collector receives an already
      materialized event and translates it into registry writes.

    Both methods intentionally default to no-op so observability remains a sidecar concern:
    a collector only implements the modes it actually needs.
    """

    METRICS_NAMESPACE = "openviking"

    @classmethod
    @abstractmethod
    def kind(cls) -> str:
        """Return the collector category identifier (e.g. `event`, `state`, `probe`)."""

    @classmethod
    def metric_name(cls, domain: str, metric: str, unit: str | None = None) -> str:
        """
        Build a fully-qualified metric name under the project namespace.

        Args:
            domain: Domain namespace for the metric (may include underscores).
            metric: Metric name within the domain (should not include the namespace).
            unit: Optional unit or suffix segment (e.g. `total`, `seconds`, `bytes`).

        Returns:
            Fully-qualified metric name string such as `openviking_http_requests_total`.
        """
        base = f"{cls.METRICS_NAMESPACE}_{str(domain)}_{str(metric)}"
        if unit:
            return f"{base}_{str(unit)}"
        return base

    def collector_name(self) -> str:
        """
        Return a stable collector name for diagnostics and orchestration.

        The default implementation returns the concrete class name, which keeps call sites
        simple and avoids forcing every collector to repeat the same naming boilerplate.
        """
        return self.__class__.__name__

    def collect(self, registry) -> None:
        """
        Perform pull-style collection and write samples into the registry.

        Args:
            registry: The in-process metric registry that receives the collector output.

        This method is intentionally a no-op by default. Event-driven collectors do not need
        to implement pull behavior, while refresh-managed collectors override this method with
        their actual state-reading logic.
        """
        return None

    def receive(self, event_name: str, payload: dict, registry) -> None:
        """
        Perform push-style collection by consuming a dispatched event.

        Args:
            event_name: Logical event type emitted by a DataSource or bridge layer.
            payload: Event payload already normalized into a dictionary.
            registry: The in-process metric registry that receives the translated samples.

        This method is intentionally a no-op by default. Pull-only collectors never need to
        handle events, while event-driven collectors override it to fan out by `event_name`.
        """
        return None


class MetricExporter(ABC):
    """
    Base interface for metric exporters.

    Exporters render the current registry state into an output format (e.g., Prometheus text).
    Exporters typically trigger a refresh of registered collectors before rendering.
    """

    @abstractmethod
    async def export(self) -> str:
        """
        Render the current metric state into the exporter-specific output format.

        Returns:
            A serialized metrics payload, such as Prometheus text exposition.

        Exporters may choose to refresh pull-based collectors before rendering, but callers
        should treat this method as best-effort and should not rely on observability to affect
        business logic or request correctness.
        """
        raise NotImplementedError
