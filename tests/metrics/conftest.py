"""
Shared fixtures for `tests/metrics/`.

The metrics test suite is split into feature-aligned subdirectories. This `conftest.py`
provides small, reusable fixtures/helpers that are broadly useful across those areas.
"""

from __future__ import annotations

from collections.abc import Callable, Iterator
from pathlib import Path
from typing import Any

import pytest

from openviking.metrics.core.registry import MetricRegistry
from openviking.metrics.datasources import EventMetricDataSource
from openviking.metrics.exporters.prometheus import PrometheusExporter
from openviking.metrics.global_api import configure_metric_account_dimension, shutdown_metrics


@pytest.fixture()
def project_root() -> Path:
    """
    Return the repository root directory.

    Some contract-style tests read source files directly via absolute paths; this helper
    makes those tests stable even when files are moved across `tests/metrics/*/`.
    """

    return Path(__file__).resolve().parents[2]


@pytest.fixture()
def registry() -> MetricRegistry:
    """
    Return a fresh metric registry for a single test.

    This ensures tests do not share cross-test metric state.
    """

    return MetricRegistry()


@pytest.fixture()
def render_prometheus() -> Callable[[MetricRegistry], str]:
    """
    Return a callable that renders a registry into Prometheus exposition format text.

    This is a fixture (not a plain helper import) so tests across subdirectories can use it
    without importing from `conftest.py`.
    """

    def _render(reg: MetricRegistry) -> str:
        return PrometheusExporter(registry=reg).render()

    return _render


@pytest.fixture()
def patch_event_emit(monkeypatch: pytest.MonkeyPatch) -> list[tuple[str, dict[str, Any]]]:
    """
    Patch the event datasource emission path and capture emitted events.

    Returns:
        A list of `(event_name, payload)` tuples captured during the test.
    """

    captured: list[tuple[str, dict[str, Any]]] = []

    def _emit(event_name: Any = None, payload: Any = None, **kwargs: Any) -> None:
        """
        Capture events emitted through the datasource layer.

        The production `_emit` API is called with positional arguments. Some tests may also
        call it with keyword arguments when patching.
        """
        if event_name is None and "event_name" in kwargs:
            event_name = kwargs["event_name"]
        if payload is None and "payload" in kwargs:
            payload = kwargs["payload"]
        captured.append((str(event_name), dict(payload or {})))

    monkeypatch.setattr(EventMetricDataSource, "_emit", staticmethod(_emit), raising=False)
    return captured


@pytest.fixture()
def configure_account_dimension() -> Iterator[Callable[..., None]]:
    """
    Provide a helper for configuring account dimension policy with guaranteed cleanup.

    The returned function forwards keyword arguments to `configure_metric_account_dimension()`.
    Regardless of test outcomes, `shutdown_metrics(app=None)` is executed to avoid leaking
    global metrics state across tests.
    """

    def _configure(**kwargs: Any) -> None:
        configure_metric_account_dimension(**kwargs)

    try:
        yield _configure
    finally:
        shutdown_metrics(app=None)
