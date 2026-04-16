# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0

from openviking.metrics.collectors.base import (
    CollectorConfig,
    ProbeMetricCollector,
    StateMetricCollector,
)
from openviking.metrics.core.base import ReadEnvelope
from openviking.metrics.core.registry import MetricRegistry


class _EnvelopeStateCollector(StateMetricCollector):
    config = CollectorConfig()
    STALE_ON_ERROR = True

    def __init__(self) -> None:
        self.error_seen = False
        self.value_seen = None

    def read_metric_input(self):
        return ReadEnvelope(ok=False, value=("fallback",))

    def collect_hook(self, registry, metric_input) -> None:
        self.value_seen = metric_input

    def collect_stale_hook(self, registry, error: Exception) -> None:
        self.error_seen = True


class _EnvelopeProbeCollector(ProbeMetricCollector):
    config = CollectorConfig()

    def __init__(self) -> None:
        self.error_seen = False
        self.value_seen = None

    def read_metric_input(self):
        return ReadEnvelope(ok=False, value={"probe": True})

    def collect_hook(self, registry, metric_input) -> None:
        self.value_seen = metric_input

    def collect_stale_hook(self, registry, error: Exception) -> None:
        self.error_seen = True


def test_state_collector_unwraps_envelope_and_routes_ok_false_to_stale_hook():
    registry = MetricRegistry()
    collector = _EnvelopeStateCollector()

    collector.collect(registry)

    assert collector.error_seen is True
    assert collector.value_seen is None


def test_probe_collector_unwraps_envelope_and_routes_ok_false_to_stale_hook():
    registry = MetricRegistry()
    collector = _EnvelopeProbeCollector()

    collector.collect(registry)

    assert collector.error_seen is True
    assert collector.value_seen is None
