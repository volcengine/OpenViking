# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: Apache-2.0

"""Tests for the Prometheus metrics exporter."""

import httpx
import pytest

from openviking.server.app import create_app
from openviking.server.config import PrometheusConfig, ServerConfig, TelemetryConfig
from openviking.storage.observers.prometheus_observer import (
    PrometheusObserver,
    get_prometheus_observer,
    set_prometheus_observer,
)


class TestPrometheusObserver:
    """Unit tests for PrometheusObserver recording and rendering."""

    def test_record_retrieval(self):
        obs = PrometheusObserver()
        obs.record_retrieval(0.05)
        obs.record_retrieval(0.12)
        text = obs.render_metrics()
        assert "openviking_retrieval_requests_total 2" in text
        assert "openviking_retrieval_latency_seconds_count 2" in text

    def test_record_embedding(self):
        obs = PrometheusObserver()
        obs.record_embedding(0.3)
        text = obs.render_metrics()
        assert "openviking_embedding_requests_total 1" in text
        assert "openviking_embedding_latency_seconds_count 1" in text

    def test_record_vlm_call(self):
        obs = PrometheusObserver()
        obs.record_vlm_call(1.5)
        text = obs.render_metrics()
        assert "openviking_vlm_calls_total 1" in text
        assert "openviking_vlm_call_duration_seconds_count 1" in text

    def test_cache_hit_miss(self):
        obs = PrometheusObserver()
        obs.record_cache_hit("L0")
        obs.record_cache_hit("L0")
        obs.record_cache_miss("L1")
        text = obs.render_metrics()
        assert 'openviking_cache_hits_total{level="L0"} 2' in text
        assert 'openviking_cache_misses_total{level="L1"} 1' in text

    def test_render_empty_metrics(self):
        obs = PrometheusObserver()
        text = obs.render_metrics()
        assert "openviking_retrieval_requests_total 0" in text
        assert "openviking_embedding_requests_total 0" in text
        assert "openviking_vlm_calls_total 0" in text

    def test_histogram_buckets(self):
        obs = PrometheusObserver()
        obs.record_retrieval(0.02)
        text = obs.render_metrics()
        assert 'openviking_retrieval_latency_seconds_bucket{le="0.05"} 1' in text
        assert 'openviking_retrieval_latency_seconds_bucket{le="+Inf"} 1' in text


class TestPrometheusObserverSingleton:
    """Tests for the module-level singleton accessor."""

    def test_default_is_none(self):
        set_prometheus_observer(None)
        assert get_prometheus_observer() is None

    def test_set_and_get(self):
        obs = PrometheusObserver()
        set_prometheus_observer(obs)
        assert get_prometheus_observer() is obs
        set_prometheus_observer(None)

    def test_clear_singleton(self):
        obs = PrometheusObserver()
        set_prometheus_observer(obs)
        set_prometheus_observer(None)
        assert get_prometheus_observer() is None


class TestRetrievalStatsPrometheusIntegration:
    """Test that RetrievalStatsCollector notifies the PrometheusObserver."""

    def test_record_query_notifies_prometheus(self):
        from openviking.retrieve.retrieval_stats import RetrievalStatsCollector

        obs = PrometheusObserver()
        set_prometheus_observer(obs)
        try:
            collector = RetrievalStatsCollector()
            collector.record_query(
                context_type="memory",
                result_count=3,
                scores=[0.8, 0.7, 0.6],
                latency_ms=42.5,
            )
            text = obs.render_metrics()
            assert "openviking_retrieval_requests_total 1" in text
            assert "openviking_retrieval_latency_seconds_count 1" in text
        finally:
            set_prometheus_observer(None)


@pytest.mark.asyncio
class TestMetricsEndpoint:
    """Tests for the /metrics HTTP endpoint."""

    async def test_metrics_disabled_returns_404(self):
        config = ServerConfig()
        app = create_app(config=config, service=None)
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
            resp = await client.get("/metrics")
            assert resp.status_code == 404

    async def test_metrics_enabled_returns_200(self):
        config = ServerConfig(
            telemetry=TelemetryConfig(prometheus=PrometheusConfig(enabled=True))
        )
        app = create_app(config=config, service=None)
        # Simulate lifespan setting the observer on app.state
        obs = PrometheusObserver()
        app.state.prometheus_observer = obs
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
            resp = await client.get("/metrics")
            assert resp.status_code == 200
            assert "openviking_retrieval_requests_total" in resp.text
