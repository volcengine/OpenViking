"""Opt-in principal-scoped observation ledger and snapshot queries.

This consumes existing facts; observed model events are not a provider bill or
proof that every physical RPC was instrumented. Only scalar allowlisted fields
are persisted. Cache/reasoning are subsets, never added to input+output.
"""

from __future__ import annotations

import base64
import hashlib
import json
import math
from collections import defaultdict
from datetime import datetime, timedelta, timezone
from typing import Any

from openviking_cli.exceptions import ConflictError, InvalidArgumentError

MODEL_EVENTS = {"vlm.call", "embedding.call", "rerank.call"}
SUPPORTED = MODEL_EVENTS | {"http.request", "task_terminal", "queue.processed", "workflow.stage"}
TOKEN_FIELDS = {
    "input_tokens": "prompt_tokens",
    "output_tokens": "completion_tokens",
    "cached_tokens": "prompt_cached_tokens",
    "reasoning_tokens": "completion_reasoning_tokens",
}
DDL = """
CREATE TABLE IF NOT EXISTS performance_meta (key TEXT PRIMARY KEY,value TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS performance_receipts (
 account_id TEXT NOT NULL,user_id TEXT NOT NULL,event_name TEXT NOT NULL,event_id TEXT NOT NULL,
 created_at TEXT NOT NULL,fingerprint TEXT NOT NULL,
 PRIMARY KEY(account_id,user_id,event_name,event_id)
);
CREATE INDEX IF NOT EXISTS performance_receipt_retention ON performance_receipts(account_id,created_at);
CREATE TABLE IF NOT EXISTS performance_events (
 seq INTEGER PRIMARY KEY AUTOINCREMENT,
 account_id TEXT NOT NULL,user_id TEXT NOT NULL,event_name TEXT NOT NULL,event_id TEXT NOT NULL,
 recorded_at TEXT NOT NULL,created_at TEXT NOT NULL,fingerprint TEXT NOT NULL,
 workflow_id TEXT NOT NULL,task_id TEXT NOT NULL,work_id TEXT NOT NULL,request_id TEXT NOT NULL,
 stage_id TEXT NOT NULL,parent_stage_id TEXT NOT NULL,stage TEXT NOT NULL,
 model TEXT NOT NULL,provider TEXT NOT NULL,status TEXT NOT NULL,
 attempt_id TEXT NOT NULL,observation_scope TEXT NOT NULL,outcome TEXT NOT NULL,
 usage_complete INTEGER,usage_known_fields TEXT NOT NULL,usage_provenance TEXT NOT NULL,
 started_at DOUBLE PRECISION,duration_seconds DOUBLE PRECISION,queue_seconds DOUBLE PRECISION,
 input_tokens BIGINT,output_tokens BIGINT,cached_tokens BIGINT,reasoning_tokens BIGINT,
 usage_quality TEXT NOT NULL,
 UNIQUE(account_id,user_id,event_name,event_id)
);
CREATE INDEX IF NOT EXISTS performance_scope_time ON performance_events(account_id,user_id,created_at,seq);
CREATE INDEX IF NOT EXISTS performance_ingestion ON performance_events(account_id,user_id,recorded_at);
CREATE INDEX IF NOT EXISTS performance_workflow ON performance_events(account_id,user_id,workflow_id,seq);
CREATE TABLE IF NOT EXISTS performance_collection (
 producer_id TEXT NOT NULL,account_id TEXT NOT NULL,user_id TEXT NOT NULL,
 started_at TEXT NOT NULL,updated_at TEXT NOT NULL,revision BIGINT NOT NULL,
 accepted BIGINT NOT NULL,persisted BIGINT NOT NULL,dropped BIGINT NOT NULL,
 failed BIGINT NOT NULL,uncertain BIGINT NOT NULL,pending BIGINT NOT NULL,clean INTEGER NOT NULL,
 PRIMARY KEY(producer_id,account_id,user_id)
);
CREATE TABLE IF NOT EXISTS performance_watermark (
 account_id TEXT PRIMARY KEY,latest_at TEXT NOT NULL
);
"""
COLUMNS = (
    "account_id",
    "user_id",
    "event_name",
    "event_id",
    "recorded_at",
    "created_at",
    "fingerprint",
    "workflow_id",
    "task_id",
    "work_id",
    "request_id",
    "stage_id",
    "parent_stage_id",
    "stage",
    "model",
    "provider",
    "status",
    "attempt_id",
    "observation_scope",
    "outcome",
    "usage_complete",
    "usage_known_fields",
    "usage_provenance",
    "started_at",
    "duration_seconds",
    "queue_seconds",
    *TOKEN_FIELDS,
    "usage_quality",
)


def initialize(conn, *, postgres=False):
    ddl = (
        DDL.replace("INTEGER PRIMARY KEY AUTOINCREMENT", "BIGSERIAL PRIMARY KEY")
        if postgres
        else DDL
    )
    for statement in ddl.split(";"):
        if statement.strip():
            conn.execute(statement)
    row = conn.execute("SELECT value FROM performance_meta WHERE key='version'").fetchone()
    if row is not None and row["value"] != "1":
        raise RuntimeError("Unsupported performance schema; migration required")
    conn.execute(
        "INSERT INTO performance_meta (key,value) VALUES ('version','1') ON CONFLICT(key) DO NOTHING"
    )


def write_collection(conn, rows):
    for row in rows:
        columns = list(row)
        updates = [key for key in columns if key not in {"producer_id", "account_id", "user_id"}]
        conn.execute(
            "INSERT INTO performance_collection ("
            + ",".join(columns)
            + ") VALUES ("
            + ",".join("?" for _ in columns)
            + ") ON CONFLICT(producer_id,account_id,user_id) DO UPDATE SET "
            + ",".join(key + "=excluded." + key for key in updates)
            + " WHERE excluded.revision>=performance_collection.revision",
            tuple(row[k] for k in columns),
        )


def number(value):
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    return max(0.0, float(value)) if math.isfinite(value) and value >= 0 else None


def tokens(value):
    return value if type(value) is int and 0 <= value <= 2**63 - 1 else None


def text(value, maximum=256):
    return str(value or "")[:maximum]


def normalized(event, now):
    p, name = event.payload, event.event_name
    if name not in SUPPORTED:
        return None
    if name == "http.request" and (
        str(p.get("route", "")).startswith("/api/v1/console")
        or p.get("route") in {"/health", "/ready", "/metrics"}
    ):
        return None
    if event.timestamp.tzinfo is None:
        return None
    row: dict[str, Any] = dict.fromkeys(COLUMNS, "")
    row.update(
        account_id=str(event.account_id or "__unknown__"),
        user_id=str(event.user_id or ""),
        event_name=name,
        event_id=event.event_id,
        recorded_at=now.isoformat(),
        created_at=event.timestamp.astimezone(timezone.utc).isoformat(),
    )
    for key in ("task_id", "work_id", "request_id", "stage_id", "parent_stage_id", "stage"):
        row[key] = text(p.get(key) or (event.request_id if key == "request_id" else ""))
    # IDs group observations only within an authenticated principal's ledger.
    row["workflow_id"] = row["task_id"] or row["request_id"] or event.event_id
    row["stage"] = row["stage"] or text(p.get("route") or p.get("task_type") or name)
    row["provider"], row["model"] = text(p.get("provider")), text(p.get("model_name"))
    row["status"] = text(p.get("status") or p.get("outcome") or "success", 64)
    if name == "http.request":
        status = str(p.get("status") or "")
        row["status"] = "success" if status.isdecimal() and 200 <= int(status) < 400 else "failed"
    elif name in MODEL_EVENTS and p.get("error_code", "OK") != "OK":
        row["status"] = "cancelled" if row["status"] == "cancelled" else "failed"
    for key in ("attempt_id", "observation_scope", "outcome"):
        row[key] = text(p.get(key))
    row["usage_complete"] = (
        int(p["usage_complete"]) if type(p.get("usage_complete")) is bool else None
    )
    known = p.get("usage_known_fields")
    known = (
        [key for key in TOKEN_FIELDS.values() if key in known]
        if isinstance(known, (list, tuple))
        else None
    )
    row["usage_known_fields"] = json.dumps(known)
    provenance = p.get("usage_provenance")
    allowed = {
        "provider_reported",
        "reported",
        "estimated",
        "character_estimate",
        "unknown",
        "embedding_no_output",
        "embedding_protocol",
        "provider_total_minus_input",
    }
    if isinstance(provenance, dict):
        provenance = {
            key: value
            for key, value in provenance.items()
            if key in TOKEN_FIELDS.values() and isinstance(value, str) and value in allowed
        }
    elif not isinstance(provenance, str) or provenance not in allowed:
        provenance = None
    row["usage_provenance"] = json.dumps(provenance)

    row["duration_seconds"] = number(p.get("duration_seconds", p.get("process_duration_seconds")))
    if row["duration_seconds"] is None and number(p.get("duration_ms")) is not None:
        row["duration_seconds"] = number(p["duration_ms"]) / 1000
    row["queue_seconds"] = number(p.get("queue_seconds"))
    row["started_at"] = number(p.get("started_at"))
    if name == "task_terminal":
        created, updated = number(p.get("created_at")), number(p.get("updated_at"))
        if created is not None and updated is not None and updated >= created:
            row["started_at"] = created / 1000
            row["duration_seconds"] = (updated - created) / 1000
    row["usage_quality"] = (
        p.get("usage_quality")
        if p.get("usage_quality") in {"actual", "estimated", "unknown"}
        else "unverified"
    )
    for target, source in TOKEN_FIELDS.items():
        # Preserve supplied zero, but never invent zero for an absent field.
        row[target] = (
            tokens(p.get(source))
            if name in MODEL_EVENTS and (known is None or source in known)
            else None
        )
    for subset, total in [("cached_tokens", "input_tokens"), ("reasoning_tokens", "output_tokens")]:
        if row[subset] is not None and row[total] is not None and row[subset] > row[total]:
            row[subset] = None
    # Never attribute a shared carrier to the timer's inherited first Task.
    if len(p.get("source_task_ids") or []) > 1 or p.get("allocations"):
        row["workflow_id"] = "batch:" + event.event_id
        row["task_id"] = row["work_id"] = row["stage_id"] = row["parent_stage_id"] = ""
    row["fingerprint"] = hashlib.sha256(
        json.dumps(
            {k: v for k, v in row.items() if k not in {"recorded_at", "fingerprint"}},
            sort_keys=True,
            allow_nan=False,
        ).encode()
    ).hexdigest()
    return row


def claim(conn, events, *, retention_days, now, acceptance_days=None):
    """Claim and project in the caller's existing account-serialized transaction."""
    accepted, clocks = [], {}
    for event in events:
        row = normalized(event, now)
        if row is None:
            # Keep legacy projection behavior for unrelated official event types.
            accepted.append(event)
            continue
        account = row["account_id"]
        if account not in clocks:
            prior = conn.execute(
                "SELECT latest_at FROM performance_watermark WHERE account_id=?", (account,)
            ).fetchone()
            clocks[account] = max(now, datetime.fromisoformat(prior["latest_at"])) if prior else now
        clock = clocks[account]
        cutoff = (clock - timedelta(days=acceptance_days)).isoformat() if acceptance_days else None
        # Once a receipt expires, its event can never be accepted again, including
        # after wall-clock rollback. Future outliers cannot advance the watermark.
        if (
            cutoff is not None and row["created_at"] < cutoff
        ) or event.timestamp > clock + timedelta(minutes=5):
            continue
        params = (account, row["user_id"], row["event_name"], row["event_id"])
        previous = conn.execute(
            "SELECT fingerprint FROM performance_receipts WHERE account_id=? AND user_id=? AND event_name=? AND event_id=?",
            params,
        ).fetchone()
        if previous:
            if previous["fingerprint"] != row["fingerprint"]:
                raise ConflictError("An observability event ID was reused with different facts")
            continue
        # A monotonic per-account logical ingestion clock avoids exposing a
        # global sequence and remains ordered through wall-clock rollback.
        clocks[account] = clock + timedelta(microseconds=1)
        row["recorded_at"] = clocks[account].isoformat()
        conn.execute(
            "INSERT INTO performance_receipts (account_id,user_id,event_name,event_id,created_at,fingerprint) VALUES (?,?,?,?,?,?)",
            (*params, row["created_at"], row["fingerprint"]),
        )
        if row["created_at"] >= (clock - timedelta(days=retention_days)).isoformat():
            conn.execute(
                "INSERT INTO performance_events ("
                + ",".join(COLUMNS)
                + ") VALUES ("
                + ",".join("?" for _ in COLUMNS)
                + ")",
                tuple(row[k] for k in COLUMNS),
            )
        accepted.append(event)
    for account, clock in clocks.items():
        conn.execute(
            "INSERT INTO performance_watermark (account_id,latest_at) VALUES (?,?) ON CONFLICT(account_id) DO UPDATE SET latest_at=excluded.latest_at",
            (account, clock.isoformat()),
        )
        conn.execute(
            "DELETE FROM performance_events WHERE account_id=? AND created_at<?",
            (account, (clock - timedelta(days=retention_days)).isoformat()),
        )
        if acceptance_days:
            conn.execute(
                "DELETE FROM performance_receipts WHERE account_id=? AND created_at<?",
                (account, (clock - timedelta(days=acceptance_days)).isoformat()),
            )
        conn.execute(
            "DELETE FROM performance_collection WHERE account_id=? AND updated_at<?",
            (account, (clock - timedelta(days=retention_days)).isoformat()),
        )
    return accepted


def metric_totals(rows):
    models = [r for r in rows if r["event_name"] in MODEL_EVENTS]
    return {
        key: {
            "known_tokens": sum(r[key] or 0 for r in models),
            "unknown_events": sum(r[key] is None for r in models),
        }
        for key in TOKEN_FIELDS
    }


def union_duration(intervals):
    end, total = None, 0.0
    for start, finish in sorted(intervals):
        if end is None or start > end:
            total += finish - start
        elif finish > end:
            total += finish - end
        end = max(finish, end or finish)
    return total


def summarize(rows):
    measured = [r for r in rows if r["duration_seconds"] is not None]
    intervals = [
        (r["started_at"], r["started_at"] + r["duration_seconds"])
        for r in measured
        if r["started_at"] is not None
    ]
    by_kind = {}

    def duration_group(row):
        return (
            "stage:" + row["stage"] if row["event_name"] == "workflow.stage" else row["event_name"]
        )

    for kind in sorted({duration_group(r) for r in rows}):
        members = [r for r in rows if duration_group(r) == kind]
        by_kind[kind] = {
            "observed_span_seconds": sum(r["duration_seconds"] or 0 for r in members),
            "unknown_duration_observations": sum(r["duration_seconds"] is None for r in members),
        }
    models = [r for r in rows if r["event_name"] in MODEL_EVENTS]
    covered = [
        r
        for r in models
        if r["usage_quality"] == "actual"
        and r["cached_tokens"] is not None
        and r["input_tokens"] is not None
    ]
    denominator = sum(r["input_tokens"] for r in covered)
    return {
        "observations": len(rows),
        "observed_model_events": len(models),
        "unverified_usage_events": sum(r["usage_quality"] == "unverified" for r in models),
        "unknown_usage_events": sum(r["usage_quality"] == "unknown" for r in models),
        "attempt_observations": sum(r["observation_scope"] == "provider_attempt" for r in models),
        "adapter_observations": sum(r["observation_scope"] == "adapter_call" for r in models),
        "failed_observations": sum(r["status"] in {"failed", "error"} for r in rows),
        "cancelled_observations": sum(r["status"] == "cancelled" for r in rows),
        "token_totals": metric_totals(rows),
        "reported_token_totals": {
            key: sum(r[key] or 0 for r in models if r["usage_quality"] == "actual")
            for key in TOKEN_FIELDS
        },
        "estimated_token_totals": {
            key: sum(r[key] or 0 for r in models if r["usage_quality"] == "estimated")
            for key in TOKEN_FIELDS
        },
        "unverified_token_totals": {
            key: sum(
                r[key] or 0 for r in models if r["usage_quality"] not in {"actual", "estimated"}
            )
            for key in TOKEN_FIELDS
        },
        "incomplete_usage_events": sum(r["usage_complete"] == 0 for r in models),
        "duration_by_kind": by_kind,
        "cache_hit_rate": sum(r["cached_tokens"] for r in covered) / denominator
        if denominator
        else None,
        "cache_covered_events": len(covered),
        "observed_interval_union_seconds": union_duration(intervals),
        "timed_interval_observations": len(intervals),
    }


def _cursor_encode(value):
    return (
        base64.urlsafe_b64encode(json.dumps(value, separators=(",", ":")).encode())
        .decode()
        .rstrip("=")
    )


def _cursor_decode(value):
    try:
        if not isinstance(value, str) or len(value) > 2048:
            raise ValueError
        data = json.loads(base64.urlsafe_b64decode(value + "=" * (-len(value) % 4)))
        if not isinstance(data, dict):
            raise ValueError
        return data
    except (ValueError, TypeError, UnicodeError):
        raise InvalidArgumentError("Invalid performance cursor") from None


def query(
    conn,
    *,
    account_id,
    user_id,
    start,
    end,
    view="summary",
    workflow_id=None,
    model=None,
    status=None,
    task_id=None,
    request_id=None,
    page_size=50,
    cursor=None,
    observed_before=None,
    retention_days=14,
):
    if type(page_size) is not int or not 1 <= page_size <= 100:
        raise InvalidArgumentError("Invalid performance page size")
    if view not in {"summary", "series", "workflows", "details"}:
        raise InvalidArgumentError("Invalid performance view")
    scope = "account_id=?" + (" AND user_id=?" if user_id is not None else "")
    scope_args = [account_id] + ([user_id] if user_id is not None else [])
    signature = hashlib.sha256(
        json.dumps(
            [
                account_id,
                user_id,
                start,
                end,
                view,
                workflow_id,
                model,
                status,
                task_id,
                request_id,
            ],
            separators=(",", ":"),
        ).encode()
    ).hexdigest()
    last = ""
    if cursor:
        decoded = _cursor_decode(cursor)
        if (
            decoded.get("scope") != signature
            or not isinstance(decoded.get("last"), str)
            or not isinstance(decoded.get("fence"), str)
        ):
            raise InvalidArgumentError("Performance cursor does not match the query scope")
        last, observed_before = decoded["last"], decoded["fence"]
    if observed_before is None:
        observed_before = (
            conn.execute(
                "SELECT MAX(recorded_at) AS latest FROM performance_events WHERE " + scope,
                scope_args,
            ).fetchone()["latest"]
            or datetime.now(timezone.utc).isoformat()
        )
    try:
        if not isinstance(observed_before, str) or len(observed_before) > 64:
            raise ValueError
        boundary = datetime.fromisoformat(observed_before)
        if boundary.tzinfo is None:
            raise ValueError
        observed_before = boundary.astimezone(timezone.utc).isoformat()
        if last and (len(last) > 64 or datetime.fromisoformat(last).tzinfo is None):
            raise ValueError
    except (ValueError, TypeError):
        raise InvalidArgumentError("Invalid observation boundary") from None
    predicate = scope + " AND created_at>=? AND created_at<? AND recorded_at<=?"
    params = [*scope_args, start, end, observed_before]
    for key, value in (
        ("workflow_id", workflow_id),
        ("model", model),
        ("status", status),
        ("task_id", task_id),
        ("request_id", request_id),
    ):
        if value is not None:
            predicate += " AND " + key + "=?"
            params.append(value)
    # Bound the materialized read, report truncation explicitly. The immutable
    # ingestion fence excludes late events even when their event time is old.
    rows = [
        dict(r)
        for r in conn.execute(
            "SELECT * FROM performance_events WHERE "
            + predicate
            + " ORDER BY recorded_at,seq LIMIT 20001",
            params,
        ).fetchall()
    ]
    truncated = len(rows) > 20000
    rows = rows[:20000]
    watermark = conn.execute(
        "SELECT latest_at FROM performance_watermark WHERE account_id=?", (account_id,)
    ).fetchone()
    clock = (
        max(datetime.now(timezone.utc), datetime.fromisoformat(watermark["latest_at"]))
        if watermark
        else datetime.now(timezone.utc)
    )
    retained_after = (clock - timedelta(days=retention_days)).isoformat()
    result = {
        "enabled": True,
        "observed_before": observed_before,
        "start": start,
        "end": end,
        "coverage": "truncated"
        if truncated
        else "retention_limited"
        if start < retained_after
        else "observed",
        "retained_after": retained_after,
        "billing_complete": False,
        "collection": {
            "mode": "best_effort",
            "counter_period": "producer_lifetime",
            "unobserved_producers_possible": True,
            "producers": [
                {k: v for k, v in dict(row).items() if k != "account_id"}
                for row in conn.execute(
                    "SELECT * FROM performance_collection WHERE " + scope, scope_args
                ).fetchall()
            ],
        },
        "summary": summarize(rows),
    }
    if view == "details":
        selected = [r for r in rows if r["recorded_at"] > last]
        result["items"] = [
            {k: v for k, v in r.items() if k not in {"fingerprint", "account_id", "seq"}}
            for r in selected[:page_size]
        ]
        for row in result["items"]:
            row["usage_known_fields"] = json.loads(row["usage_known_fields"])
            row["usage_provenance"] = json.loads(row["usage_provenance"])
            if row["usage_complete"] is not None:
                row["usage_complete"] = bool(row["usage_complete"])
        result["next_cursor"] = (
            _cursor_encode(
                {
                    "scope": signature,
                    "last": selected[page_size - 1]["recorded_at"],
                    "fence": observed_before,
                }
            )
            if len(selected) > page_size
            else None
        )
    elif view in {"series", "workflows"}:
        groups = defaultdict(list)
        for row in rows:
            group_key = (
                (row["created_at"][:13], "")
                if view == "series"
                else (row["user_id"], row["workflow_id"])
            )
            groups[group_key].append(row)
        items = []
        for group_key, members in sorted(groups.items()):
            item = summarize(members)
            item.update(
                {"hour": group_key[0] + ":00:00+00:00"}
                if view == "series"
                else {"user_id": group_key[0], "workflow_id": group_key[1]}
            )
            item["position"] = min(r["recorded_at"] for r in members)
            items.append(item)
        items.sort(key=lambda r: r["position"])
        selected = [r for r in items if r["position"] > last]
        result["items"] = selected[:page_size]
        result["next_cursor"] = (
            _cursor_encode(
                {
                    "scope": signature,
                    "last": selected[page_size - 1]["position"],
                    "fence": observed_before,
                }
            )
            if len(selected) > page_size
            else None
        )
    return result
