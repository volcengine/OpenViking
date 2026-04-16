# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0

from openviking.metrics.datasources.base import EventMetricDataSource
from openviking.metrics.datasources.cache import CacheEventDataSource
from openviking.metrics.datasources.http import HttpRequestLifecycleDataSource


def test_event_datasources_use_shared_event_metric_datasource_emit(monkeypatch):
    calls: list[tuple[str, dict]] = []

    def _fake_emit(event_name: str, payload: dict) -> None:
        calls.append((str(event_name), dict(payload)))

    monkeypatch.setattr(EventMetricDataSource, "_emit", staticmethod(_fake_emit), raising=False)

    CacheEventDataSource.record_hit("L0")
    CacheEventDataSource.record_miss("L1")
    HttpRequestLifecycleDataSource.record_request(
        method="GET", route="/demo", status="200", duration_seconds=0.01
    )

    assert calls[0][0] == "cache.hit"
    assert calls[1][0] == "cache.miss"
    assert calls[2][0] == "http.request"
