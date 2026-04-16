"""Module-level tests for `openviking.metrics.datasources.encryption`."""

from __future__ import annotations

from types import SimpleNamespace

import openviking.metrics.datasources.encryption as enc


def test_encryption_event_datasource_emits_operation_event(patch_event_emit):
    """EncryptionEventDataSource must emit a normalized `encryption.operation` event."""

    enc.EncryptionEventDataSource.record_operation(
        operation="encrypt",
        status="ok",
        duration_seconds=0.25,
    )

    assert (
        "encryption.operation",
        {"operation": "encrypt", "status": "ok", "duration_seconds": 0.25},
    ) in patch_event_emit


def test_encryption_event_datasource_ignores_non_positive_bytes(monkeypatch):
    """EncryptionEventDataSource must not emit `encryption.bytes` when size is non-positive."""

    calls: list[tuple[str, dict]] = []

    def _emit(event_name: str, payload: dict) -> None:
        calls.append((str(event_name), dict(payload)))
        raise RuntimeError("should not be called for non-positive bytes")

    monkeypatch.setattr(enc.EventMetricDataSource, "_emit", staticmethod(_emit))
    enc.EncryptionEventDataSource.record_bytes(operation="encrypt", size_bytes=0)
    enc.EncryptionEventDataSource.record_bytes(operation="encrypt", size_bytes=-1)
    assert calls == []


def test_encryption_probe_datasource_ok_and_provider(monkeypatch):
    """EncryptionProbeDataSource returns `(True, provider)` on successful bootstrap."""

    class _Cfg:
        encryption = SimpleNamespace(provider="volcengine")

    def _bootstrap():
        return None

    monkeypatch.setattr("openviking.crypto.config.bootstrap_encryption", _bootstrap)
    ds = enc.EncryptionProbeDataSource(config_provider=lambda: _Cfg())
    env = ds.read_probe_state()
    assert env.ok is True
    assert env.value == (True, "volcengine")


def test_encryption_probe_datasource_returns_default_on_exception(monkeypatch):
    """EncryptionProbeDataSource must return `(False, provider)` with `ok=False` on failures."""

    class _Cfg:
        encryption = SimpleNamespace(provider="volcengine")

    def _boom():
        raise RuntimeError("bootstrap failed")

    monkeypatch.setattr("openviking.crypto.config.bootstrap_encryption", _boom)
    ds = enc.EncryptionProbeDataSource(config_provider=lambda: _Cfg())
    env = ds.read_probe_state()
    assert env.ok is False
    assert env.value == (False, "volcengine")
    assert env.error_type == "RuntimeError"
