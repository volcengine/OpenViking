# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0

from __future__ import annotations

from types import SimpleNamespace

import openviking.metrics.datasources.encryption as enc
import openviking.metrics.datasources.retrieval as retrieval
import openviking.metrics.datasources.session as session
from openviking.metrics.datasources.base import EventMetricDataSource
from openviking.metrics.datasources.cache import CacheEventDataSource
from openviking.metrics.datasources.http import HttpRequestLifecycleDataSource


def test_event_datasources_use_shared_event_metric_datasource_emit(monkeypatch):
    calls: list[tuple[str, dict]] = []

    def _fake_emit(event_name: str, payload: dict) -> None:
        calls.append((str(event_name), dict(payload)))

    monkeypatch.setattr(EventMetricDataSource, "_emit", staticmethod(_fake_emit), raising=False)

    CacheEventDataSource.record_hit("L0")
    CacheEventDataSource.record_miss("L1")
    HttpRequestLifecycleDataSource.record_request(
        method="GET", route="/demo", status="200", duration_seconds=0.01
    )

    assert calls[0][0] == "cache.hit"
    assert calls[1][0] == "cache.miss"
    assert calls[2][0] == "http.request"


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


def test_retrieval_stats_datasource_emits_completed_event(patch_event_emit):
    """RetrievalStatsDataSource must emit a normalized `retrieval.completed` event payload."""

    retrieval.RetrievalStatsDataSource.record_retrieval(
        context_type="context_1",
        result_count=3,
        latency_seconds=0.12,
        rerank_used=True,
        rerank_fallback=False,
    )

    assert (
        "retrieval.completed",
        {
            "context_type": "context_1",
            "result_count": 3,
            "latency_seconds": 0.12,
            "rerank_used": True,
            "rerank_fallback": False,
        },
    ) in patch_event_emit


def test_retrieval_stats_datasource_normalizes_unknown_context_type(patch_event_emit):
    """Empty/falsey context types are normalized to `unknown`."""

    retrieval.RetrievalStatsDataSource.record_retrieval(
        context_type="",
        result_count=0,
        latency_seconds=0.0,
    )

    assert any(
        event_name == "retrieval.completed" and payload.get("context_type") == "unknown"
        for event_name, payload in patch_event_emit
    )


def test_session_lifecycle_datasource_emits_lifecycle_event(patch_event_emit):
    """SessionLifecycleDataSource must emit `session.lifecycle` with bounded labels."""

    session.SessionLifecycleDataSource.record_lifecycle(action="create", status="ok")
    assert ("session.lifecycle", {"action": "create", "status": "ok"}) in patch_event_emit


def test_session_lifecycle_datasource_ignores_non_positive_context_deltas(monkeypatch):
    """SessionLifecycleDataSource must not emit when delta is non-positive."""

    calls: list[tuple[str, dict]] = []

    def _emit(event_name: str, payload: dict) -> None:
        calls.append((str(event_name), dict(payload)))
        raise RuntimeError("should not be called for non-positive deltas")

    monkeypatch.setattr(session.EventMetricDataSource, "_emit", staticmethod(_emit))
    session.SessionLifecycleDataSource.record_contexts_used(action="create", delta=0)
    session.SessionLifecycleDataSource.record_contexts_used(action="create", delta=-1)
    assert calls == []


def test_session_lifecycle_datasource_emits_archive_event(patch_event_emit):
    """SessionLifecycleDataSource must emit `session.archive` events."""

    session.SessionLifecycleDataSource.record_archive(status="ok")
    assert ("session.archive", {"status": "ok"}) in patch_event_emit
