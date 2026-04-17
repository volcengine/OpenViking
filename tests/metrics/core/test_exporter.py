# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0


def test_exporter_outputs_help_type_and_empty_metrics(registry, render_prometheus):
    registry.counter("openviking_empty_counter_total")
    registry.gauge("openviking_empty_gauge")
    registry.histogram("openviking_empty_histogram_seconds")
    text = render_prometheus(registry)
    assert "# TYPE openviking_empty_counter_total counter" in text
    assert "openviking_empty_counter_total 0" in text
    assert "# TYPE openviking_empty_histogram_seconds histogram" in text
    assert "openviking_empty_histogram_seconds_count 0" in text
    assert 'openviking_empty_histogram_seconds_bucket{le="+Inf"} 0' in text
