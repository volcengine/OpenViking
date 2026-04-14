# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0

from pathlib import Path

from openviking.metrics.core.base import MetricCollector


def test_metric_collector_metric_name_includes_namespace_and_optional_unit():
    assert (
        MetricCollector.metric_name("cache", "hits", unit="total") == "openviking_cache_hits_total"
    )
    assert (
        MetricCollector.metric_name("resource", "stage_duration", unit="seconds")
        == "openviking_resource_stage_duration_seconds"
    )
    assert MetricCollector.metric_name("lock", "active") == "openviking_lock_active"


def test_collectors_do_not_embed_openviking_metric_name_literals():
    root = Path(__file__).resolve().parents[2] / "openviking" / "metrics" / "collectors"
    offenders = []
    for path in root.glob("*.py"):
        text = path.read_text(encoding="utf-8")
        if '"openviking_' in text or "'openviking_" in text:
            offenders.append(path.name)
    assert offenders == []
