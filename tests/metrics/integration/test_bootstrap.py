# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0

from __future__ import annotations

import re
from types import SimpleNamespace

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


@pytest.mark.asyncio
async def test_default_collectors_publish_readiness_without_feedback(registry, render_prometheus):
    manager = bootstrap.create_default_collector_manager(
        app=SimpleNamespace(state=SimpleNamespace()),
        service=SimpleNamespace(initialized=True),
    )

    await manager.refresh_all(registry, deadline_seconds=2.0)

    text = render_prometheus(registry)
    assert 'openviking_service_readiness{valid="1"} 1.0' in text
    assert 'openviking_api_key_manager_readiness{valid="1"} 0.0' in text
    assert "openviking_storage_readiness{" in text
    assert "openviking_feedback_" not in text


def test_create_default_collector_manager_propagates_construction_failures(monkeypatch):
    def _boom():
        raise RuntimeError("cannot init datasource")

    monkeypatch.setattr(bootstrap, "QueuePipelineStateDataSource", _boom)
    with pytest.raises(RuntimeError, match="cannot init datasource"):
        bootstrap.create_default_collector_manager(app=None, service=None)


def test_resource_ingestion_event_datasource_can_drive_resource_ingestion_collector(monkeypatch):
    registry = MetricRegistry()
    collector = ResourceIngestionCollector()

    configure_metric_account_dimension(
        enabled=True,
        metric_allowlist={
            ResourceIngestionCollector.IMPORTS_TOTAL,
            ResourceIngestionCollector.STAGE_TOTAL,
            ResourceIngestionCollector.STAGE_DURATION_SECONDS,
            ResourceIngestionCollector.WAIT_DURATION_SECONDS,
        },
        max_active_accounts=10,
    )

    def _emit(event_name: str, payload: dict) -> None:
        collector.receive(event_name, payload, registry)

    monkeypatch.setattr(EventMetricDataSource, "_emit", staticmethod(_emit), raising=False)

    ResourceIngestionEventDataSource.record_import(
        pipeline="connector",
        source_type="repository",
        account_id="acct-resource",
    )
    ResourceIngestionEventDataSource.record_import(pipeline="connector", source_type="repository")
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
        'openviking_resource_imports_total{account_id="acct-resource",'
        'pipeline="connector",source_type="repository"} 1'
    ) in text
    assert (
        'openviking_resource_imports_total{account_id="__unknown__",'
        'pipeline="connector",source_type="repository"} 1'
    ) in text
    assert (
        'openviking_resource_stage_total{account_id="__unknown__",stage="parse",status="ok"} 1'
        in text
    )
    assert (
        'openviking_resource_stage_total{account_id="__unknown__",stage="parse",status="error"} 1'
        in text
    )
    assert re.search(
        r'openviking_resource_wait_duration_seconds_count\{account_id="__unknown__",'
        r'bucket_schema="[0-9a-f]{16}",operation="queue_processing"\} 1',
        text,
    )
    assert (
        'openviking_resource_stage_total{account_id="acct-resource",stage="parse",status="ok"} 1'
        in text
    )
    assert re.search(
        r'openviking_resource_wait_duration_seconds_count\{account_id="acct-resource",'
        r'bucket_schema="[0-9a-f]{16}",operation="queue_processing"\} 1',
        text,
    )

    reset_metric_account_dimension()
