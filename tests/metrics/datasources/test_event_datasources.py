# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0

from __future__ import annotations

from types import SimpleNamespace

import openviking.metrics.datasources.encryption as enc
import openviking.metrics.datasources.retrieval as retrieval
import openviking.metrics.datasources.session as session
import openviking.metrics.datasources.vikingdb as vikingdb
from openviking.metrics.datasources.base import EventMetricDataSource
from openviking.metrics.datasources.cache import CacheEventDataSource
from openviking.metrics.datasources.http import HttpRequestLifecycleDataSource
from openviking.storage.errors import VikingDBException


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


def test_vikingdb_datasource_emits_operation_and_retry_events(patch_event_emit):
    vikingdb.VikingDBEventDataSource.record_operation(
        backend="bytedviking",
        access_mode="direct",
        operation="upsert",
        duration_seconds=0.2,
        result="record-1",
    )
    vikingdb.VikingDBEventDataSource.record_retry(
        backend="bytedviking",
        operation="upsert",
        error_source="http_status",
        error_code="429",
        delay_seconds=0.1,
    )
    assert ("vikingdb.operation", {
        "backend": "bytedviking", "access_mode": "direct", "operation": "upsert",
        "outcome": "success", "error_source": "none", "error_code": "OK",
        "error_category": "none", "retryable": "false", "duration_seconds": 0.2,
        "records": 1, "record_direction": "written",
    }) in patch_event_emit
    assert ("vikingdb.retry", {
        "backend": "bytedviking", "operation": "upsert", "error_source": "http_status",
        "error_code": "429", "delay_seconds": 0.1,
    }) in patch_event_emit


def test_vikingdb_datasource_preserves_handled_error_and_false_result(patch_event_emit):
    owns_metric, depth_token, error_token = vikingdb.VikingDBEventDataSource.enter_operation()
    assert owns_metric is True
    try:
        error = VikingDBException("rate limited", code="-55900461", retryable=True)
        vikingdb.VikingDBEventDataSource.record_handled_error(error)
        vikingdb.VikingDBEventDataSource.record_operation(
            backend="vikingdb",
            access_mode="default",
            operation="drop_collection",
            duration_seconds=0.1,
            result=False,
            error=vikingdb.VikingDBEventDataSource.handled_error(),
        )
    finally:
        vikingdb.VikingDBEventDataSource.exit_operation(depth_token, error_token)

    assert ("vikingdb.operation", {
        "backend": "vikingdb", "access_mode": "default", "operation": "drop_collection",
        "outcome": "error", "error_source": "backend", "error_code": "-55900461",
        "error_category": "unknown", "retryable": "true", "duration_seconds": 0.1,
    }) in patch_event_emit

    vikingdb.VikingDBEventDataSource.record_operation(
        backend="local",
        access_mode="default",
        operation="create_collection",
        duration_seconds=0.1,
        result=False,
    )
    assert any(
        event_name == "vikingdb.operation"
        and payload["operation"] == "create_collection"
        and payload["outcome"] == "error"
        and payload["error_code"] == "FALSE_RESULT"
        for event_name, payload in patch_event_emit
    )


def test_vikingdb_retry_uses_the_exception_error_code(patch_event_emit):
    vikingdb.VikingDBEventDataSource.record_retry(
        backend="vikingdb",
        operation="reindex_inventory",
        error=VikingDBException("rate limited", code="-55900461", retryable=True),
        delay_seconds=0.25,
    )

    assert ("vikingdb.retry", {
        "backend": "vikingdb", "operation": "reindex_inventory", "error_source": "backend",
        "error_code": "-55900461", "delay_seconds": 0.25,
    }) in patch_event_emit


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


def test_session_lifecycle_datasource_emits_archive_event(patch_event_emit):
    """SessionLifecycleDataSource must emit `session.archive` events."""

    session.SessionLifecycleDataSource.record_archive(status="ok")
    assert ("session.archive", {"status": "ok"}) in patch_event_emit
