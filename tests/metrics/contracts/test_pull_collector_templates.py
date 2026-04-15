# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0

from openviking.metrics.collectors.base import (
    CollectorConfig,
    DomainStatsMetricCollector,
    ProbeMetricCollector,
    StateMetricCollector,
)
from openviking.metrics.core.registry import MetricRegistry


class _DummyStateCollector(StateMetricCollector):
    config = CollectorConfig()

    def __init__(self) -> None:
        self.seen = []

    def read_metric_input(self):
        return {"value": 3}

    def collect_hook(self, registry, metric_input) -> None:
        self.seen.append(("state", metric_input["value"]))


class _DummyProbeCollector(ProbeMetricCollector):
    config = CollectorConfig()

    def __init__(self) -> None:
        self.seen = []

    def read_metric_input(self):
        return {"ok": True}

    def collect_hook(self, registry, metric_input) -> None:
        self.seen.append(("probe", metric_input["ok"]))


class _DummyDomainCollector(DomainStatsMetricCollector):
    config = CollectorConfig()

    def __init__(self) -> None:
        self.seen = []

    def read_metric_input(self):
        return {"total": 7}

    def collect_hook(self, registry, metric_input) -> None:
        self.seen.append(("domain", metric_input["total"]))


class _DummyFailingStateCollector(StateMetricCollector):
    config = CollectorConfig()

    def __init__(self) -> None:
        self.error_seen = None

    def read_metric_input(self):
        raise RuntimeError("boom")

    def collect_hook(self, registry, metric_input) -> None:
        raise AssertionError("should not be called")

    def collect_error_hook(self, registry, error: Exception) -> None:
        self.error_seen = str(error)


def test_state_metric_collector_collect_uses_template_hooks():
    registry = MetricRegistry()
    collector = _DummyStateCollector()

    collector.collect(registry)

    assert collector.seen == [("state", 3)]


def test_probe_metric_collector_collect_uses_template_hooks():
    registry = MetricRegistry()
    collector = _DummyProbeCollector()

    collector.collect(registry)

    assert collector.seen == [("probe", True)]


def test_domain_stats_metric_collector_collect_uses_template_hooks():
    registry = MetricRegistry()
    collector = _DummyDomainCollector()

    collector.collect(registry)

    assert collector.seen == [("domain", 7)]


def test_state_metric_collector_collect_error_hook_can_handle_read_failures():
    registry = MetricRegistry()
    collector = _DummyFailingStateCollector()

    collector.collect(registry)

    assert collector.error_seen == "boom"
