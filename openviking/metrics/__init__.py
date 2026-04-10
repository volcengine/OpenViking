# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0

"""
OpenViking metrics subsystem.

High-level architecture:
- DataSource: emits events or exposes read APIs; must not write MetricRegistry directly.
- Collector: the only writer to MetricRegistry; may be Event/State/Probe/DomainStats.
- CollectorManager: orchestrates refresh before each Prometheus scrape with TTL + SWR.
- PrometheusExporter: refreshes collectors (best-effort) and renders registry to text format.
"""

from .core.base import MetricCollector, MetricDataSource, MetricExporter
from .core.registry import MetricRegistry
from .exporters.prometheus import PrometheusExporter


def get_metrics_registry():
    """Lazily import and return the process-global metrics registry."""
    from .global_api import get_metrics_registry as _get_metrics_registry

    return _get_metrics_registry()


def init_metrics_from_server_config(config, *, registry=None, app=None, service=None):
    """Lazily initialize metrics from server configuration."""
    from .global_api import init_metrics_from_server_config as _init_metrics_from_server_config

    return _init_metrics_from_server_config(
        config,
        registry=registry,
        app=app,
        service=service,
    )


def shutdown_metrics(*, app=None):
    """Lazily clear global metrics objects and detach exporter from the application."""
    from .global_api import shutdown_metrics as _shutdown_metrics

    return _shutdown_metrics(app=app)


__all__ = [
    "MetricDataSource",
    "MetricCollector",
    "MetricExporter",
    "MetricRegistry",
    "PrometheusExporter",
    "get_metrics_registry",
    "init_metrics_from_server_config",
    "shutdown_metrics",
]
