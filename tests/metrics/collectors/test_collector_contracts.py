# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

import pytest

from openviking.metrics.collectors.async_system_probe import AsyncSystemProbeCollector
from openviking.metrics.collectors.base import (
    CollectorConfig,
    DomainStatsMetricCollector,
    ProbeMetricCollector,
    StateMetricCollector,
)
from openviking.metrics.collectors.encryption_probe import EncryptionProbeCollector
from openviking.metrics.collectors.model_provider_probe import ModelProviderProbeCollector
from openviking.metrics.collectors.retrieval_backend_probe import RetrievalBackendProbeCollector
from openviking.metrics.collectors.service_probe import ServiceProbeCollector
from openviking.metrics.collectors.storage_probe import StorageProbeCollector
from openviking.metrics.core.base import ReadEnvelope
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


@dataclass
class _ProbeDataSource:
    value: object | None = None
    ok: bool = True
    raises: bool = False

    def read_probe_state(self):
        if self.raises:
            raise RuntimeError("probe read failed")
        return ReadEnvelope(ok=self.ok, value=self.value, error_type="probe", error_message="fail")


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
    assert series[()] == 18


def test_service_probe_collector_emits_valid_on_success_and_invalid_on_failure(
    registry, render_prometheus
):
    ds = _ProbeDataSource(
        value={"service_readiness": True, "api_key_manager_readiness": False}, ok=True
    )
    c = ServiceProbeCollector(data_source=ds)
    c.collect(registry)
    text = render_prometheus(registry)
    assert 'openviking_service_readiness{valid="1"} 1.0' in text
    assert 'openviking_api_key_manager_readiness{valid="1"} 0.0' in text

    ds.ok = False
    c.collect(registry)
    text2 = render_prometheus(registry)
    assert 'openviking_service_readiness{valid="0"} 0.0' in text2
    assert 'openviking_api_key_manager_readiness{valid="0"} 0.0' in text2


def test_storage_probe_collector_preserves_last_probe_set_and_marks_invalid(
    registry, render_prometheus
):
    ds = _ProbeDataSource(value={"agfs": True, "other": False}, ok=True)
    c = StorageProbeCollector(data_source=ds)
    c.collect(registry)
    text = render_prometheus(registry)
    assert 'openviking_storage_readiness{probe="agfs",valid="1"} 1.0' in text
    assert 'openviking_storage_readiness{probe="other",valid="1"} 0.0' in text

    ds.raises = True
    c.collect(registry)
    text2 = render_prometheus(registry)
    assert 'openviking_storage_readiness{probe="agfs",valid="0"} 0.0' in text2
    assert 'openviking_storage_readiness{probe="other",valid="0"} 0.0' in text2


def test_retrieval_backend_probe_collector_preserves_last_probe_set_and_marks_invalid(
    registry, render_prometheus
):
    ds = _ProbeDataSource(value={"vikingdb": True}, ok=True)
    c = RetrievalBackendProbeCollector(data_source=ds)
    c.collect(registry)
    text = render_prometheus(registry)
    assert 'openviking_retrieval_backend_readiness{probe="vikingdb",valid="1"} 1.0' in text

    ds.ok = False
    c.collect(registry)
    text2 = render_prometheus(registry)
    assert 'openviking_retrieval_backend_readiness{probe="vikingdb",valid="0"} 0.0' in text2


def test_encryption_probe_collector_uses_last_provider_on_failure(registry, render_prometheus):
    ds = _ProbeDataSource(value=(True, "volcengine"), ok=True)
    c = EncryptionProbeCollector(data_source=ds)
    c.collect(registry)
    text = render_prometheus(registry)
    assert 'openviking_encryption_component_health{valid="1"} 1.0' in text
    assert 'openviking_encryption_root_key_ready{valid="1"} 1.0' in text
    assert 'openviking_encryption_kms_provider_ready{provider="volcengine",valid="1"} 1.0' in text

    ds.raises = True
    c.collect(registry)
    text2 = render_prometheus(registry)
    assert 'openviking_encryption_component_health{valid="0"} 0.0' in text2
    assert 'openviking_encryption_root_key_ready{valid="0"} 0.0' in text2
    assert 'openviking_encryption_kms_provider_ready{provider="volcengine",valid="0"} 0.0' in text2


def test_model_provider_probe_collector_uses_last_provider_on_failure(registry, render_prometheus):
    ds = _ProbeDataSource(value={"provider": ("volcengine", True)}, ok=True)
    c = ModelProviderProbeCollector(data_source=ds)
    c.collect(registry)
    text = render_prometheus(registry)
    assert 'openviking_model_provider_readiness{provider="volcengine",valid="1"} 1.0' in text

    ds.ok = False
    c.collect(registry)
    text2 = render_prometheus(registry)
    assert 'openviking_model_provider_readiness{provider="volcengine",valid="0"} 0.0' in text2


def test_async_system_probe_collector_marks_invalid_on_failure(registry, render_prometheus):
    ds = _ProbeDataSource(value={"queue": True}, ok=True)
    c = AsyncSystemProbeCollector(data_source=ds)
    c.collect(registry)
    text = render_prometheus(registry)
    assert 'openviking_async_system_readiness{probe="queue",valid="1"} 1.0' in text

    ds.raises = True
    c.collect(registry)
    text2 = render_prometheus(registry)
    assert 'openviking_async_system_readiness{probe="queue",valid="0"} 0.0' in text2


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
    root = Path(__file__).resolve().parents[3] / "openviking" / "metrics"
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


def test_runtime_code_has_no_prometheus_observer_references():
    root = Path(__file__).resolve().parents[3] / "openviking"
    banned = (
        "PrometheusObserver",
        "get_prometheus_observer",
        "set_prometheus_observer",
        "prometheus_observer.py",
    )
    for path in root.rglob("*.py"):
        text = path.read_text(encoding="utf-8")
        for needle in banned:
            assert needle not in text, f"{needle} still found in {path}"


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
