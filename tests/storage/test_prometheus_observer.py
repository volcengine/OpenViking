# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: Apache-2.0
"""Tests for PrometheusObserver metrics exporter."""

from openviking.storage.observers.prometheus_observer import PrometheusObserver


class TestPrometheusObserver:
    """Test suite for PrometheusObserver."""

    def test_implements_base_observer(self):
        observer = PrometheusObserver()
        assert observer.is_healthy() is True
        assert observer.has_errors() is False
        assert "Prometheus Metrics Exporter" in observer.get_status_table()

    def test_counter_increments(self):
        observer = PrometheusObserver()
        observer.record_retrieval(0.1)
        observer.record_retrieval(0.2)
        assert observer._retrieval_requests_total.value == 2

    def test_histogram_records(self):
        observer = PrometheusObserver()
        observer.record_retrieval(0.05)
        observer.record_embedding(0.03)
        observer.record_vlm_call(1.5)
        assert observer._retrieval_latency.count == 1
        assert observer._embedding_latency.count == 1
        assert observer._vlm_call_duration.count == 1

    def test_cache_metrics(self):
        observer = PrometheusObserver()
        observer.record_cache_hit("L0")
        observer.record_cache_hit("L0")
        observer.record_cache_miss("L0")
        observer.record_cache_hit("L1")
        assert observer._cache_hits["L0"].value == 2
        assert observer._cache_misses["L0"].value == 1
        assert observer._cache_hits["L1"].value == 1

    def test_gauge_values(self):
        observer = PrometheusObserver()
        observer.set_active_sessions(5)
        assert observer._active_sessions.value == 5
        observer.set_cache_size_bytes("L0", 1024)
        assert observer._cache_size_bytes["L0"].value == 1024

    def test_render_metrics_format(self):
        observer = PrometheusObserver()
        observer.record_retrieval(0.1)
        observer.record_embedding(0.05)
        observer.record_vlm_call(2.0)
        observer.record_cache_hit("L0")
        observer.record_cache_miss("L1")
        observer.set_active_sessions(3)
        observer.set_cache_size_bytes("L0", 2048)

        output = observer.render_metrics()

        # Verify counter lines
        assert "openviking_retrieval_requests_total 1" in output
        assert "openviking_embedding_requests_total 1" in output
        assert "openviking_vlm_calls_total 1" in output

        # Verify histogram lines
        assert "openviking_retrieval_latency_seconds_count 1" in output
        assert "openviking_retrieval_latency_seconds_sum 0.1" in output
        assert 'openviking_retrieval_latency_seconds_bucket{le="+Inf"} 1' in output

        # Verify cache lines
        assert 'openviking_cache_hits_total{level="L0"} 1' in output
        assert 'openviking_cache_misses_total{level="L1"} 1' in output

        # Verify gauge lines
        assert "openviking_active_sessions 3" in output
        assert 'openviking_cache_size_bytes{level="L0"} 2048' in output

        # Verify TYPE/HELP comments
        assert "# TYPE openviking_retrieval_requests_total counter" in output
        assert "# TYPE openviking_retrieval_latency_seconds histogram" in output
        assert "# TYPE openviking_active_sessions gauge" in output

    def test_render_metrics_empty(self):
        observer = PrometheusObserver()
        output = observer.render_metrics()
        assert "openviking_retrieval_requests_total 0" in output
        assert "openviking_active_sessions 0" in output
        # No cache levels registered, so no cache lines
        assert "openviking_cache_hits_total" not in output

    def test_histogram_bucket_boundaries(self):
        observer = PrometheusObserver()
        observer.record_retrieval(0.001)  # <= 0.005
        observer.record_retrieval(0.5)  # <= 0.5
        observer.record_retrieval(100.0)  # Only in +Inf

        output = observer.render_metrics()
        assert 'openviking_retrieval_latency_seconds_bucket{le="0.005"} 1' in output
        assert 'openviking_retrieval_latency_seconds_bucket{le="0.5"} 2' in output
        assert 'openviking_retrieval_latency_seconds_bucket{le="+Inf"} 3' in output
        assert "openviking_retrieval_latency_seconds_count 3" in output
