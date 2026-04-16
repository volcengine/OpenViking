# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0

"""
Default metrics bootstrap.

This module wires DataSources and Collectors into a CollectorManager that is invoked
before each Prometheus scrape (`/metrics`).

The registered collectors are intentionally split into two categories:
- Per-scrape refresh (no TTL): cheap, purely in-process reads (e.g., queue/lock counters).
- TTL/SWR protected: collectors that may touch slower or less reliable subsystems
  (e.g., service/model probes, vikingdb state, aggregated model usage).

If you add a new collector:
- Prefer low cardinality labels.
- Decide whether it needs TTL refresh control.
- Add/extend tests under `tests/metrics`.

Notes:
- Default-enabled collectors must keep refresh best-effort: `/metrics` should remain available
  even if one subsystem is down.
- For aggregated collectors where a "failure default" is not meaningful, export `valid=1/0`
  (or a similar validity gauge) so dashboards/alerts can distinguish stale data.
"""

from __future__ import annotations

from openviking.metrics.collectors import (
    AsyncSystemProbeCollector,
    CollectorManager,
    EncryptionProbeCollector,
    LockCollector,
    ModelProviderProbeCollector,
    ModelUsageCollector,
    ObserverHealthCollector,
    ObserverStateCollector,
    QueueCollector,
    RetrievalBackendProbeCollector,
    ServiceProbeCollector,
    StorageProbeCollector,
    TaskTrackerCollector,
    VikingDBCollector,
)
from openviking.metrics.datasources.encryption import EncryptionProbeDataSource
from openviking.metrics.datasources.model_usage import ModelUsageDataSource
from openviking.metrics.datasources.observer_state import (
    LockStateDataSource,
    ObserverStateDataSource,
    VikingDBStateDataSource,
)
from openviking.metrics.datasources.probes import (
    AsyncSystemProbeDataSource,
    ModelProviderProbeDataSource,
    RetrievalBackendProbeDataSource,
    ServiceProbeDataSource,
    StorageProbeDataSource,
)
from openviking.metrics.datasources.queue import QueuePipelineStateDataSource
from openviking.metrics.datasources.task import TaskStateDataSource
from openviking_cli.utils.config.open_viking_config import get_openviking_config


def create_default_collector_manager(*, app=None, service=None) -> CollectorManager:
    """
    Build the default `CollectorManager` used by the server's Prometheus endpoint.

    The default manager mixes lightweight state collectors, probe collectors, and a few
    aggregated collectors that rely on TTL/SWR behavior inside `CollectorManager`.

    Args:
        app: FastAPI application instance needed by certain readiness probes.
        service: Service object that exposes in-process state and subsystem handles preferred by
            several collectors and datasources.

    Returns:
        A `CollectorManager` with the standard OpenViking collector set registered in the order
        expected by the metrics bootstrap path.
    """
    manager = CollectorManager()
    manager.register(QueueCollector(data_source=QueuePipelineStateDataSource()))
    manager.register(TaskTrackerCollector(data_source=TaskStateDataSource()))
    manager.register(ObserverHealthCollector(data_source=ObserverStateDataSource(service=service)))
    manager.register(ObserverStateCollector(data_source=ObserverStateDataSource(service=service)))
    manager.register(LockCollector(data_source=LockStateDataSource()))
    manager.register(VikingDBCollector(data_source=VikingDBStateDataSource(service=service)))
    manager.register(
        ModelUsageCollector(
            data_source=ModelUsageDataSource(config_provider=get_openviking_config, service=service)
        )
    )
    manager.register(
        ServiceProbeCollector(data_source=ServiceProbeDataSource(app=app, service=service))
    )
    manager.register(StorageProbeCollector(data_source=StorageProbeDataSource()))
    manager.register(
        RetrievalBackendProbeCollector(data_source=RetrievalBackendProbeDataSource(service=service))
    )
    manager.register(
        ModelProviderProbeCollector(
            data_source=ModelProviderProbeDataSource(config_provider=get_openviking_config)
        )
    )
    manager.register(AsyncSystemProbeCollector(data_source=AsyncSystemProbeDataSource()))
    manager.register(
        EncryptionProbeCollector(
            data_source=EncryptionProbeDataSource(config_provider=get_openviking_config)
        )
    )
    return manager
