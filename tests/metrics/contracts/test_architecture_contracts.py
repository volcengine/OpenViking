# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0

from pathlib import Path


def test_required_core_modules_exist():
    import openviking.metrics.core.base as core_base
    import openviking.metrics.core.refresh as core_refresh
    import openviking.metrics.core.registry as core_registry
    import openviking.metrics.core.runtime as core_runtime
    import openviking.metrics.core.types as core_types

    assert hasattr(core_base, "MetricDataSource")
    assert hasattr(core_base, "MetricCollector")
    assert hasattr(core_base, "MetricExporter")
    assert hasattr(core_refresh, "RefreshGate")
    assert hasattr(core_registry, "MetricRegistry")
    assert hasattr(core_runtime, "EventCollectorRouter")
    assert core_types is not None


def test_required_architecture_types_exist():
    import openviking.metrics.core.base as base_module

    assert hasattr(base_module, "MetricDataSource")
    assert hasattr(base_module, "MetricCollector")
    assert hasattr(base_module, "MetricExporter")


def test_required_intermediate_types_exist():
    import openviking.metrics.collectors.base as collector_base_module
    import openviking.metrics.datasources as ds_module
    import openviking.metrics.datasources.base as ds_base_module

    assert hasattr(ds_base_module, "EventMetricDataSource")
    assert hasattr(ds_base_module, "StateMetricDataSource")
    assert hasattr(ds_base_module, "DomainStatsMetricDataSource")
    assert hasattr(ds_base_module, "ProbeMetricDataSource")
    assert hasattr(ds_module, "CacheEventDataSource")
    assert hasattr(ds_module, "TelemetryBridgeEventDataSource")

    assert hasattr(collector_base_module, "EventMetricCollector")
    assert hasattr(collector_base_module, "StateMetricCollector")
    assert hasattr(collector_base_module, "DomainStatsMetricCollector")
    assert hasattr(collector_base_module, "ProbeMetricCollector")
    assert hasattr(collector_base_module, "Refreshable")
    assert not hasattr(collector_base_module, "AbstractMetricCollector")


def test_prometheus_exporter_inherits_base_metric_exporter():
    import openviking.metrics.exporters as exporters_module
    from openviking.metrics.core.base import MetricExporter
    from openviking.metrics.exporters.prometheus import PrometheusExporter

    assert issubclass(PrometheusExporter, MetricExporter)
    assert not hasattr(exporters_module, "AbstractMetricExporter")


def test_concrete_datasources_and_collectors_follow_doc_inheritance():
    from openviking.metrics.collectors.base import (
        ProbeMetricCollector,
        Refreshable,
        StateMetricCollector,
    )
    from openviking.metrics.collectors.lock import LockCollector
    from openviking.metrics.collectors.queue import QueueCollector
    from openviking.metrics.collectors.service_probe import ServiceProbeCollector
    from openviking.metrics.collectors.vikingdb import VikingDBCollector
    from openviking.metrics.datasources.base import (
        DomainStatsMetricDataSource,
        ProbeMetricDataSource,
        StateMetricDataSource,
    )
    from openviking.metrics.datasources.observer_state import (
        LockStateDataSource,
        ObserverStateDataSource,
        VikingDBStateDataSource,
    )
    from openviking.metrics.datasources.probes import ServiceProbeDataSource
    from openviking.metrics.datasources.queue import QueuePipelineStateDataSource

    assert issubclass(QueuePipelineStateDataSource, StateMetricDataSource)
    assert issubclass(ObserverStateDataSource, DomainStatsMetricDataSource)
    assert issubclass(LockStateDataSource, StateMetricDataSource)
    assert issubclass(VikingDBStateDataSource, StateMetricDataSource)
    assert issubclass(ServiceProbeDataSource, ProbeMetricDataSource)

    assert issubclass(QueueCollector, StateMetricCollector)
    assert issubclass(LockCollector, StateMetricCollector)
    assert issubclass(VikingDBCollector, StateMetricCollector)
    assert issubclass(ServiceProbeCollector, ProbeMetricCollector)
    assert issubclass(QueueCollector, Refreshable)
    assert issubclass(LockCollector, Refreshable)
    assert issubclass(VikingDBCollector, Refreshable)
    assert issubclass(ServiceProbeCollector, Refreshable)


def test_metrics_code_has_no_snapshot_api():
    root = Path(__file__).resolve().parents[2] / "openviking" / "metrics"
    for path in root.rglob("*.py"):
        text = path.read_text(encoding="utf-8")
        assert ".snapshot(" not in text, f"snapshot api still found in {path}"


def test_metric_collector_supports_collect_and_receive():
    from openviking.metrics.core.base import MetricCollector

    class _DummyCollector(MetricCollector):
        @classmethod
        def kind(cls) -> str:
            return "dummy"

    collector = _DummyCollector()
    assert collector.collector_name() == "_DummyCollector"
    assert collector.collect(None) is None
    assert collector.receive("event", {}, None) is None
