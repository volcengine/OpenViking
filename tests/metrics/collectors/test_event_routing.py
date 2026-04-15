# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0

from openviking.metrics.collectors.base import EventMetricCollector
from openviking.metrics.collectors.cache import CacheCollector


class _DummyEventCollector(EventMetricCollector):
    SUPPORTED_EVENTS = frozenset({"demo.hit"})

    def __init__(self) -> None:
        self.seen: list[tuple[str, str]] = []

    def receive_hook(self, event_name: str, payload: dict, registry) -> None:
        self.seen.append(
            (
                str(event_name),
                str(payload["value"]),
                registry.collector_name() if hasattr(registry, "collector_name") else "registry",
            )
        )


def test_event_metric_collector_uses_supported_events_and_receive_hook(registry):
    collector = _DummyEventCollector()

    collector.receive("demo.hit", {"value": "ok"}, registry)
    collector.receive("demo.unknown", {"value": "skip"}, registry)

    assert collector.SUPPORTED_EVENTS == frozenset({"demo.hit"})
    assert collector.seen == [("demo.hit", "ok", "registry")]


def test_cache_collector_uses_supported_events_and_receive_hook_routing(
    registry, render_prometheus
):
    collector = CacheCollector()

    collector.receive("cache.hit", {"level": "L0"}, registry)
    collector.receive("cache.miss", {"level": "L1"}, registry)

    text = render_prometheus(registry)

    assert collector.SUPPORTED_EVENTS == frozenset({"cache.hit", "cache.miss"})
    assert 'openviking_cache_hits_total{level="L0"} 1' in text
    assert 'openviking_cache_misses_total{level="L1"} 1' in text
