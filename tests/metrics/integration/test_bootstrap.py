# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0

from __future__ import annotations

from pathlib import Path

import pytest

import openviking.metrics.bootstrap as bootstrap
from openviking.metrics.account_dimension import (
    configure_metric_account_dimension,
    reset_metric_account_dimension,
)
from openviking.metrics.collectors.resource import ResourceIngestionCollector
from openviking.metrics.core.registry import MetricRegistry
from openviking.metrics.datasources.base import EventMetricDataSource
from openviking.metrics.datasources.resource import ResourceIngestionEventDataSource
from openviking.metrics.exporters.prometheus import PrometheusExporter


def test_create_default_collector_manager_registers_expected_collectors_in_order():
    manager = bootstrap.create_default_collector_manager(app=None, service=None)

    names = [type(c).__name__ for c in manager._collectors]
    assert names == [
        "QueueCollector",
        "TaskTrackerCollector",
        "ObserverHealthCollector",
        "ObserverStateCollector",
        "LockCollector",
        "VikingDBCollector",
        "ModelUsageCollector",
        "ServiceProbeCollector",
        "StorageProbeCollector",
        "RetrievalBackendProbeCollector",
        "ModelProviderProbeCollector",
        "AsyncSystemProbeCollector",
        "EncryptionProbeCollector",
    ]


def test_create_default_collector_manager_propagates_construction_failures(monkeypatch):
    def _boom():
        raise RuntimeError("cannot init datasource")

    monkeypatch.setattr(bootstrap, "QueuePipelineStateDataSource", _boom)
    with pytest.raises(RuntimeError, match="cannot init datasource"):
        bootstrap.create_default_collector_manager(app=None, service=None)


def test_optional_cache_datasource_instrumentation_is_wired_into_key_call_sites():
    project_root = Path(__file__).resolve().parents[3]
    targets = [
        project_root / "openviking" / "storage" / "queuefs" / "semantic_dag.py",
        project_root / "openviking" / "storage" / "queuefs" / "semantic_processor.py",
        project_root / "openviking" / "session" / "memory" / "extract_loop.py",
    ]
    missing = []
    for path in targets:
        text = path.read_text(encoding="utf-8")
        if "CacheEventDataSource.record_" not in text:
            missing.append(path.name)
    assert missing == []


def test_resource_ingestion_event_datasource_can_drive_resource_ingestion_collector(monkeypatch):
    registry = MetricRegistry()
    collector = ResourceIngestionCollector()

    configure_metric_account_dimension(
        enabled=True,
        metric_allowlist={
            ResourceIngestionCollector.STAGE_TOTAL,
            ResourceIngestionCollector.STAGE_DURATION_SECONDS,
            ResourceIngestionCollector.WAIT_DURATION_SECONDS,
        },
        max_active_accounts=10,
    )

    def _emit(event_name: str, payload: dict) -> None:
        collector.receive(event_name, payload, registry)

    monkeypatch.setattr(EventMetricDataSource, "_emit", staticmethod(_emit), raising=False)

    ResourceIngestionEventDataSource.record_stage(stage="parse", status="ok", duration_seconds=0.01)
    ResourceIngestionEventDataSource.record_stage(
        stage="parse", status="error", duration_seconds=0.02
    )
    ResourceIngestionEventDataSource.record_stage(
        stage="parse",
        status="ok",
        duration_seconds=0.03,
        account_id="acct-resource",
    )
    ResourceIngestionEventDataSource.record_wait(
        operation="queue_processing", duration_seconds=0.03
    )
    ResourceIngestionEventDataSource.record_wait(
        operation="queue_processing",
        duration_seconds=0.04,
        account_id="acct-resource",
    )

    text = PrometheusExporter(registry=registry).render()
    assert (
        'openviking_resource_stage_total{account_id="__unknown__",stage="parse",status="ok"} 1'
        in text
    )
    assert (
        'openviking_resource_stage_total{account_id="__unknown__",stage="parse",status="error"} 1'
        in text
    )
    assert (
        'openviking_resource_wait_duration_seconds_count{account_id="__unknown__",operation="queue_processing"} 1'
        in text
    )
    assert (
        'openviking_resource_stage_total{account_id="acct-resource",stage="parse",status="ok"} 1'
        in text
    )
    assert (
        'openviking_resource_wait_duration_seconds_count{account_id="acct-resource",operation="queue_processing"} 1'
        in text
    )

    reset_metric_account_dimension()
