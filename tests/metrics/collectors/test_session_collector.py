"""Module-level tests for `openviking.metrics.collectors.session`."""

from __future__ import annotations

from openviking.metrics.collectors.session import SessionCollector


def test_session_collector_records_lifecycle_and_contexts_and_archive(registry, render_prometheus):
    """SessionCollector must translate supported events into bounded counters."""
    c = SessionCollector()
    c.receive("session.lifecycle", {"action": "create", "status": "ok"}, registry)
    c.receive("session.contexts_used", {"action": "create", "delta": 2}, registry)
    c.receive("session.archive", {"status": "ok"}, registry)

    text = render_prometheus(registry)
    assert (
        'openviking_session_lifecycle_total{account_id="__unknown__",action="create",status="ok"} 1'
        in text
    )
    assert (
        'openviking_session_contexts_used_total{account_id="__unknown__",action="create"} 2' in text
    )
    assert 'openviking_session_archive_total{account_id="__unknown__",status="ok"} 1' in text


def test_session_collector_ignores_malformed_payloads_instead_of_raising(
    registry, render_prometheus
):
    """Missing keys must not raise; the collector should best-effort ignore malformed payloads."""
    c = SessionCollector()

    # Guard: base collector should ignore non-dict payloads.
    c.receive("session.lifecycle", "not-a-dict", registry)

    # Malformed dicts: should not raise and should not modify counters.
    c.receive("session.lifecycle", {"action": "create"}, registry)
    c.receive("session.contexts_used", {"action": "create"}, registry)
    c.receive("session.archive", {}, registry)

    text = render_prometheus(registry)
    assert "openviking_session_lifecycle_total" not in text
    assert "openviking_session_contexts_used_total" not in text
    assert "openviking_session_archive_total" not in text
