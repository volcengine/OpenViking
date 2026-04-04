# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0
"""Telemetry runtime entrypoints."""

from __future__ import annotations

from dataclasses import dataclass, field
from threading import Lock
from typing import Any, Dict, Tuple


class MemoryTelemetryMeter:
    """Lightweight in-process telemetry meter.

    This is intentionally simple for now; it provides the global telemetry hook
    points required by the design without forcing immediate broad adoption.
    """

    def __init__(self):
        self._counters: Dict[Tuple[str, Tuple[Tuple[str, Any], ...]], float] = {}
        self._gauges: Dict[Tuple[str, Tuple[Tuple[str, Any], ...]], Any] = {}
        self._histograms: Dict[Tuple[str, Tuple[Tuple[str, Any], ...]], list[float]] = {}
        self._lock = Lock()

    @staticmethod
    def _key(metric: str, attrs: Dict[str, Any] | None) -> Tuple[str, Tuple[Tuple[str, Any], ...]]:
        normalized = tuple(sorted((attrs or {}).items()))
        return metric, normalized

    def increment(self, metric: str, value: float = 1, attrs: Dict[str, Any] | None = None) -> None:
        key = self._key(metric, attrs)
        with self._lock:
            self._counters[key] = self._counters.get(key, 0) + value

    def record_histogram(
        self, metric: str, value: float, attrs: Dict[str, Any] | None = None
    ) -> None:
        key = self._key(metric, attrs)
        with self._lock:
            self._histograms.setdefault(key, []).append(value)

    def set_gauge(self, metric: str, value: Any, attrs: Dict[str, Any] | None = None) -> None:
        key = self._key(metric, attrs)
        with self._lock:
            self._gauges[key] = value

    def record_event(
        self, name: str, attrs: Dict[str, Any] | None = None, scope: str | None = None
    ) -> None:
        # Event capture is intentionally disabled for summary-only telemetry.
        _ = (name, attrs, scope)


@dataclass
class TelemetryRuntime:
    meter_instance: MemoryTelemetryMeter = field(default_factory=MemoryTelemetryMeter)

    def meter(self) -> MemoryTelemetryMeter:
        return self.meter_instance


_RUNTIME = TelemetryRuntime()


def get_telemetry_runtime() -> TelemetryRuntime:
    return _RUNTIME


def set_telemetry_runtime(runtime: TelemetryRuntime) -> None:
    global _RUNTIME
    _RUNTIME = runtime


__all__ = [
    "MemoryTelemetryMeter",
    "TelemetryRuntime",
    "get_telemetry_runtime",
    "set_telemetry_runtime",
]
