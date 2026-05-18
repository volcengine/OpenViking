# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0
"""Projection from generic observability events into product usage/audit rows."""

from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Sequence

from openviking.observability.events import ObservabilityEvent

UNKNOWN_IDENTITY = "__unknown__"
AUDIT_EXCLUDED_ROUTES = frozenset(
    {
        "",
        "/metrics",
        "/health",
        "/ready",
        "/docs",
        "/docs/oauth2-redirect",
        "/redoc",
        "/openapi.json",
        "/favicon.ico",
        "/favicon.png",
        "/apple-touch-icon.png",
    }
)


@dataclass(slots=True)
class UsageAuditProjection:
    """Write-ready rows derived from a batch of observability events."""

    token_rows: dict[tuple, int] = field(default_factory=dict)
    retrieval_rows: dict[tuple, tuple[int, int]] = field(default_factory=dict)
    context_rows: dict[tuple, int] = field(default_factory=dict)
    agent_rows: dict[tuple, tuple[int, str]] = field(default_factory=dict)
    audit_rows: list[tuple] = field(default_factory=list)
    touched_audit_accounts: set[str] = field(default_factory=set)


def normalize_identity(value: Any, *, unknown: bool = False) -> str:
    """Normalize optional identity fields into stable store keys."""
    if value is None or value == "":
        return UNKNOWN_IDENTITY if unknown else ""
    return str(value)


def safe_int(value: Any, default: int = 0) -> int:
    """Best-effort integer conversion."""
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def safe_float(value: Any, default: float = 0.0) -> float:
    """Best-effort float conversion."""
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def project_events(
    events: Sequence[ObservabilityEvent],
    *,
    tz,
) -> UsageAuditProjection:
    """Project generic events into product usage/audit rows."""
    token_rows: defaultdict[tuple, int] = defaultdict(int)
    retrieval_rows: defaultdict[tuple, tuple[int, int]] = defaultdict(lambda: (0, 0))
    context_rows: defaultdict[tuple, int] = defaultdict(int)
    agent_rows: dict[tuple, tuple[int, str]] = {}
    audit_rows: list[tuple] = []
    touched_audit_accounts: set[str] = set()

    for event in events:
        account_id = normalize_identity(event.account_id, unknown=True)
        user_id = normalize_identity(event.user_id)
        agent_id = normalize_identity(event.agent_id)
        local_dt = _local_dt(event, tz)
        event_date = local_dt.date().isoformat()
        created_at = local_dt.isoformat()
        payload = event.payload

        if event.event_name == "vlm.call":
            _add_token_rows(
                token_rows,
                account_id=account_id,
                user_id=user_id,
                agent_id=agent_id,
                event_date=event_date,
                source="vlm",
                provider=payload.get("provider"),
                model_name=payload.get("model_name"),
                input_tokens=payload.get("prompt_tokens"),
                output_tokens=payload.get("completion_tokens"),
            )
            continue

        if event.event_name == "embedding.call":
            _add_token_rows(
                token_rows,
                account_id=account_id,
                user_id=user_id,
                agent_id=agent_id,
                event_date=event_date,
                source="embedding",
                provider=payload.get("provider"),
                model_name=payload.get("model_name"),
                input_tokens=payload.get("prompt_tokens"),
                output_tokens=0,
            )
            continue

        if event.event_name == "rerank.call":
            _add_token_rows(
                token_rows,
                account_id=account_id,
                user_id=user_id,
                agent_id=agent_id,
                event_date=event_date,
                source="rerank",
                provider=payload.get("provider"),
                model_name=payload.get("model_name"),
                input_tokens=payload.get("prompt_tokens"),
                output_tokens=payload.get("completion_tokens"),
            )
            continue

        if event.event_name == "http.request":
            _project_http_request(
                event,
                event_date=event_date,
                hour=int(local_dt.hour),
                created_at=created_at,
                retrieval_rows=retrieval_rows,
                context_rows=context_rows,
                audit_rows=audit_rows,
                touched_audit_accounts=touched_audit_accounts,
                agent_rows=agent_rows,
            )

    return UsageAuditProjection(
        token_rows=dict(token_rows),
        retrieval_rows=dict(retrieval_rows),
        context_rows=dict(context_rows),
        agent_rows=agent_rows,
        audit_rows=audit_rows,
        touched_audit_accounts=touched_audit_accounts,
    )


def _local_dt(event: ObservabilityEvent, tz) -> datetime:
    ts = event.timestamp
    if ts.tzinfo is None:
        ts = ts.replace(tzinfo=timezone.utc)
    return ts.astimezone(tz)


def _add_token_rows(
    rows: defaultdict[tuple, int],
    *,
    account_id: str,
    user_id: str,
    agent_id: str,
    event_date: str,
    source: str,
    provider: Any,
    model_name: Any,
    input_tokens: Any,
    output_tokens: Any,
) -> None:
    provider_key = normalize_identity(provider)
    model_key = normalize_identity(model_name)
    input_count = max(safe_int(input_tokens), 0)
    output_count = max(safe_int(output_tokens), 0)
    if input_count:
        key = (
            account_id,
            user_id,
            agent_id,
            event_date,
            source,
            "input",
            provider_key,
            model_key,
        )
        rows[key] += input_count
    if output_count:
        key = (
            account_id,
            user_id,
            agent_id,
            event_date,
            source,
            "output",
            provider_key,
            model_key,
        )
        rows[key] += output_count


def _project_http_request(
    event: ObservabilityEvent,
    *,
    event_date: str,
    hour: int,
    created_at: str,
    retrieval_rows: defaultdict[tuple, tuple[int, int]],
    context_rows: defaultdict[tuple, int],
    audit_rows: list[tuple],
    touched_audit_accounts: set[str],
    agent_rows: dict[tuple, tuple[int, str]],
) -> None:
    payload = event.payload
    route = str(payload.get("route") or "")
    method = str(payload.get("method") or "").upper()
    if should_skip_audit_route(route):
        return
    status_code = safe_int(payload.get("status"), 500)
    duration_ms = safe_float(payload.get("duration_ms"))
    if duration_ms <= 0:
        duration_ms = safe_float(payload.get("duration_seconds")) * 1000.0
    audit_account = normalize_identity(
        payload.get("account_id") or event.account_id,
        unknown=True,
    )
    audit_user = normalize_identity(payload.get("user_id") or event.user_id) or None
    audit_agent = normalize_identity(payload.get("agent_id") or event.agent_id) or None
    row_user = audit_user or ""
    row_agent = audit_agent or ""
    status = "success" if 200 <= status_code < 400 else "error"

    retrieval_operation = retrieval_operation_for_http(method, route)
    if retrieval_operation:
        key = (audit_account, row_user, row_agent, event_date, retrieval_operation, status)
        prev_count, prev_results = retrieval_rows[key]
        retrieval_rows[key] = (prev_count + 1, prev_results)

    context_operation = context_write_operation_for_http(method, route, status_code)
    if context_operation:
        key = (audit_account, row_user, row_agent, event_date, hour, context_operation)
        context_rows[key] += 1

    audit_rows.append(
        (
            payload.get("request_id") or event.request_id,
            audit_account,
            audit_user,
            audit_agent,
            str(payload.get("method") or ""),
            route,
            str(payload.get("api_type") or derive_api_type(route)),
            status_code,
            duration_ms,
            created_at,
        )
    )
    touched_audit_accounts.add(audit_account)
    if audit_agent:
        agent_key = (audit_account, audit_agent, event_date)
        prev_count, prev_seen = agent_rows.get(agent_key, (0, ""))
        agent_rows[agent_key] = (
            prev_count + 1,
            max(prev_seen, created_at) if prev_seen else created_at,
        )


def should_skip_audit_route(route: str) -> bool:
    """Return whether an HTTP route should be omitted from product audit."""
    return (
        route in AUDIT_EXCLUDED_ROUTES
        or route.startswith("/console/")
        or route.startswith("/api/v1/console/")
    )


def retrieval_operation_for_http(method: str, route: str) -> str | None:
    """Map successful public search APIs from `http.request` into dashboard counters."""
    if method != "POST":
        return None
    if route == "/api/v1/search/find":
        return "find"
    if route == "/api/v1/search/search":
        return "search"
    return None


def context_write_operation_for_http(method: str, route: str, status_code: int) -> str | None:
    """Map successful write APIs from `http.request` into context-write heatmap buckets."""
    if method != "POST" or not (200 <= status_code < 400):
        return None
    if route == "/api/v1/resources":
        return "add_resource"
    if route == "/api/v1/skills":
        return "add_skill"
    if route == "/api/v1/sessions/{session_id}/messages":
        return "session.add_message"
    if route == "/api/v1/sessions/{session_id}/commit":
        return "session.commit"
    return None


def derive_api_type(route: str) -> str:
    """Derive the stable product-facing API type from a route template."""
    if route == "/api/v1/search/find":
        return "search.find"
    if route == "/api/v1/search/search":
        return "search.search"
    prefix_map = {
        "/api/v1/resources": "resources",
        "/api/v1/skills": "skills",
        "/api/v1/sessions": "sessions",
        "/api/v1/fs": "filesystem",
        "/api/v1/content": "content",
        "/api/v1/admin": "admin",
        "/api/v1/tasks": "tasks",
    }
    for prefix, api_type in prefix_map.items():
        if route == prefix or route.startswith(prefix + "/"):
            return api_type
    parts = [part for part in route.split("/") if part]
    if len(parts) >= 3 and parts[0] == "api" and parts[1] == "v1":
        return parts[2]
    return "unknown"
