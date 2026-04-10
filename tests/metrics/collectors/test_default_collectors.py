# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0

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
