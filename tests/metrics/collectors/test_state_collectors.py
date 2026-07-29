# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0

from __future__ import annotations

import time

from openviking.metrics.collectors.lock import LockCollector
from openviking.metrics.collectors.observer_health import ObserverHealthCollector
from openviking.metrics.collectors.queue import QueueCollector
from openviking.metrics.collectors.task_tracker import TaskTrackerCollector
from openviking.metrics.collectors.vikingdb import VikingDBCollector
from openviking.metrics.core.registry import MetricRegistry
from openviking.metrics.datasources.observer_state import (
    LockStateDataSource,
    ObserverStateDataSource,
    VikingDBStateDataSource,
)
from openviking.metrics.datasources.queue import QueuePipelineStateDataSource
from openviking.metrics.datasources.task import TaskStateDataSource
from openviking.metrics.exporters.prometheus import PrometheusExporter


def test_queue_collector_maps_status(monkeypatch):
    class DummyQueueStatus:
        def __init__(
            self, pending: int, in_progress: int, processed: int, error_count: int
        ) -> None:
            self.pending = pending
            self.in_progress = in_progress
            self.processed = processed
            self.error_count = error_count

    class DummyQueueManager:
        async def check_status(self):
            return {
                "semantic": DummyQueueStatus(3, 1, 10, 2),
                "embedding": DummyQueueStatus(5, 0, 7, 0),
            }

    monkeypatch.setattr(
        "openviking.metrics.datasources.queue.get_queue_manager",
        lambda: DummyQueueManager(),
    )
    registry = MetricRegistry()
    QueueCollector(data_source=QueuePipelineStateDataSource()).collect(registry)
    text = PrometheusExporter(registry=registry).render()
    assert 'openviking_queue_pending{queue="semantic"} 3.0' in text
    assert 'openviking_queue_in_progress{queue="semantic"} 1.0' in text
    assert 'openviking_queue_processed_total{queue="semantic"} 10' in text
    assert 'openviking_queue_errors_total{queue="semantic"} 2' in text


def test_task_tracker_collector_maps_counts(monkeypatch):
    class DummyTracker:
        def snapshot_counts_by_type(self):
            return {
                "session_commit": {"pending": 1, "running": 2, "completed": 3, "failed": 4},
            }

    import openviking.metrics.datasources.task as task_datasource_module

    monkeypatch.setattr(task_datasource_module, "get_task_tracker", lambda: DummyTracker())
    registry = MetricRegistry()
    TaskTrackerCollector(data_source=TaskStateDataSource()).collect(registry)
    text = PrometheusExporter(registry=registry).render()
    assert 'openviking_task_pending{task_type="session_commit"} 1.0' in text
    assert 'openviking_task_running{task_type="session_commit"} 2.0' in text
    assert 'openviking_task_completed{task_type="session_commit"} 3.0' in text
    assert 'openviking_task_failed{task_type="session_commit"} 4.0' in text


def test_task_tracker_collector_clears_disappeared_task_types():
    registry = MetricRegistry()
    collector = TaskTrackerCollector(data_source=TaskStateDataSource())

    collector.collect_hook(registry, {"session_commit": {"pending": 2}})
    text = PrometheusExporter(registry=registry).render()
    assert 'openviking_task_pending{task_type="session_commit"} 2.0' in text

    collector.collect_hook(registry, {})
    text2 = PrometheusExporter(registry=registry).render()
    assert 'task_type="session_commit"' not in text2


def test_observer_health_collector_maps_component_status():
    class Status:
        def __init__(self, ok: bool, err: bool) -> None:
            self.is_healthy = ok
            self.has_errors = err

    class Observer:
        queue = Status(True, False)
        models = Status(True, False)
        lock = Status(False, True)
        retrieval = Status(True, True)

        def vikingdb(self, ctx=None):
            return Status(True, False)

    class Debug:
        observer = Observer()

    class Service:
        debug = Debug()

    registry = MetricRegistry()
    ObserverHealthCollector(data_source=ObserverStateDataSource(service=Service())).collect(
        registry
    )
    text = PrometheusExporter(registry=registry).render()
    assert 'openviking_component_health{component="lock",valid="1"} 0.0' in text
    assert 'openviking_component_errors{component="lock",valid="1"} 1.0' in text
    assert 'openviking_component_health{component="vikingdb",valid="1"} 1.0' in text


def test_observer_state_collector_valid_and_failure_keeps_last_values(registry, render_prometheus):
    from openviking.metrics.collectors.observer_state import ObserverStateCollector

    class S:
        def __init__(self, ok: bool, err: bool):
            self.is_healthy = ok
            self.has_errors = err

    class DS:
        def __init__(self):
            self.fail = False

        def read_component_states(self):
            if self.fail:
                raise RuntimeError("boom")
            return {"a": S(True, False), "b": S(False, True), "c": S(True, True)}

    ds = DS()
    c = ObserverStateCollector(data_source=ds)
    c.collect(registry)
    text = render_prometheus(registry)
    assert 'openviking_observer_components_total{valid="1"} 3.0' in text
    assert 'openviking_observer_components_unhealthy{valid="1"} 1.0' in text
    assert 'openviking_observer_components_with_errors{valid="1"} 2.0' in text

    ds.fail = True
    c.collect(registry)
    text2 = render_prometheus(registry)
    assert 'openviking_observer_components_total{valid="0"} 3.0' in text2
    assert 'openviking_observer_components_unhealthy{valid="0"} 1.0' in text2
    assert 'openviking_observer_components_with_errors{valid="0"} 2.0' in text2


def test_model_usage_collector_delta_and_available_gauge(registry, render_prometheus):
    from openviking.metrics.collectors.model_usage import ModelUsageCollector

    class DS:
        def __init__(self):
            self.data = {
                "vlm": {
                    "available": True,
                    "usage_by_model": {
                        "m1": {
                            "usage_by_provider": {
                                "p1": {
                                    "prompt_tokens": 2,
                                    "completion_tokens": 3,
                                    "total_tokens": 5,
                                    "call_count": 1,
                                }
                            }
                        }
                    },
                },
                "embedding": {"available": False, "usage_by_model": {}},
                "rerank": {"available": False, "usage_by_model": {}},
            }

        def read_model_usage(self):
            return self.data

    ds = DS()
    c = ModelUsageCollector(data_source=ds)
    c.collect(registry)
    text = render_prometheus(registry)
    assert 'openviking_model_usage_available{model_type="vlm",valid="1"} 1.0' in text
    assert 'openviking_model_usage_available{model_type="embedding",valid="1"} 0.0' in text
    assert 'openviking_model_usage_available{model_type="rerank",valid="1"} 0.0' in text
    assert "openviking_model_usage_valid" not in text
    assert 'openviking_model_calls_total{model_name="m1",model_type="vlm",provider="p1"} 1' in text
    assert (
        'openviking_model_tokens_total{model_name="m1",model_type="vlm",provider="p1",token_type="total"} 5'
        in text
    )

    ds.data["vlm"]["usage_by_model"]["m1"]["usage_by_provider"]["p1"]["call_count"] = 2
    ds.data["vlm"]["usage_by_model"]["m1"]["usage_by_provider"]["p1"]["total_tokens"] = 7
    ds.data["vlm"]["usage_by_model"]["m1"]["usage_by_provider"]["p1"]["prompt_tokens"] = 3
    ds.data["vlm"]["usage_by_model"]["m1"]["usage_by_provider"]["p1"]["completion_tokens"] = 4
    c.collect(registry)
    text2 = render_prometheus(registry)
    assert 'openviking_model_calls_total{model_name="m1",model_type="vlm",provider="p1"} 2' in text2
    assert (
        'openviking_model_tokens_total{model_name="m1",model_type="vlm",provider="p1",token_type="total"} 7'
        in text2
    )


def test_model_usage_collector_failure_reuses_last_available_state_with_valid_zero(
    registry, render_prometheus
):
    from openviking.metrics.collectors.model_usage import ModelUsageCollector

    class DS:
        def __init__(self):
            self.fail = False

        def read_model_usage(self):
            if self.fail:
                raise RuntimeError("boom")
            return {
                "vlm": {"available": True, "usage_by_model": {}},
                "embedding": {"available": False, "usage_by_model": {}},
                "rerank": {"available": False, "usage_by_model": {}},
            }

    ds = DS()
    collector = ModelUsageCollector(data_source=ds)

    collector.collect(registry)
    ds.fail = True
    collector.collect(registry)

    text = render_prometheus(registry)
    assert 'openviking_model_usage_available{model_type="vlm",valid="0"} 1.0' in text
    assert 'openviking_model_usage_available{model_type="embedding",valid="0"} 0.0' in text
    assert 'openviking_model_usage_available{model_type="rerank",valid="0"} 0.0' in text


def test_lock_collector_counts_active_and_stale(monkeypatch):
    class Handle:
        def __init__(self, locks: int, last_active_at: float) -> None:
            self.locks = [object()] * locks
            self.last_active_at = last_active_at

    class DummyLockManager:
        def get_active_handles(self):
            now = time.time()
            return {
                "a": Handle(locks=2, last_active_at=now - 10),
                "b": Handle(locks=1, last_active_at=now - 1000),
            }

    monkeypatch.setattr(
        "openviking.metrics.datasources.observer_state.get_lock_manager",
        lambda: DummyLockManager(),
    )
    registry = MetricRegistry()
    LockCollector(data_source=LockStateDataSource()).collect(registry)
    text = PrometheusExporter(registry=registry).render()
    assert "openviking_lock_active 3.0" in text
    assert "openviking_lock_stale 1.0" in text


def test_vikingdb_collector_exports_health_and_count(monkeypatch):
    class DummyVikingDB:
        collection_name = "my_collection"

        async def health_check(self):
            return True

        async def count(self, filter=None, ctx=None):
            return 123

    class Service:
        _vikingdb_manager = DummyVikingDB()

    registry = MetricRegistry()
    VikingDBCollector(data_source=VikingDBStateDataSource(service=Service())).collect(registry)
    text = PrometheusExporter(registry=registry).render()
    assert 'openviking_vikingdb_collection_health{collection="my_collection",valid="1"} 1.0' in text
    assert (
        'openviking_vikingdb_collection_vectors{collection="my_collection",valid="1"} 123.0' in text
    )
