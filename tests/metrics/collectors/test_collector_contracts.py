# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import pytest

from openviking.metrics.collectors.async_system_probe import AsyncSystemProbeCollector
from openviking.metrics.collectors.base import (
    CollectorConfig,
    DomainStatsMetricCollector,
    StateMetricCollector,
)
from openviking.metrics.collectors.encryption_probe import EncryptionProbeCollector
from openviking.metrics.collectors.model_provider_probe import ModelProviderProbeCollector
from openviking.metrics.collectors.retrieval_backend_probe import RetrievalBackendProbeCollector
from openviking.metrics.collectors.service_probe import ServiceProbeCollector
from openviking.metrics.collectors.storage_probe import StorageProbeCollector
from openviking.metrics.core.registry import MetricRegistry
from openviking.metrics.datasources import probes as probe_datasources
from openviking.metrics.datasources.encryption import EncryptionProbeDataSource


class _FailingStateCollector(StateMetricCollector):
    config = CollectorConfig()

    def read_metric_input(self):
        raise RuntimeError("state read failed")

    def collect_hook(self, registry, metric_input) -> None:
        raise AssertionError("collect_hook must not be called on read failure")


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


def test_state_default_error_strategy_raises():
    registry = MetricRegistry()
    collector = _FailingStateCollector()

    with pytest.raises(RuntimeError, match="state read failed"):
        collector.collect(registry)


def test_domain_stats_delta_helper_applies_non_negative_deltas_and_handles_resets():
    registry = MetricRegistry()
    collector = _DeltaDomainStatsCollector()

    collector.collect(registry)

    metrics = {
        m.name: m
        for r in registry.collect().resource_metrics
        for s in r.scope_metrics
        for m in s.metrics
    }
    assert metrics["openviking_test_cumulative_total"].data.data_points[0].value == 18


def test_service_probe_collector_emits_valid_on_success_and_invalid_on_failure(
    registry, render_prometheus
):
    app_state = Mock(return_value=SimpleNamespace(api_key_manager=object()))

    class App:
        @property
        def state(self):
            return app_state()

    ds = probe_datasources.ServiceProbeDataSource(
        app=App(), service=SimpleNamespace(initialized=True)
    )
    c = ServiceProbeCollector(data_source=ds)
    c.collect(registry)
    text = render_prometheus(registry)
    assert 'openviking_service_readiness{valid="1"} 1.0' in text
    assert 'openviking_api_key_manager_readiness{valid="1"} 1.0' in text

    app_state.side_effect = RuntimeError("state read failed")
    c.collect(registry)
    text2 = render_prometheus(registry)
    assert 'openviking_service_readiness{valid="0"} 0.0' in text2
    assert 'openviking_api_key_manager_readiness{valid="0"} 0.0' in text2


def test_storage_probe_collector_preserves_last_probe_set_and_marks_invalid(
    monkeypatch, registry, render_prometheus
):
    get_fs = Mock(return_value=SimpleNamespace(agfs=object()))
    monkeypatch.setattr(probe_datasources, "get_viking_fs", get_fs)
    c = StorageProbeCollector(data_source=probe_datasources.StorageProbeDataSource())
    c.collect(registry)
    text = render_prometheus(registry)
    assert 'openviking_storage_readiness{probe="agfs",valid="1"} 1.0' in text
    get_fs.side_effect = RuntimeError("filesystem unavailable")
    c.collect(registry)
    text2 = render_prometheus(registry)
    assert 'openviking_storage_readiness{probe="agfs",valid="0"} 0.0' in text2


def test_retrieval_backend_probe_collector_preserves_last_probe_set_and_marks_invalid(
    registry, render_prometheus
):
    service = SimpleNamespace()
    ds = probe_datasources.RetrievalBackendProbeDataSource(service=service)
    c = RetrievalBackendProbeCollector(data_source=ds)
    c.collect(registry)
    assert 'openviking_retrieval_backend_readiness{probe="vikingdb",valid="1"} 0.0' in (
        render_prometheus(registry)
    )
    health_check = AsyncMock(return_value=True)
    service.vikingdb = SimpleNamespace(health_check=health_check)
    c.collect(registry)
    text = render_prometheus(registry)
    assert 'openviking_retrieval_backend_readiness{probe="vikingdb",valid="1"} 1.0' in text

    health_check.side_effect = RuntimeError("VikingDB unavailable")
    c.collect(registry)
    text2 = render_prometheus(registry)
    assert 'openviking_retrieval_backend_readiness{probe="vikingdb",valid="0"} 0.0' in text2


def test_encryption_probe_collector_uses_last_provider_on_failure(
    monkeypatch, registry, render_prometheus
):
    bootstrap = Mock()
    monkeypatch.setattr("openviking.crypto.config.bootstrap_encryption", bootstrap)
    config_provider = Mock(
        return_value=SimpleNamespace(encryption=SimpleNamespace(provider="volcengine"))
    )
    ds = EncryptionProbeDataSource(config_provider=config_provider)
    c = EncryptionProbeCollector(data_source=ds)
    c.collect(registry)
    text = render_prometheus(registry)
    assert 'openviking_encryption_component_health{valid="1"} 1.0' in text
    assert 'openviking_encryption_root_key_ready{valid="1"} 1.0' in text
    assert 'openviking_encryption_kms_provider_ready{provider="volcengine",valid="1"} 1.0' in text

    bootstrap.side_effect = RuntimeError("encryption bootstrap failed")
    c.collect(registry)
    assert 'openviking_encryption_kms_provider_ready{provider="volcengine",valid="0"} 0.0' in (
        render_prometheus(registry)
    )
    config_provider.side_effect = RuntimeError("encryption config unavailable")
    c.collect(registry)
    text2 = render_prometheus(registry)
    assert 'openviking_encryption_component_health{valid="0"} 0.0' in text2
    assert 'openviking_encryption_root_key_ready{valid="0"} 0.0' in text2
    assert 'openviking_encryption_kms_provider_ready{provider="volcengine",valid="0"} 0.0' in text2


def test_model_provider_probe_collector_uses_last_provider_on_failure(registry, render_prometheus):
    get_vlm_instance = Mock(return_value=object())
    config = SimpleNamespace(
        vlm=SimpleNamespace(provider="volcengine", get_vlm_instance=get_vlm_instance)
    )
    ds = probe_datasources.ModelProviderProbeDataSource(config_provider=lambda: config)
    c = ModelProviderProbeCollector(data_source=ds)
    c.collect(registry)
    text = render_prometheus(registry)
    assert 'openviking_model_provider_readiness{provider="volcengine",valid="1"} 1.0' in text

    get_vlm_instance.side_effect = RuntimeError("model client unavailable")
    c.collect(registry)
    text2 = render_prometheus(registry)
    assert 'openviking_model_provider_readiness{provider="volcengine",valid="0"} 0.0' in text2


def test_async_system_probe_collector_marks_invalid_on_failure(
    monkeypatch, registry, render_prometheus
):
    monkeypatch.setattr(probe_datasources, "get_queue_manager", lambda: object())
    ds = probe_datasources.AsyncSystemProbeDataSource()
    c = AsyncSystemProbeCollector(data_source=ds)
    c.collect(registry)
    text = render_prometheus(registry)
    assert 'openviking_async_system_readiness{probe="queue",valid="1"} 1.0' in text

    def _raise_queue_manager():
        raise RuntimeError("probe read failed")

    monkeypatch.setattr(probe_datasources, "get_queue_manager", _raise_queue_manager)
    c.collect(registry)
    text2 = render_prometheus(registry)
    assert 'openviking_async_system_readiness{probe="queue",valid="0"} 0.0' in text2


def test_state_metric_collector_collect_error_hook_can_handle_read_failures():
    registry = MetricRegistry()
    collector = _DummyFailingStateCollector()

    collector.collect(registry)

    assert collector.error_seen == "boom"
