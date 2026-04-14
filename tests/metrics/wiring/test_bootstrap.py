"""Module-level tests for `openviking.metrics.bootstrap`."""

from __future__ import annotations

import pytest

import openviking.metrics.bootstrap as bootstrap


def test_create_default_collector_manager_registers_expected_collectors_in_order():
    """Default bootstrap must register a stable collector sequence for `/metrics` refresh."""
    manager = bootstrap.create_default_collector_manager(app=None, service=None)

    names = [type(c).__name__ for c in manager._collectors]  # noqa: SLF001 (intentional white-box)
    assert names == [
        "QueueCollector",
        "TaskTrackerCollector",
        "ObserverHealthCollector",
        "ObserverStateCollector",
        "LockCollector",
        "VikingDBCollector",
        "ModelUsageCollector",
        "ServiceProbeCollector",
        "StorageProbeCollector",
        "RetrievalBackendProbeCollector",
        "ModelProviderProbeCollector",
        "AsyncSystemProbeCollector",
        "EncryptionProbeCollector",
    ]


def test_create_default_collector_manager_propagates_construction_failures(monkeypatch):
    """
    If a required datasource fails to construct, bootstrap should fail fast.

    This protects the server from silently starting with a partially wired metrics stack.
    """

    def _boom():
        raise RuntimeError("cannot init datasource")

    monkeypatch.setattr(bootstrap, "QueuePipelineStateDataSource", _boom)
    with pytest.raises(RuntimeError, match="cannot init datasource"):
        bootstrap.create_default_collector_manager(app=None, service=None)
