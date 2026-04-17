# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0

from openviking.metrics.datasources.telemetry_bridge import TelemetryBridgeEventDataSource
from openviking.metrics.global_api import init_metrics_from_server_config, shutdown_metrics
from openviking.server.config import MetricsConfig, ObservabilityConfig, ServerConfig


def test_telemetry_bridge_records_operation_and_resource_metrics(registry, render_prometheus):
    init_metrics_from_server_config(
        ServerConfig(observability=ObservabilityConfig(metrics=MetricsConfig(enabled=True))),
        app=None,
        registry=registry,
    )
    try:
        TelemetryBridgeEventDataSource.record_summary(
            {
                "operation": "resource.process",
                "status": "ok",
                "duration_ms": 1200,
                "tokens": {
                    "total": 14,
                    "llm": {"input": 3, "output": 7},
                    "rerank": {"total": 4},
                    "stages": {
                        "vlm": {"llm": {"input": 3, "output": 7, "total": 10}},
                        "rerank": {"rerank": {"total": 4}},
                    },
                },
                "vector": {"searches": 1, "scored": 2, "passed": 2, "returned": 1, "scanned": 9},
                "memory": {"extracted": 4},
                "semantic_nodes": {"OK": 12},
                "resource": {
                    "process": {
                        "parse": {"duration_ms": 10, "warnings_count": 1},
                        "finalize": {"duration_ms": 20},
                        "summarize": {"duration_ms": 30},
                        "duration_ms": 80,
                    },
                    "wait": {"duration_ms": 40},
                    "watch": {"duration_ms": 50},
                },
            }
        )
        text = render_prometheus(registry)
        assert (
            'openviking_operation_requests_total{account_id="__unknown__",operation="resource.process",status="ok"} 1'
            in text
        )
        assert (
            'openviking_operation_duration_seconds_count{account_id="__unknown__",operation="resource.process",status="ok"} 1'
            in text
        )
        assert (
            'openviking_operation_tokens_total{account_id="__unknown__",operation="resource.process",stage="rerank",token_type="rerank"} 4'
            in text
        )
        assert 'openviking_vector_searches_total{operation="resource.process"} 1' in text
        assert 'openviking_memory_extracted_total{operation="resource.process"} 4' in text
        assert (
            'openviking_semantic_nodes_total{status="OK"} 12' in text
            or 'openviking_semantic_nodes_total{status="OK"} 12.0' in text
        )
        assert (
            'openviking_resource_stage_total{account_id="__unknown__",stage="parse",status="warning"} 1'
            in text
        )
        assert (
            'openviking_resource_wait_duration_seconds_count{account_id="__unknown__",operation="resource.process"} 1'
            in text
        )
    finally:
        shutdown_metrics(app=None)


def test_telemetry_bridge_semantic_nodes_total_is_cumulative(registry, render_prometheus):
    init_metrics_from_server_config(
        ServerConfig(observability=ObservabilityConfig(metrics=MetricsConfig(enabled=True))),
        app=None,
        registry=registry,
    )
    try:
        TelemetryBridgeEventDataSource.record_summary(
            {
                "operation": "resource.process",
                "status": "ok",
                "duration_ms": 1,
                "tokens": {"total": 1, "llm": {"input": 1, "output": 0}},
                "semantic_nodes": {"OK": 12},
            }
        )
        TelemetryBridgeEventDataSource.record_summary(
            {
                "operation": "resource.process",
                "status": "ok",
                "duration_ms": 1,
                "tokens": {"total": 1, "llm": {"input": 1, "output": 0}},
                "semantic_nodes": {"OK": 12},
            }
        )
        text = render_prometheus(registry)
        assert (
            'openviking_semantic_nodes_total{status="OK"} 24' in text
            or 'openviking_semantic_nodes_total{status="OK"} 24.0' in text
        )
    finally:
        shutdown_metrics(app=None)
