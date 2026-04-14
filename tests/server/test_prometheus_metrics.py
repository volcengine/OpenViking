# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0

"""Tests for the Prometheus metrics endpoint and exposition output."""

import httpx
import pytest

from openviking.metrics.core.registry import MetricRegistry
from openviking.metrics.exporters.prometheus import PrometheusExporter
from openviking.metrics.global_api import (
    get_metrics_registry,
    init_metrics_from_server_config,
    shutdown_metrics,
)
from openviking.server.app import create_app
from openviking.server.config import (
    MetricsAccountDimensionConfig,
    MetricsConfig,
    PrometheusConfig,
    ServerConfig,
    TelemetryConfig,
)


class TestPrometheusExposition:
    def test_counter_histogram_and_labels(self):
        registry = MetricRegistry()
        labels = {"context_type": "memory"}
        registry.counter(
            "openviking_retrieval_requests_total",
            label_names=("context_type",),
        ).inc(labels=labels)
        registry.counter(
            "openviking_retrieval_requests_total",
            label_names=("context_type",),
        ).inc(labels=labels)
        registry.histogram(
            "openviking_retrieval_latency_seconds",
            label_names=("context_type",),
        ).observe(0.02, labels=labels)

        registry.counter("openviking_cache_hits_total", label_names=("level",)).inc(
            labels={"level": "L0"}
        )
        registry.counter("openviking_cache_hits_total", label_names=("level",)).inc(
            labels={"level": "L0"}
        )
        registry.counter("openviking_cache_misses_total", label_names=("level",)).inc(
            labels={"level": "L1"}
        )

        exporter = PrometheusExporter(registry=registry)
        text = exporter.render()

        assert 'openviking_retrieval_requests_total{context_type="memory"} 2' in text
        assert 'openviking_retrieval_latency_seconds_count{context_type="memory"} 1' in text
        assert (
            'openviking_retrieval_latency_seconds_bucket{context_type="memory",le="0.05"} 1' in text
        )
        assert (
            'openviking_retrieval_latency_seconds_bucket{context_type="memory",le="+Inf"} 1' in text
        )
        assert 'openviking_cache_hits_total{level="L0"} 2' in text
        assert 'openviking_cache_misses_total{level="L1"} 1' in text


class TestRetrievalStatsMetricsIntegration:
    def test_record_query_updates_metrics_registry(self):
        from openviking.retrieve.retrieval_stats import RetrievalStatsCollector

        registry = MetricRegistry()
        shutdown_metrics(app=None)
        init_metrics_from_server_config(
            ServerConfig(telemetry=TelemetryConfig(prometheus=PrometheusConfig(enabled=True))),
            app=None,
            registry=registry,
        )
        try:
            collector = RetrievalStatsCollector()
            collector.record_query(
                context_type="memory",
                result_count=3,
                scores=[0.8, 0.7, 0.6],
                latency_ms=42.5,
            )
            exporter = PrometheusExporter(registry=get_metrics_registry())
            text = exporter.render()
            assert (
                'openviking_retrieval_requests_total{account_id="__unknown__",context_type="memory"} 1'
                in text
            )
            assert (
                'openviking_retrieval_results_total{account_id="__unknown__",context_type="memory"} 3'
                in text
            )
            assert (
                'openviking_retrieval_latency_seconds_count{account_id="__unknown__",context_type="memory"} 1'
                in text
            )
        finally:
            shutdown_metrics(app=None)


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
        config = ServerConfig(telemetry=TelemetryConfig(prometheus=PrometheusConfig(enabled=True)))
        app = create_app(config=config, service=None)
        init_metrics_from_server_config(config, app=app)
        transport = httpx.ASGITransport(app=app)
        try:
            registry = get_metrics_registry()
            registry.counter(
                "openviking_retrieval_requests_total",
                label_names=("context_type",),
            ).inc(labels={"context_type": "memory"})
            async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
                resp = await client.get("/metrics")
                assert resp.status_code == 200
                assert "openviking_retrieval_requests_total" in resp.text
        finally:
            shutdown_metrics(app=app)

    async def test_metrics_enabled_by_new_server_metrics_flag(self):
        config = ServerConfig(metrics=MetricsConfig(enabled=True))
        app = create_app(config=config, service=None)
        init_metrics_from_server_config(config, app=app)
        transport = httpx.ASGITransport(app=app)
        try:
            async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
                resp = await client.get("/metrics")
                assert resp.status_code == 200
        finally:
            shutdown_metrics(app=app)

    async def test_metrics_enabled_by_legacy_or_new_flag(self):
        config = ServerConfig(
            telemetry=TelemetryConfig(prometheus=PrometheusConfig(enabled=False)),
            metrics=MetricsConfig(enabled=True),
        )
        app = create_app(config=config, service=None)
        init_metrics_from_server_config(config, app=app)
        assert getattr(app.state, "metrics_exporter", None) is not None
        shutdown_metrics(app=app)

    async def test_metrics_account_dimension_config_is_loaded(self):
        config = ServerConfig(
            metrics=MetricsConfig(
                enabled=True,
                account_dimension=MetricsAccountDimensionConfig(
                    enabled=True,
                    max_active_accounts=3,
                    metric_allowlist=["openviking_http_requests_total"],
                ),
            )
        )
        app = create_app(config=config, service=None)
        init_metrics_from_server_config(config, app=app)
        try:
            assert config.metrics.account_dimension.enabled is True
            assert config.metrics.account_dimension.max_active_accounts == 3
            assert config.metrics.account_dimension.metric_allowlist == [
                "openviking_http_requests_total"
            ]
        finally:
            shutdown_metrics(app=app)
