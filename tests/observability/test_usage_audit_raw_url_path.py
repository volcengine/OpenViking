# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0

"""Issue #4061: the audit store must keep the raw request path, not only the route template.

`HttpRequestLifecycleDataSource.record_request()` already emits `url_path` alongside the
low-cardinality `route`, but the audit projection read only `route`, so a 404 on a
parameterized endpoint was persisted as `/api/v1/sessions/{session_id}` and the concrete
failing id was unrecoverable afterwards. Uvicorn access logs are off by default, so there
was no second source.

`route` stays the aggregation key — these tests pin that it is unchanged.
"""

from __future__ import annotations

from datetime import datetime, timezone

from openviking.observability.events import ObservabilityEvent
from openviking.observability.usage_audit.sqlite_store import SQLiteUsageAuditStore


def _http_event(payload: dict, *, request_id: str) -> ObservabilityEvent:
    return ObservabilityEvent(
        event_name="http.request",
        payload={"request_id": request_id, **payload},
        timestamp=datetime(2026, 5, 12, 1, 2, 3, tzinfo=timezone.utc),
        request_id=request_id,
        account_id="acct-1",
        user_id="user-1",
    )


def _session_request(session_id: str, *, status: int, request_id: str) -> ObservabilityEvent:
    return _http_event(
        {
            "method": "GET",
            "route": "/api/v1/sessions/{session_id}",
            "url_path": f"/api/v1/sessions/{session_id}",
            "status": status,
            "duration_ms": 4.0,
            "account_id": "acct-1",
            "user_id": "user-1",
        },
        request_id=request_id,
    )


async def test_raw_url_path_is_recoverable_for_a_parameterized_404(tmp_path):
    store = SQLiteUsageAuditStore(tmp_path / "usage.sqlite3")
    await store.initialize()
    try:
        await store.record_batch([_session_request("sess-missing", status=404, request_id="r1")])
        audit = await store.query_audit_logs(account_id="acct-1")

        assert audit["total"] == 1
        item = audit["items"][0]
        assert item["url_path"] == "/api/v1/sessions/sess-missing"
        # The template is the aggregation key and must be untouched.
        assert item["route"] == "/api/v1/sessions/{session_id}"
        assert item["status_code"] == 404
    finally:
        await store.close()


async def test_two_ids_under_one_template_stay_distinguishable(tmp_path):
    # The reported diagnostic need: repeated 404s aggregate under one route, but the
    # operator has to be able to tell WHICH ids failed.
    store = SQLiteUsageAuditStore(tmp_path / "usage.sqlite3")
    await store.initialize()
    try:
        await store.record_batch(
            [
                _session_request("sess-a", status=404, request_id="r1"),
                _session_request("sess-b", status=404, request_id="r2"),
                _session_request("sess-ok", status=200, request_id="r3"),
            ]
        )
        audit = await store.query_audit_logs(account_id="acct-1")

        assert audit["total"] == 3
        assert {item["route"] for item in audit["items"]} == {"/api/v1/sessions/{session_id}"}
        assert {item["url_path"] for item in audit["items"]} == {
            "/api/v1/sessions/sess-a",
            "/api/v1/sessions/sess-b",
            "/api/v1/sessions/sess-ok",
        }
        failed = {item["url_path"] for item in audit["items"] if item["status_code"] >= 400}
        assert failed == {"/api/v1/sessions/sess-a", "/api/v1/sessions/sess-b"}
    finally:
        await store.close()


async def test_event_without_url_path_still_records(tmp_path):
    # Not every emitter supplies it — `record_request` omits the key when the raw path is
    # unknown — so the column must be nullable rather than a required field.
    store = SQLiteUsageAuditStore(tmp_path / "usage.sqlite3")
    await store.initialize()
    try:
        await store.record_batch(
            [
                _http_event(
                    {
                        "method": "GET",
                        "route": "/api/v1/sessions",
                        "status": 200,
                        "duration_ms": 2.0,
                        "account_id": "acct-1",
                        "user_id": "user-1",
                    },
                    request_id="r-no-path",
                )
            ]
        )
        audit = await store.query_audit_logs(account_id="acct-1")

        assert audit["total"] == 1
        item = audit["items"][0]
        assert item["url_path"] is None
        assert item["route"] == "/api/v1/sessions"
    finally:
        await store.close()


async def test_success_rate_and_filters_are_unaffected(tmp_path):
    store = SQLiteUsageAuditStore(tmp_path / "usage.sqlite3")
    await store.initialize()
    try:
        await store.record_batch(
            [
                _session_request("sess-a", status=404, request_id="r1"),
                _session_request("sess-ok", status=200, request_id="r2"),
            ]
        )
        assert (await store.query_audit_logs(account_id="acct-1"))["success_rate"] == 0.5

        errors = await store.query_audit_logs(account_id="acct-1", statuses=["error"])
        assert errors["total"] == 1
        assert errors["items"][0]["url_path"] == "/api/v1/sessions/sess-a"

        by_request = await store.query_audit_logs(account_id="acct-1", request_id="r2")
        assert by_request["total"] == 1
        assert by_request["items"][0]["url_path"] == "/api/v1/sessions/sess-ok"
    finally:
        await store.close()
