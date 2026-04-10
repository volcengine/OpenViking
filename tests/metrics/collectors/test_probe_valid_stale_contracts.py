"""Contract tests for probe collectors (valid/stale semantics)."""

from __future__ import annotations

from dataclasses import dataclass

import pytest

from openviking.metrics.collectors.async_system_probe import AsyncSystemProbeCollector
from openviking.metrics.collectors.encryption_probe import EncryptionProbeCollector
from openviking.metrics.collectors.model_provider_probe import ModelProviderProbeCollector
from openviking.metrics.collectors.retrieval_backend_probe import RetrievalBackendProbeCollector
from openviking.metrics.collectors.service_probe import ServiceProbeCollector
from openviking.metrics.collectors.storage_probe import StorageProbeCollector
from openviking.metrics.core.base import ReadEnvelope


@dataclass
class _ProbeDataSource:
    """
    Minimal stub datasource for probe collectors.

    The stub can return a `ReadEnvelope` (ok or failure) or raise an exception to exercise
    probe collector error paths.
    """

    value: object | None = None
    ok: bool = True
    raises: bool = False

    def read_probe_state(self):
        """Return a probe envelope or raise an exception depending on the configured flags."""
        if self.raises:
            raise RuntimeError("probe read failed")
        return ReadEnvelope(ok=self.ok, value=self.value, error_type="probe", error_message="fail")


def test_service_probe_collector_emits_valid_on_success_and_invalid_on_failure(
    registry, render_prometheus
):
    """ServiceProbeCollector must export readiness as `valid=1` on success and `valid=0` on failure."""
    ds = _ProbeDataSource(
        value={"service_readiness": True, "api_key_manager_readiness": False}, ok=True
    )
    c = ServiceProbeCollector(data_source=ds)
    c.collect(registry)
    text = render_prometheus(registry)
    assert 'openviking_service_readiness{valid="1"} 1.0' in text
    assert 'openviking_api_key_manager_readiness{valid="1"} 0.0' in text

    # Failure via envelope must flip to `valid=0` replacement series.
    ds.ok = False
    c.collect(registry)
    text2 = render_prometheus(registry)
    assert 'openviking_service_readiness{valid="0"} 0.0' in text2
    assert 'openviking_api_key_manager_readiness{valid="0"} 0.0' in text2


def test_storage_probe_collector_preserves_last_probe_set_and_marks_invalid(
    registry, render_prometheus
):
    """StorageProbeCollector must re-publish last-known probe set under `valid=0` on failure."""
    ds = _ProbeDataSource(value={"agfs": True, "other": False}, ok=True)
    c = StorageProbeCollector(data_source=ds)
    c.collect(registry)
    text = render_prometheus(registry)
    assert 'openviking_storage_readiness{probe="agfs",valid="1"} 1.0' in text
    assert 'openviking_storage_readiness{probe="other",valid="1"} 0.0' in text

    ds.raises = True
    with pytest.raises(RuntimeError):
        ds.read_probe_state()  # sanity: ensure exception path is exercised
    c.collect(registry)
    text2 = render_prometheus(registry)
    assert 'openviking_storage_readiness{probe="agfs",valid="0"} 0.0' in text2
    assert 'openviking_storage_readiness{probe="other",valid="0"} 0.0' in text2


def test_retrieval_backend_probe_collector_preserves_last_probe_set_and_marks_invalid(
    registry, render_prometheus
):
    """RetrievalBackendProbeCollector must re-publish last-known probes under `valid=0` on failure."""
    ds = _ProbeDataSource(value={"vikingdb": True}, ok=True)
    c = RetrievalBackendProbeCollector(data_source=ds)
    c.collect(registry)
    text = render_prometheus(registry)
    assert 'openviking_retrieval_backend_readiness{probe="vikingdb",valid="1"} 1.0' in text

    ds.ok = False
    c.collect(registry)
    text2 = render_prometheus(registry)
    assert 'openviking_retrieval_backend_readiness{probe="vikingdb",valid="0"} 0.0' in text2


def test_encryption_probe_collector_uses_last_provider_on_failure(registry, render_prometheus):
    """EncryptionProbeCollector must preserve last provider label when emitting `valid=0` series."""
    ds = _ProbeDataSource(value=(True, "volcengine"), ok=True)
    c = EncryptionProbeCollector(data_source=ds)
    c.collect(registry)
    text = render_prometheus(registry)
    assert 'openviking_encryption_component_health{valid="1"} 1.0' in text
    assert 'openviking_encryption_root_key_ready{valid="1"} 1.0' in text
    assert 'openviking_encryption_kms_provider_ready{provider="volcengine",valid="1"} 1.0' in text

    ds.raises = True
    c.collect(registry)
    text2 = render_prometheus(registry)
    assert 'openviking_encryption_component_health{valid="0"} 0.0' in text2
    assert 'openviking_encryption_root_key_ready{valid="0"} 0.0' in text2
    assert 'openviking_encryption_kms_provider_ready{provider="volcengine",valid="0"} 0.0' in text2


def test_model_provider_probe_collector_uses_last_provider_on_failure(registry, render_prometheus):
    """ModelProviderProbeCollector must preserve last provider label when emitting `valid=0` series."""
    ds = _ProbeDataSource(value={"provider": ("volcengine", True)}, ok=True)
    c = ModelProviderProbeCollector(data_source=ds)
    c.collect(registry)
    text = render_prometheus(registry)
    assert 'openviking_model_provider_readiness{provider="volcengine",valid="1"} 1.0' in text

    ds.ok = False
    c.collect(registry)
    text2 = render_prometheus(registry)
    assert 'openviking_model_provider_readiness{provider="volcengine",valid="0"} 0.0' in text2


def test_async_system_probe_collector_marks_invalid_on_failure(registry, render_prometheus):
    """AsyncSystemProbeCollector must publish `valid` and emit invalid series on failure."""
    ds = _ProbeDataSource(value={"queue": True}, ok=True)
    c = AsyncSystemProbeCollector(data_source=ds)
    c.collect(registry)
    text = render_prometheus(registry)
    assert 'openviking_async_system_readiness{probe="queue",valid="1"} 1.0' in text

    ds.raises = True
    c.collect(registry)
    text2 = render_prometheus(registry)
    assert 'openviking_async_system_readiness{probe="queue",valid="0"} 0.0' in text2
