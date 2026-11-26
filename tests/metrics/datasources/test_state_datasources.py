# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0

from __future__ import annotations

from types import SimpleNamespace

import pytest


@pytest.mark.parametrize(
    "changes",
    [
        {},
        {"name": "openviking_calls"},
        {"name": "bad name"},
        {"value": True},
        {"value": -1},
        {"value": 2**64},
        {"scale": 0.0},
        {"scale": float("inf")},
        {"scale": 1e308},
        {"labels": {"mount": "/local"}},
        {"labels": {"account_id": "x"}},
        {"labels": {"path": "/a"}},
        {"labels": {"__name__": "x"}},
        {"type": "unknown"},
    ],
)
def test_ragfs_datasource_validates_records(changes):
    """Use native-shaped counter input to check validation and one read; return None."""
    from openviking.metrics.datasources.ragfs import RagfsMetricDataSource

    calls = []
    records = [
        {
            "name": "ragfs_calls_total",
            "labels": {},
            "type": "counter",
            "value": 123,
            "scale": 1.0,
        }
        | changes
    ]

    def metrics():
        """Record one native read and return the controlled batch."""
        calls.append(1)
        return records

    service = SimpleNamespace(_agfs_client=SimpleNamespace(metrics=metrics))
    source = RagfsMetricDataSource(service=service)
    result = source.read_metrics()
    assert result.ok is (not changes)
    assert result.value == (records if not changes else [])
    assert len(calls) == 1
    service._agfs_client = None
    assert source.read_metrics().ok is False


@pytest.mark.parametrize(
    "changes",
    [
        {},
        {"bucket_counts": [1]},
        {"bucket_counts": [True, 0]},
        {"labels": {"bucket_schema": "caller-supplied"}},
        {"count": 3},
        {"bucket_bounds": [2, 1]},
        {"sum": -1},
        {"bucket_bounds": [2**53, 2**53 + 1], "bucket_counts": [1, 0, 0]},
    ],
)
def test_ragfs_datasource_validates_histogram_and_family(changes):
    """Check histogram shape, duplicates and same-name contracts from batches; return None."""
    from openviking.metrics.datasources.ragfs import RagfsMetricDataSource

    record = {
        "name": "ragfs_duration_seconds",
        "labels": {"plugin": "localfs"},
        "type": "histogram",
        "bucket_bounds": [1000],
        "bucket_counts": [1, 0],
        "count": 1,
        "sum": 123,
        "scale": 1e-9,
    } | changes
    records = [record]
    source = RagfsMetricDataSource(
        service=SimpleNamespace(_agfs_client=SimpleNamespace(metrics=lambda: records))
    )
    assert source.read_metrics().ok is (not changes)
    if not changes:
        for other in (
            record,
            record | {"labels": {}},
            record | {"scale": 1.0},
            record | {"bucket_bounds": [2000]},
        ):
            records[:] = [record, other]
            assert source.read_metrics().ok is False
        records[:] = [{"name": "ragfs_tasks", "labels": {}, "type": "gauge", "value": -2.0}]
        assert source.read_metrics().ok
        records[0]["value"] = float("nan")
        assert not source.read_metrics().ok
