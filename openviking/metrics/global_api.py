# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0

"""
Global metrics bootstrap and event dispatch.

This module provides:
- A process-global MetricRegistry and PrometheusExporter instance for the server.
- A lightweight in-process event router used by DataSources to emit events without
  directly depending on MetricRegistry internals.

Why an event router?
Business code (models, encryption, HTTP middleware, etc.) calls DataSource APIs such as
`VLMEventDataSource.record_call(...)`. DataSources emit events here. Collectors are the
only layer allowed to write into MetricRegistry, keeping the architecture consistent.
"""

from __future__ import annotations

import threading
from typing import Optional

from openviking.server.config import ServerConfig

from .account_dimension import (
    configure_metric_account_dimension as _configure_metric_account_dimension_runtime,
)
from .account_dimension import (
    reset_metric_account_dimension,
)
from .bootstrap import create_default_collector_manager
from .collectors.cache import CacheCollector
from .collectors.embedding import EmbeddingCollector
from .collectors.encryption import EncryptionCollector
from .collectors.http import HTTPCollector
from .collectors.rerank import RerankCollector
from .collectors.resource import ResourceIngestionCollector
from .collectors.retrieval import RetrievalCollector
from .collectors.session import SessionCollector
from .collectors.telemetry_bridge import TelemetryBridgeCollector
from .collectors.vlm import VLMCollector
from .core.registry import MetricRegistry
from .core.runtime import EventCollectorRouter
from .exporters.prometheus import PrometheusExporter

_lock = threading.Lock()
_registry: Optional[MetricRegistry] = None
_exporter: Optional[PrometheusExporter] = None
_event_router: EventCollectorRouter | None = None


def configure_metric_account_dimension(
    *,
    enabled: bool | None = None,
    metric_allowlist: set[str] | list[str] | tuple[str, ...] | None = None,
    max_active_accounts: int | None = None,
    policy=None,
    resolver=None,
) -> None:
    """
    Configure the process-global account-dimension runtime used by collector write helpers.

    This function is a thin façade over `account_dimension.configure_metric_account_dimension`
    so callers do not need to import policy internals directly from the metrics package.
    """
    _configure_metric_account_dimension_runtime(
        enabled=enabled,
        metric_allowlist=metric_allowlist,
        max_active_accounts=max_active_accounts,
        policy=policy,
        resolver=resolver,
    )


def init_metrics_from_server_config(
    config: ServerConfig, *, app=None, service=None, registry: MetricRegistry | None = None
) -> None:
    """
    Initialize the process-global metrics registry, router, and exporter from server config.

    When metrics are enabled:
    - Creates a MetricRegistry (or uses provided one)
    - Registers default collectors (State/Probe/DomainStats)
    - Builds the in-process event router for Event-style metrics
    - Creates PrometheusExporter and stores it in app.state.metrics_exporter
    When metrics are disabled:
    - Clears all global metrics state
    - Resets account-dimension runtime to its disabled defaults
    - Detaches any exporter reference previously stored on `app.state`
    """
    enabled = config.observability.metrics.enabled
    global _registry, _exporter, _event_router
    with _lock:
        if not enabled:
            _registry = None
            _exporter = None
            _event_router = None
            reset_metric_account_dimension()
            if app is not None:
                app.state.metrics_exporter = None
            return
        account_dimension = config.observability.metrics.account_dimension
        configure_metric_account_dimension(
            enabled=account_dimension.enabled,
            metric_allowlist=list(account_dimension.metric_allowlist or []),
            max_active_accounts=int(account_dimension.max_active_accounts),
        )
        _registry = registry or MetricRegistry()
        collector_manager = create_default_collector_manager(app=app, service=service)
        _event_router = _build_event_router(_registry)
        _exporter = PrometheusExporter(registry=_registry, collector_manager=collector_manager)
        if app is not None:
            app.state.metrics_exporter = _exporter


def shutdown_metrics(*, app=None) -> None:
    """
    Clear all process-global metrics objects and detach the exporter from the application.

    Shutdown is intentionally idempotent so tests and service teardown paths can call it
    repeatedly without needing to coordinate current metrics initialization state.
    """
    global _registry, _exporter, _event_router
    with _lock:
        _registry = None
        _exporter = None
        _event_router = None
        reset_metric_account_dimension()
        if app is not None:
            app.state.metrics_exporter = None


def get_metrics_registry() -> MetricRegistry:
    """
    Return the process-global `MetricRegistry`.

    Raises:
        RuntimeError: If metrics have not been initialized yet or were explicitly disabled.
    """
    reg = _registry
    if reg is None:
        raise RuntimeError("metrics registry not initialized")
    return reg


def try_get_metrics_registry() -> MetricRegistry | None:
    """
    Return the global MetricRegistry if metrics are initialized, otherwise `None`.

    This helper is intended for best-effort call sites that want to avoid raising an exception
    when metrics are disabled.
    """
    return _registry


def try_dispatch_event(event_name: str, payload: dict) -> None:
    """
    Dispatch an in-process metrics event.

    This is a best-effort API: if metrics are not initialized, it is a no-op.
    Callers must not assume the event is recorded.
    """
    router = _event_router
    if router is None:
        return
    router.dispatch(event_name, payload)


def _build_event_router(registry: MetricRegistry) -> EventCollectorRouter:
    """
    Build the default in-process event router for event-driven metrics.

    The router binds each supported event name to the corresponding collector instance and
    ensures every handler receives the shared registry object that backs the active exporter.
    """
    router = EventCollectorRouter()
    http_collector = HTTPCollector()
    cache_collector = CacheCollector()
    embedding_collector = EmbeddingCollector()
    rerank_collector = RerankCollector()
    vlm_collector = VLMCollector()
    session_collector = SessionCollector()
    resource_collector = ResourceIngestionCollector()
    retrieval_collector = RetrievalCollector()
    encryption_collector = EncryptionCollector()
    telemetry_bridge_collector = TelemetryBridgeCollector()

    def _receiver(collector, event_name: str):
        """
        Create a payload handler bound to a specific collector and event name.

        The returned handler closes over the global registry and forwards the payload to the
        collector's `receive(...)` method, preserving the architectural rule that collectors are
        the only layer allowed to write into `MetricRegistry`.
        """

        def _handle(payload):
            """Forward one event payload into the bound collector without altering the payload."""
            collector.receive(event_name, payload, registry)

        return _handle

    for event_name, collector in (
        ("http.request", http_collector),
        ("http.inflight", http_collector),
        ("cache.hit", cache_collector),
        ("cache.miss", cache_collector),
        ("embedding.call", embedding_collector),
        ("embedding.success", embedding_collector),
        ("embedding.error", embedding_collector),
        ("rerank.call", rerank_collector),
        ("vlm.call", vlm_collector),
        ("session.lifecycle", session_collector),
        ("session.contexts_used", session_collector),
        ("session.archive", session_collector),
        ("resource.stage", resource_collector),
        ("resource.wait", resource_collector),
        ("retrieval.completed", retrieval_collector),
        ("encryption.operation", encryption_collector),
        ("encryption.bytes", encryption_collector),
        ("encryption.payload_size", encryption_collector),
        ("encryption.auth_failed", encryption_collector),
        ("encryption.key_derivation", encryption_collector),
        ("encryption.key_load", encryption_collector),
        ("encryption.key_cache_hit", encryption_collector),
        ("encryption.key_cache_miss", encryption_collector),
        ("encryption.key_version_usage", encryption_collector),
        ("telemetry.summary", telemetry_bridge_collector),
    ):
        router.register(event_name, _receiver(collector, event_name))
    return router
