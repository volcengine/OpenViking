# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0

import asyncio

import pytest

from openviking.metrics.collectors.base import CollectorConfig, Refreshable
from openviking.metrics.collectors.manager import CollectorManager, RefreshResult
from openviking.metrics.core.refresh import RefreshGate
from openviking.metrics.core.registry import MetricRegistry


def test_refresh_gate_ttl():
    gate = RefreshGate(ttl_seconds=1.0)
    d0 = gate.decide(now=0.0)
    assert d0.do_refresh is True

    assert gate.mark_inflight() is True
    d1 = gate.decide(now=0.1)
    assert d1.do_refresh is False
    assert d1.reason == "inflight"

    gate.mark_complete(success=True, now=0.2)
    d2 = gate.decide(now=0.9)
    assert d2.do_refresh is False
    assert d2.reason == "ttl_valid"

    d3 = gate.decide(now=1.3)
    assert d3.do_refresh is True
    assert d3.reason == "ttl_expired"


@pytest.mark.asyncio
async def test_collector_manager_requires_refreshable():
    class DummyCollector:
        name = "dummy"
        config = CollectorConfig(ttl_seconds=10.0, timeout_seconds=0.01)

        def __init__(self) -> None:
            self.calls = 0

        def collect(self, registry) -> None:
            self.calls += 1
            registry.counter("openviking_dummy_refresh_total").inc()

    mgr = CollectorManager()
    c = DummyCollector()
    assert not isinstance(c, Refreshable)
    with pytest.raises(TypeError):
        mgr.register(c)


@pytest.mark.asyncio
async def test_collector_manager_ttl_and_timeout():
    class DummyCollector(Refreshable):
        name = "dummy"
        config = CollectorConfig(ttl_seconds=10.0, timeout_seconds=0.01)

        def __init__(self) -> None:
            self.calls = 0

        def collect(self, registry) -> None:
            self.calls += 1
            registry.counter("openviking_dummy_refresh_total").inc()

    registry = MetricRegistry()
    mgr = CollectorManager()
    c = DummyCollector()
    mgr.register(c)

    r1 = await mgr.refresh_all(registry, deadline_seconds=1.0)
    assert len(r1) == 1
    assert r1[0].collector == "dummy"
    assert r1[0].attempted is True

    r2 = await mgr.refresh_all(registry, deadline_seconds=1.0)
    assert len(r2) == 1
    assert r2[0].attempted is False
    assert r2[0].reason == "ttl_valid"


@pytest.mark.asyncio
async def test_collector_manager_swr_when_ttl_expired_and_has_last_success():
    class DummyCollector(Refreshable):
        name = "dummy"
        config = CollectorConfig(ttl_seconds=1.0, timeout_seconds=0.5)

        def __init__(self) -> None:
            self.calls = 0

        def collect(self, registry) -> None:
            self.calls += 1
            registry.inc_counter("openviking_dummy_refresh_total")

    registry = MetricRegistry()
    mgr = CollectorManager()
    c = DummyCollector()
    mgr.register(c)

    r1 = await mgr.refresh_all(registry, deadline_seconds=1.0)
    assert r1[0].attempted is True
    assert c.calls == 1

    r2 = await mgr.refresh_all(registry, deadline_seconds=1.0)
    assert r2[0].attempted is False

    await asyncio.sleep(1.1)
    r3 = await mgr.refresh_all(registry, deadline_seconds=1.0)
    assert r3[0].attempted is False
    assert r3[0].reason == "swr_triggered"


@pytest.mark.asyncio
async def test_collector_manager_deadline_exceeded_does_not_stick_inflight(monkeypatch):
    class DummyCollector(Refreshable):
        name = "dummy"
        config = CollectorConfig(ttl_seconds=10.0, timeout_seconds=10.0)

        def collect(self, registry) -> None:
            raise AssertionError("collect should not be called in this test")

    registry = MetricRegistry()
    mgr = CollectorManager()
    mgr.register(DummyCollector())

    started = asyncio.Event()
    release = asyncio.Event()

    async def _fake_run_collector(self, collector, registry, *, timeout: float):
        started.set()
        await release.wait()
        return RefreshResult(
            collector="dummy",
            attempted=True,
            success=True,
            reason="ok",
        )

    monkeypatch.setattr(CollectorManager, "_run_collector", _fake_run_collector)

    refresh_task = asyncio.create_task(mgr.refresh_all(registry, deadline_seconds=0.01))
    await asyncio.wait_for(started.wait(), timeout=0.5)
    r1 = await refresh_task
    assert r1
    assert any(item.collector == "dummy" and item.reason == "deadline_exceeded" for item in r1)

    r2 = await mgr.refresh_all(registry, deadline_seconds=1.0)
    assert r2 and r2[0].collector == "dummy"
    assert r2[0].attempted is False
    assert r2[0].reason == "inflight"

    release.set()
    await asyncio.wait_for(asyncio.sleep(0), timeout=0.5)
    r3 = await mgr.refresh_all(registry, deadline_seconds=1.0)
    assert r3 and r3[0].collector == "dummy"
    assert r3[0].attempted is False
    assert r3[0].reason == "ttl_valid"
