# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0

from __future__ import annotations

import asyncio
import multiprocessing
from concurrent.futures import ThreadPoolExecutor
from threading import Event
from types import SimpleNamespace
from unittest.mock import Mock

import async_timeout
import pytest

from openviking.metrics.collectors.async_system_probe import AsyncSystemProbeCollector
from openviking.metrics.collectors.model_usage import ModelUsageCollector
from openviking.metrics.collectors.observer_health import ObserverHealthCollector
from openviking.metrics.collectors.queue import QueueCollector
from openviking.metrics.collectors.task_tracker import TaskTrackerCollector
from openviking.metrics.collectors.vikingdb import VikingDBCollector
from openviking.metrics.core.registry import MetricRegistry
from openviking.metrics.core.runtime import install_executor_monitor, uninstall_executor_monitor
from openviking.metrics.datasources.model_usage import ModelUsageDataSource
from openviking.metrics.datasources.observer_state import (
    ObserverStateDataSource,
    VikingDBStateDataSource,
)
from openviking.metrics.datasources.probes import AsyncSystemProbeDataSource
from openviking.metrics.datasources.queue import QueuePipelineStateDataSource
from openviking.metrics.datasources.task import TaskStateDataSource
from openviking.metrics.exporters.prometheus import PrometheusExporter
from openviking.models.vlm.token_usage import TokenUsageTracker


@pytest.mark.asyncio
async def test_executor_monitor_tracks_default_executor_only(registry, render_prometheus):
    loop = asyncio.get_running_loop()
    previous_default = getattr(loop, "_default_executor", None)
    default_executor = ThreadPoolExecutor(max_workers=1)
    custom_executor = ThreadPoolExecutor(max_workers=1)
    loop.set_default_executor(default_executor)
    install_executor_monitor(
        loop=loop,
        process_role="legacy_server",
    )
    collector = AsyncSystemProbeCollector(data_source=AsyncSystemProbeDataSource())
    labels = (
        'pool="asyncio_default",process_role="legacy_server",'
        f'worker="{multiprocessing.current_process().name}"'
    )
    started, release = Event(), Event()

    def blocking_default():
        started.set()
        release.wait(5)
        return "ok"

    def raising_default():
        raise RuntimeError("boom")

    try:
        first = loop.run_in_executor(None, blocking_default)
        async with async_timeout.timeout(2):
            while not started.is_set():
                await asyncio.sleep(0.01)
        second = loop.run_in_executor(None, lambda: "queued")

        collector.collect(registry)
        text = render_prometheus(registry)
        assert f"openviking_executor_active_tasks{{{labels}}} 1.0" in text
        assert f"openviking_executor_pending_tasks{{{labels}}} 1.0" in text
        assert f"openviking_executor_submitted_total{{{labels}}} 2" in text

        release.set()
        assert await first == "ok"
        assert await second == "queued"
        with pytest.raises(RuntimeError, match="boom"):
            await loop.run_in_executor(None, raising_default)
        assert await loop.run_in_executor(custom_executor, lambda: "custom") == "custom"

        collector.collect(registry)
        text = render_prometheus(registry)
        for name, value in {
            "max_workers": "1.0",
            "threads": "1.0",
            "active_tasks": "0.0",
            "pending_tasks": "0.0",
            "submitted_total": "3",
            "completed_total": "3",
            "failed_total": "1",
        }.items():
            assert f"openviking_executor_{name}{{{labels}}} {value}" in text
    finally:
        release.set()
        uninstall_executor_monitor()
        if previous_default is not None:
            loop.set_default_executor(previous_default)
        else:
            loop._default_executor = None  # type: ignore[attr-defined]
        custom_executor.shutdown(wait=True)
        default_executor.shutdown(wait=True)


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


def test_task_tracker_collector_maps_counts(monkeypatch, registry, render_prometheus):
    counts = {"pending": 1, "running": 2, "completed": 3, "failed": 4}
    snapshot = Mock(return_value={"session_commit": counts})
    tracker = SimpleNamespace(snapshot_counts_by_type=snapshot)
    monkeypatch.setattr("openviking.metrics.datasources.task.get_task_tracker", lambda: tracker)
    collector = TaskTrackerCollector(data_source=TaskStateDataSource())
    collector.collect(registry)

    text = render_prometheus(registry)
    for status, count in counts.items():
        assert f'openviking_task_{status}{{task_type="session_commit"}} {float(count)}' in text

    snapshot.return_value = {}
    collector.collect(registry)
    assert 'task_type="session_commit"' not in render_prometheus(registry)


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
    tracker = TokenUsageTracker()
    config = Mock(return_value=SimpleNamespace(rerank=None))
    service = SimpleNamespace(vlm_resolver=SimpleNamespace(get_node_token_usage=tracker.to_dict))
    source = ModelUsageDataSource(config_provider=config, service=service)
    collector = ModelUsageCollector(data_source=source)
    labels = 'model_name="m1",model_type="vlm",provider="p1"'

    for prompt, completion, calls, total in ((2, 3, 1, 5), (1, 1, 2, 7)):
        tracker.update("m1", "p1", prompt, completion)
        collector.collect(registry)
        text = render_prometheus(registry)
        assert f"openviking_model_calls_total{{{labels}}} {calls}" in text
        assert f'openviking_model_tokens_total{{{labels},token_type="total"}} {total}' in text
    assert "openviking_model_usage_valid" not in text
    for model_type in ("vlm", "embedding", "rerank"):
        available = 1.0 if model_type == "vlm" else 0.0
        assert (
            f'openviking_model_usage_available{{model_type="{model_type}",valid="1"}} {available}'
            in text
        )

    config.side_effect = RuntimeError("cannot read the node configuration")
    collector.collect(registry)
    stale = render_prometheus(registry)
    for model_type in ("vlm", "embedding", "rerank"):
        available = 1.0 if model_type == "vlm" else 0.0
        assert (
            f'openviking_model_usage_available{{model_type="{model_type}",valid="0"}} {available}'
            in stale
        )
    assert f"openviking_model_calls_total{{{labels}}} 2" in stale
    assert f'openviking_model_tokens_total{{{labels},token_type="total"}} 7' in stale


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


def test_ragfs_collector_replaces_recovers_and_deletes(monkeypatch):
    """Drive real registry writes through controlled native batches and faults; return None."""
    from openviking.metrics.collectors.ragfs import RagfsMetricCollector
    from openviking.metrics.datasources.ragfs import RagfsMetricDataSource

    records = []
    service = SimpleNamespace(_agfs_client=SimpleNamespace(metrics=lambda: records))
    collector = RagfsMetricCollector(data_source=RagfsMetricDataSource(service=service))
    registry = MetricRegistry()
    for count in (4, 2, 0):
        records[:] = [
            {
                "name": "ragfs_calls_total",
                "labels": {},
                "type": "counter",
                "value": count,
                "scale": 1.0,
            },
            {"name": "ragfs_tasks", "labels": {}, "type": "gauge", "value": float(count)},
            {
                "name": "ragfs_latency_seconds",
                "labels": {},
                "type": "histogram",
                "bucket_bounds": [1000],
                "bucket_counts": [count, 0],
                "count": count,
                "sum": 123 * count,
                "scale": 1e-9,
            },
        ]
        collector.collect(registry)
        metrics = {
            m.name: m
            for r in registry.collect().resource_metrics
            for s in r.scope_metrics
            for m in s.metrics
        }
        assert metrics["openviking_ragfs_calls_total"].data.data_points[0].value == count
        assert registry.gauge_get("openviking_ragfs_tasks") == count
        hist = metrics["openviking_ragfs_latency_seconds"].data.data_points[0]
        assert hist.explicit_bounds == pytest.approx((1e-6,))
        assert hist.bucket_counts == (count, 0) and hist.count == count
        assert hist.sum == pytest.approx(123e-9 * count)
        assert "account_id" not in PrometheusExporter(registry=registry).render()
    previous = PrometheusExporter(registry=registry).render()
    records.append({"broken": True})
    with pytest.raises(RuntimeError):
        collector.collect(registry)
    assert PrometheusExporter(registry=registry).render() == previous

    def fail(*args, **kwargs):
        """Raise an injected storage error for supplied write or delete arguments."""
        raise RuntimeError("injected")

    records[:] = [
        {
            "name": "ragfs_partial_total",
            "labels": {},
            "type": "counter",
            "value": 9,
            "scale": 1.0,
        },
        {"name": "ragfs_tasks", "labels": {}, "type": "gauge", "value": 1.0},
    ]
    with monkeypatch.context() as patch:
        patch.setattr(registry, "set_gauge", fail)
        with pytest.raises(RuntimeError, match="injected"):
            collector.collect(registry)
    assert "openviking_ragfs_partial_total 9.0" in PrometheusExporter(registry=registry).render()
    records.clear()
    with monkeypatch.context() as patch:
        patch.setattr(registry, "delete_matching", fail)
        with pytest.raises(RuntimeError, match="injected"):
            collector.collect(registry)
    collector.collect(registry)
    assert not registry.collect().resource_metrics[0].scope_metrics[0].metrics
    assert "openviking_ragfs_" not in PrometheusExporter(registry=registry).render()


def test_ragfs_collector_serializes_whole_refresh():
    """Hold a native read with Events and verify concurrent refresh cannot overwrite it."""
    from openviking.metrics.collectors.ragfs import RagfsMetricCollector
    from openviking.metrics.datasources.ragfs import RagfsMetricDataSource

    entered, release = Event(), Event()
    calls = []

    def metrics():
        """Block one native read until released and return a gauge record."""
        calls.append(1)
        entered.set()
        assert release.wait(5)
        return [{"name": "ragfs_tasks", "labels": {}, "type": "gauge", "value": 3.0}]

    collector = RagfsMetricCollector(
        data_source=RagfsMetricDataSource(
            service=SimpleNamespace(_agfs_client=SimpleNamespace(metrics=metrics))
        )
    )
    registry = MetricRegistry()
    with ThreadPoolExecutor(max_workers=2) as pool:
        first = pool.submit(collector.collect, registry)
        try:
            assert entered.wait(5)
            pool.submit(collector.collect, registry).result(timeout=2)
            assert len(calls) == 1
        finally:
            release.set()
        first.result(timeout=5)
    assert registry.gauge_get("openviking_ragfs_tasks") == 3
