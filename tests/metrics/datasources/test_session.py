"""Module-level tests for `openviking.metrics.datasources.session`."""

from __future__ import annotations

import openviking.metrics.datasources.session as session


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
