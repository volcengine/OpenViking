# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0
"""Projection of ``mcp.tool`` events into dashboard retrieval counters."""

from __future__ import annotations

from datetime import datetime, timezone

from openviking.observability.events import ObservabilityEvent
from openviking.observability.usage_audit.projection import project_events

TS = datetime(2026, 5, 12, 1, 2, 3, tzinfo=timezone.utc)


def _mcp_event(
    tool: str,
    *,
    status: str = "success",
    account_id: str | None = "acct-1",
    user_id: str | None = "user-1",
) -> ObservabilityEvent:
    payload: dict = {"tool": tool, "status": status}
    if account_id is not None:
        payload["account_id"] = account_id
    if user_id is not None:
        payload["user_id"] = user_id
    return ObservabilityEvent(
        event_name="mcp.tool",
        payload=payload,
        timestamp=TS,
        account_id=account_id,
        user_id=user_id,
    )


def test_mcp_find_and_search_count_as_retrievals():
    rows = project_events(
        [
            _mcp_event("find"),
            _mcp_event("search"),
            _mcp_event("find"),
        ]
    ).retrieval_rows
    assert rows[("acct-1", "user-1", "2026-05-12", 1, "find", "success")] == (2, 0)
    assert rows[("acct-1", "user-1", "2026-05-12", 1, "search", "success")] == (1, 0)


def test_mcp_tool_error_buckets_separately():
    rows = project_events(
        [
            _mcp_event("find"),
            _mcp_event("find", status="error"),
        ]
    ).retrieval_rows
    assert rows[("acct-1", "user-1", "2026-05-12", 1, "find", "success")] == (1, 0)
    assert rows[("acct-1", "user-1", "2026-05-12", 1, "find", "error")] == (1, 0)


def test_non_retrieval_mcp_tools_are_ignored():
    for tool in ("read", "multi_read", "list", "write", "glob", "add_resource"):
        rows = project_events([_mcp_event(tool)]).retrieval_rows
        assert rows == {}


def test_identity_falls_back_to_event_envelope():
    event = ObservabilityEvent(
        event_name="mcp.tool",
        payload={"tool": "search", "status": "success"},
        timestamp=TS,
        account_id="acct-2",
        user_id="user-2",
    )
    rows = project_events([event]).retrieval_rows
    assert rows[("acct-2", "user-2", "2026-05-12", 1, "search", "success")] == (1, 0)


def test_missing_identity_normalizes_to_unknown():
    event = ObservabilityEvent(
        event_name="mcp.tool",
        payload={"tool": "search", "status": "success"},
        timestamp=TS,
    )
    rows = project_events([event]).retrieval_rows
    assert rows[("__unknown__", "", "2026-05-12", 1, "search", "success")] == (1, 0)


def test_mcp_events_do_not_touch_audit_rows():
    projection = project_events([_mcp_event("find")])
    assert projection.audit_rows == []
    assert projection.touched_audit_accounts == set()


def test_missing_status_defaults_to_success():
    event = ObservabilityEvent(
        event_name="mcp.tool",
        payload={"tool": "find"},
        timestamp=TS,
        account_id="acct-1",
        user_id="user-1",
    )
    rows = project_events([event]).retrieval_rows
    assert rows[("acct-1", "user-1", "2026-05-12", 1, "find", "success")] == (1, 0)
