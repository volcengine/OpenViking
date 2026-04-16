# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0

from openviking.metrics.collectors.task_tracker import TaskTrackerCollector
from openviking.metrics.core.registry import MetricRegistry
from openviking.metrics.datasources.task import TaskStateDataSource
from openviking.metrics.exporters.prometheus import PrometheusExporter


def test_task_tracker_collector_clears_disappeared_task_types():
    registry = MetricRegistry()
    collector = TaskTrackerCollector(data_source=TaskStateDataSource())

    collector.collect_hook(registry, {"session_commit": {"pending": 2}})
    text = PrometheusExporter(registry=registry).render()
    assert 'openviking_task_pending{task_type="session_commit"} 2.0' in text

    # When the snapshot no longer includes the task type, previously-exported series must not
    # remain stale forever.
    collector.collect_hook(registry, {})
    text2 = PrometheusExporter(registry=registry).render()
    assert 'task_type="session_commit"' not in text2
