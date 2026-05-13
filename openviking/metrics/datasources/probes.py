# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0

from __future__ import annotations

from typing import Any, Callable

from openviking.metrics.core.base import ReadEnvelope
from openviking.storage.queuefs import get_queue_manager
from openviking.storage.viking_fs import get_viking_fs
from openviking_cli.utils import run_async

from .base import ProbeMetricDataSource


class ServiceProbeDataSource(ProbeMetricDataSource):
    """Read high-level readiness probes from the service object and FastAPI application."""

    def __init__(self, *, app=None, service=None) -> None:
        """Store the optional app and service objects needed to read readiness indicators."""
        self._app = app
        self._service = service

    def read_probe_state(self) -> ReadEnvelope[dict[str, bool]]:
        """
        Read coarse service and API-key-manager readiness flags.

        Returns:
            A mapping of probe names to boolean readiness values derived from the current
            service initialization state and the FastAPI application's API key manager.
        """

        def _read():
            ready = bool(
                getattr(self._service, "initialized", False)
                or getattr(self._service, "_initialized", False)
            )
            api_key_ready = False
            if self._app is not None:
                api_key_ready = getattr(self._app.state, "api_key_manager", None) is not None
            return {"service_readiness": ready, "api_key_manager_readiness": api_key_ready}

        return self.safe_value_probe(
            _read,
            default={"service_readiness": False, "api_key_manager_readiness": False},
        )


class StorageProbeDataSource(ProbeMetricDataSource):
    """
    Read readiness state for the AGFS-backed storage subsystem.

    The probe intentionally reduces storage health to a simple boolean readiness signal that can
    be exported consistently across environments.
    """

    def read_probe_state(self) -> ReadEnvelope[dict[str, bool]]:
        """
        Check whether the AGFS-backed VikingFS storage dependency can be resolved.

        Failure to resolve the storage dependency is exported as a best-effort false value rather
        than propagating an exception into metrics collection.
        """
        return self.safe_bool_probe("agfs", lambda: bool(get_viking_fs().agfs))


class RetrievalBackendProbeDataSource(ProbeMetricDataSource):
    """
    Read readiness state for the retrieval backend currently used by the service.

    The probe currently assumes VikingDB is the active backend and normalizes its health check
    result into a single named readiness metric.
    """

    def __init__(self, *, service=None) -> None:
        """Store the optional service used to resolve the active retrieval backend manager."""
        self._service = service

    def read_probe_state(self) -> ReadEnvelope[dict[str, bool]]:
        """
        Check whether the active VikingDB retrieval backend passes its health check.

        Missing service wiring is treated as a negative readiness result instead of an exception.
        """

        def _check() -> bool:
            vikingdb = None
            if self._service is not None:
                vikingdb = getattr(self._service, "_vikingdb_manager", None) or getattr(
                    self._service, "vikingdb", None
                )
            if vikingdb is None:
                return False
            return bool(run_async(vikingdb.health_check()))

        return self.safe_bool_probe("vikingdb", _check)


class ModelProviderProbeDataSource(ProbeMetricDataSource):
    """
    Read readiness state for the configured model provider client.

    The datasource reports both the configured provider name and whether the client can be
    materialized successfully from the current configuration.
    """

    def __init__(self, *, config_provider: Callable[[], Any]) -> None:
        """Store the config provider used to resolve the current VLM provider configuration."""
        self._config_provider = config_provider

    def read_probe_state(self) -> ReadEnvelope[dict[str, tuple[str, bool]]]:
        """
        Resolve the configured VLM provider name and whether its client can be created.

        Returns:
            A mapping with one `provider` entry containing `(provider_name, healthy)`.
        """

        def _probe() -> tuple[str, bool]:
            config = self._config_provider()
            provider = str(getattr(getattr(config, "vlm", None), "provider", "") or "unknown")
            _ = config.vlm.get_vlm_instance()
            return provider, True

        return self.safe_tuple_probe("provider", _probe, default_name="unknown")


class AsyncSystemProbeDataSource(ProbeMetricDataSource):
    """
    Read readiness state for shared async-system infrastructure such as queue management.

    The queue manager is used as the coarse-grained readiness indicator for the shared async
    processing subsystem.
    """

    def read_probe_state(self) -> ReadEnvelope[dict[str, bool]]:
        """Check whether the shared async queue subsystem can be resolved successfully."""
        return self.safe_bool_probe("queue", lambda: bool(get_queue_manager()))
