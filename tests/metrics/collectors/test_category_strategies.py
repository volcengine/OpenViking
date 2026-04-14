# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0

import pytest

from openviking.metrics.collectors.base import (
    CollectorConfig,
    DomainStatsMetricCollector,
    ProbeMetricCollector,
    StateMetricCollector,
)
from openviking.metrics.core.registry import MetricRegistry


class _FailingProbeCollector(ProbeMetricCollector):
    config = CollectorConfig()

    def __init__(self) -> None:
        self.stale_called = False

    def read_metric_input(self):
        raise RuntimeError("probe read failed")

    def collect_hook(self, registry, metric_input) -> None:
        raise AssertionError("collect_hook must not be called on read failure")

    def collect_stale_hook(self, registry, error: Exception) -> None:
        self.stale_called = True


class _FailingStateCollector(StateMetricCollector):
    config = CollectorConfig()

    def read_metric_input(self):
        raise RuntimeError("state read failed")

    def collect_hook(self, registry, metric_input) -> None:
        raise AssertionError("collect_hook must not be called on read failure")


class _StaleOnErrorStateCollector(StateMetricCollector):
    config = CollectorConfig()
    STALE_ON_ERROR = True

    def __init__(self) -> None:
        self.stale_called = False

    def read_metric_input(self):
        raise RuntimeError("state read failed")

    def collect_hook(self, registry, metric_input) -> None:
        raise AssertionError("collect_hook must not be called on read failure")

    def collect_stale_hook(self, registry, error: Exception) -> None:
        self.stale_called = True


class _DeltaDomainStatsCollector(DomainStatsMetricCollector):
    config = CollectorConfig()

    def read_metric_input(self):
        return None

    def collect_hook(self, registry, metric_input) -> None:
        self.inc_counter_from_cumulative(
            registry=registry,
            metric_name="openviking_test_cumulative_total",
            key=("k",),
            current_value=10,
        )
        self.inc_counter_from_cumulative(
            registry=registry,
            metric_name="openviking_test_cumulative_total",
            key=("k",),
            current_value=15,
        )
        self.inc_counter_from_cumulative(
            registry=registry,
            metric_name="openviking_test_cumulative_total",
            key=("k",),
            current_value=3,
        )


def test_probe_default_error_strategy_does_not_raise_and_calls_stale_hook():
    registry = MetricRegistry()
    collector = _FailingProbeCollector()

    collector.collect(registry)

    assert collector.stale_called is True


def test_state_default_error_strategy_raises():
    registry = MetricRegistry()
    collector = _FailingStateCollector()

    with pytest.raises(RuntimeError, match="state read failed"):
        collector.collect(registry)


def test_state_stale_on_error_strategy_calls_stale_hook_and_does_not_raise():
    registry = MetricRegistry()
    collector = _StaleOnErrorStateCollector()

    collector.collect(registry)

    assert collector.stale_called is True


def test_domain_stats_delta_helper_applies_non_negative_deltas_and_handles_resets():
    registry = MetricRegistry()
    collector = _DeltaDomainStatsCollector()

    collector.collect(registry)

    counters = dict(registry.iter_counters())
    series = dict(counters["openviking_test_cumulative_total"])
    assert series[()] == 10 + 5 + 3
