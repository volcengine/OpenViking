"""Offline feedback observability aggregation over persisted sessions."""

from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path
from typing import Any


FEEDBACK_STATS_SORT_FIELDS = (
    "responses_total",
    "tracked_responses_total",
    "responses_with_feedback",
    "feedback_total",
    "thumb_up_total",
    "thumb_down_total",
    "outcomes_total",
    "positive_feedback_total",
    "negative_feedback_total",
    "reasked_total",
    "resolved_total",
    "follow_up_without_feedback_total",
    "feedback_coverage",
    "thumbs_up_rate",
    "thumbs_down_rate",
    "positive_feedback_rate",
    "negative_feedback_rate",
    "reask_rate",
    "one_turn_resolution_rate",
)

_SUMMARY_METRICS = (
    ("Sessions Scanned", "sessions_scanned", "count"),
    ("Responses", "responses_total", "count"),
    ("Tracked Responses", "tracked_responses_total", "count"),
    ("Responses With Feedback", "responses_with_feedback", "count"),
    ("Feedback Events", "feedback_total", "count"),
    ("Thumbs Up", "thumb_up_total", "count"),
    ("Thumbs Down", "thumb_down_total", "count"),
    ("Outcome Records", "outcomes_total", "count"),
    ("Positive Feedback Outcomes", "positive_feedback_total", "count"),
    ("Negative Feedback Outcomes", "negative_feedback_total", "count"),
    ("Reasked Outcomes", "reasked_total", "count"),
    ("Resolved Outcomes", "resolved_total", "count"),
    ("Follow-up Without Feedback", "follow_up_without_feedback_total", "count"),
    ("Feedback Coverage", "feedback_coverage", "percent"),
    ("Thumbs Up Rate", "thumbs_up_rate", "percent"),
    ("Thumbs Down Rate", "thumbs_down_rate", "percent"),
    ("Positive Feedback Rate", "positive_feedback_rate", "percent"),
    ("Negative Feedback Rate", "negative_feedback_rate", "percent"),
    ("Reask Rate", "reask_rate", "percent"),
    ("One-turn Resolution Rate", "one_turn_resolution_rate", "percent"),
)


def compute_feedback_stats(
    bot_data_path: Path,
    *,
    channel: str | None = None,
    session_key: str | None = None,
    updated_since: str | None = None,
    updated_until: str | None = None,
    include_sessions: bool = False,
) -> dict[str, Any]:
    """Aggregate minimal feedback observability metrics from session JSONL files."""
    sessions_dir = bot_data_path / "sessions"
    filters = {
        "channel": channel,
        "session_key": session_key,
        "updated_since": _parse_datetime(updated_since),
        "updated_until": _parse_datetime(updated_until),
    }
    totals: dict[str, Any] = {
        "sessions_scanned": 0,
        "responses_total": 0,
        "tracked_responses_total": 0,
        "responses_with_feedback": 0,
        "feedback_total": 0,
        "thumb_up_total": 0,
        "thumb_down_total": 0,
        "outcomes_total": 0,
        "positive_feedback_total": 0,
        "negative_feedback_total": 0,
        "reasked_total": 0,
        "resolved_total": 0,
        "follow_up_without_feedback_total": 0,
        "channels": {},
        "sessions": [] if include_sessions else None,
    }

    if not sessions_dir.exists():
        return _finalize_feedback_stats(totals)

    for session_path in sorted(sessions_dir.glob("*.jsonl")):
        session_metrics = _read_session_metrics(session_path, filters)
        if session_metrics is None:
            continue
        totals["sessions_scanned"] += 1
        _merge_feedback_stats(totals, session_metrics)

    return _finalize_feedback_stats(totals)


def format_feedback_stats_table(stats: dict[str, Any]) -> dict[str, Any]:
    """Return table-friendly rows for CLI rendering."""
    summary = stats.get("summary", {})
    summary_rows = [
        (label, _format_metric_value(summary.get(key, 0), value_type))
        for label, key, value_type in _SUMMARY_METRICS
    ]

    channel_rows = []
    for channel_key, channel_stats in stats.get("channels", {}).items():
        channel_rows.append(
            (
                channel_key,
                _format_metric_value(channel_stats.get("responses_total", 0), "count"),
                _format_metric_value(channel_stats.get("feedback_total", 0), "count"),
                _format_metric_value(channel_stats.get("feedback_coverage", 0.0), "percent"),
                _format_metric_value(channel_stats.get("thumbs_up_rate", 0.0), "percent"),
                _format_metric_value(channel_stats.get("thumbs_down_rate", 0.0), "percent"),
                _format_metric_value(channel_stats.get("one_turn_resolution_rate", 0.0), "percent"),
            )
        )

    session_rows = []
    for session_stats in stats.get("sessions", []):
        session_rows.append(
            (
                session_stats.get("session_key", ""),
                session_stats.get("channel", "unknown"),
                str(session_stats.get("updated_at") or ""),
                _format_metric_value(session_stats.get("responses_total", 0), "count"),
                _format_metric_value(session_stats.get("feedback_total", 0), "count"),
                _format_metric_value(session_stats.get("negative_feedback_total", 0), "count"),
                _format_metric_value(session_stats.get("reasked_total", 0), "count"),
                _format_metric_value(session_stats.get("one_turn_resolution_rate", 0.0), "percent"),
            )
        )

    return {
        "summary_rows": summary_rows,
        "channel_rows": channel_rows,
        "session_rows": session_rows,
    }


def format_feedback_stats_markdown(stats: dict[str, Any]) -> dict[str, str]:
    """Return markdown-friendly sections for console rendering."""
    table_data = format_feedback_stats_table(stats)

    summary_lines = [f"- **{label}:** {value}" for label, value in table_data["summary_rows"]]
    summary_markdown = "\n".join(summary_lines) if summary_lines else "No feedback stats available."

    channels_markdown = _format_markdown_table(
        ["Channel", "Responses", "Feedback", "Coverage", "Thumbs Up", "Thumbs Down", "Resolution"],
        table_data["channel_rows"],
        empty_message="No channel stats available.",
    )
    sessions_markdown = _format_markdown_table(
        [
            "Session",
            "Channel",
            "Updated At",
            "Responses",
            "Feedback",
            "Negative",
            "Reasked",
            "Resolution",
        ],
        table_data["session_rows"],
        empty_message="No session stats available.",
    )

    return {
        "summary_markdown": summary_markdown,
        "channels_markdown": channels_markdown,
        "sessions_markdown": sessions_markdown,
    }


def build_feedback_stats_display(
    bot_data_path: Path,
    *,
    channel: str | None = None,
    session_key: str | None = None,
    updated_since: str | None = None,
    updated_until: str | None = None,
    sort_by: str = "responses_total",
    top_n: int | None = None,
    include_sessions: bool = True,
    session_limit: int | None = None,
) -> dict[str, str]:
    """Compute, select, and format feedback stats for display surfaces."""
    stats = compute_feedback_stats(
        bot_data_path,
        channel=channel,
        session_key=session_key,
        updated_since=updated_since,
        updated_until=updated_until,
        include_sessions=include_sessions,
    )
    stats = select_feedback_stats(
        stats,
        sort_by=sort_by,
        top_n=top_n,
        session_limit=session_limit if include_sessions else None,
    )
    return format_feedback_stats_markdown(stats)


def select_feedback_stats(
    stats: dict[str, Any],
    *,
    sort_by: str = "responses_total",
    top_n: int | None = None,
    session_limit: int | None = None,
) -> dict[str, Any]:
    """Sort and optionally trim channel and session breakdowns for display."""
    validate_feedback_stats_sort_by(sort_by)
    channels = stats.get("channels", {})
    sessions = stats.get("sessions", [])

    sorted_channels = sorted(
        channels.items(),
        key=lambda item: _sort_value(item[1], sort_by),
        reverse=True,
    )
    if top_n is not None:
        sorted_channels = sorted_channels[:top_n]

    sorted_sessions = sorted(
        sessions,
        key=lambda item: _sort_value(item, "updated_at"),
        reverse=True,
    )
    if session_limit is not None:
        sorted_sessions = sorted_sessions[:session_limit]

    return {
        "summary": stats.get("summary", {}),
        "channels": dict(sorted_channels),
        "sessions": sorted_sessions,
    }


def validate_feedback_stats_sort_by(sort_by: str) -> str:
    """Validate the requested sort field for channel-level stats."""
    if sort_by not in FEEDBACK_STATS_SORT_FIELDS:
        allowed = ", ".join(FEEDBACK_STATS_SORT_FIELDS)
        raise ValueError(f"sort_by must be one of: {allowed}")
    return sort_by


def _read_session_metrics(session_path: Path, filters: dict[str, Any]) -> dict[str, Any] | None:
    try:
        with open(session_path, encoding="utf-8") as f:
            first_line = f.readline().strip()
            remaining_lines = f.readlines()
    except OSError:
        return None

    if not first_line:
        return None

    try:
        first_record = json.loads(first_line)
    except json.JSONDecodeError:
        return None

    if first_record.get("_type") != "metadata":
        return None

    session_key = first_record.get("session_key", "")
    metadata = first_record.get("metadata", {})
    channel = _channel_from_session_key(session_key)
    updated_at = _parse_datetime(first_record.get("updated_at"))

    if filters["channel"] and channel != filters["channel"]:
        return None
    if filters["session_key"] and session_key != filters["session_key"]:
        return None
    if filters["updated_since"] and (updated_at is None or updated_at < filters["updated_since"]):
        return None
    if filters["updated_until"] and (updated_at is None or updated_at > filters["updated_until"]):
        return None

    feedback_events = metadata.get("feedback_events", [])
    response_outcomes = metadata.get("response_outcomes", {})
    response_ids = _collect_response_ids(remaining_lines)

    feedback_by_response = {
        event.get("response_id")
        for event in feedback_events
        if isinstance(event, dict) and event.get("response_id")
    }
    tracked_response_ids = feedback_by_response | {
        response_id
        for response_id, payload in response_outcomes.items()
        if isinstance(response_id, str) and response_id and isinstance(payload, dict)
    }

    metrics: dict[str, Any] = {
        "session_key": session_key,
        "responses_total": len(response_ids),
        "tracked_responses_total": len(tracked_response_ids),
        "responses_with_feedback": len(feedback_by_response),
        "feedback_total": 0,
        "thumb_up_total": 0,
        "thumb_down_total": 0,
        "outcomes_total": 0,
        "positive_feedback_total": 0,
        "negative_feedback_total": 0,
        "reasked_total": 0,
        "resolved_total": 0,
        "follow_up_without_feedback_total": 0,
        "channel": channel,
        "updated_at": first_record.get("updated_at"),
    }

    for event in feedback_events:
        if not isinstance(event, dict):
            continue
        metrics["feedback_total"] += 1
        feedback_type = event.get("feedback_type")
        if feedback_type == "thumb_up":
            metrics["thumb_up_total"] += 1
        elif feedback_type == "thumb_down":
            metrics["thumb_down_total"] += 1

    for payload in response_outcomes.values():
        if not isinstance(payload, dict):
            continue
        metrics["outcomes_total"] += 1
        outcome_label = payload.get("outcome_label")
        if outcome_label == "positive_feedback":
            metrics["positive_feedback_total"] += 1
        elif outcome_label == "negative_feedback":
            metrics["negative_feedback_total"] += 1
        elif outcome_label == "reasked":
            metrics["reasked_total"] += 1
        elif outcome_label == "resolved":
            metrics["resolved_total"] += 1
        elif outcome_label == "follow_up_without_feedback":
            metrics["follow_up_without_feedback_total"] += 1

    return _finalize_session_metrics(metrics)


def _finalize_session_metrics(metrics: dict[str, Any]) -> dict[str, Any]:
    tracked_responses_total = metrics["tracked_responses_total"]
    metrics["feedback_coverage"] = _safe_ratio(
        metrics["responses_with_feedback"], tracked_responses_total
    )
    metrics["thumbs_up_rate"] = _safe_ratio(metrics["thumb_up_total"], metrics["feedback_total"])
    metrics["thumbs_down_rate"] = _safe_ratio(
        metrics["thumb_down_total"], metrics["feedback_total"]
    )
    metrics["positive_feedback_rate"] = _safe_ratio(
        metrics["positive_feedback_total"], tracked_responses_total
    )
    metrics["negative_feedback_rate"] = _safe_ratio(
        metrics["negative_feedback_total"], tracked_responses_total
    )
    metrics["reask_rate"] = _safe_ratio(metrics["reasked_total"], tracked_responses_total)
    metrics["one_turn_resolution_rate"] = _safe_ratio(
        metrics["resolved_total"] + metrics["positive_feedback_total"],
        tracked_responses_total,
    )

    return metrics


def _collect_response_ids(lines: list[str]) -> set[str]:
    response_ids: set[str] = set()
    for line in lines:
        line = line.strip()
        if not line:
            continue
        try:
            record = json.loads(line)
        except json.JSONDecodeError:
            continue
        if record.get("role") != "assistant":
            continue
        response_id = record.get("response_id")
        if isinstance(response_id, str) and response_id:
            response_ids.add(response_id)
    return response_ids


def _merge_feedback_stats(totals: dict[str, Any], session_metrics: dict[str, Any]) -> None:
    channel = session_metrics["channel"]
    channel_totals = totals["channels"].setdefault(
        channel,
        {
            "responses_total": 0,
            "tracked_responses_total": 0,
            "responses_with_feedback": 0,
            "feedback_total": 0,
            "thumb_up_total": 0,
            "thumb_down_total": 0,
            "outcomes_total": 0,
            "positive_feedback_total": 0,
            "negative_feedback_total": 0,
            "reasked_total": 0,
            "resolved_total": 0,
            "follow_up_without_feedback_total": 0,
        },
    )

    for key, value in session_metrics.items():
        if key in {
            "channel",
            "session_key",
            "updated_at",
            "feedback_coverage",
            "thumbs_up_rate",
            "thumbs_down_rate",
            "positive_feedback_rate",
            "negative_feedback_rate",
            "reask_rate",
            "one_turn_resolution_rate",
        }:
            continue
        totals[key] += value
        channel_totals[key] += value

    if totals["sessions"] is None:
        return

    totals["sessions"].append(
        {
            "session_key": session_metrics["session_key"],
            "channel": channel,
            "updated_at": session_metrics["updated_at"],
            "responses_total": session_metrics["responses_total"],
            "tracked_responses_total": session_metrics["tracked_responses_total"],
            "responses_with_feedback": session_metrics["responses_with_feedback"],
            "feedback_total": session_metrics["feedback_total"],
            "thumb_up_total": session_metrics["thumb_up_total"],
            "thumb_down_total": session_metrics["thumb_down_total"],
            "outcomes_total": session_metrics["outcomes_total"],
            "positive_feedback_total": session_metrics["positive_feedback_total"],
            "negative_feedback_total": session_metrics["negative_feedback_total"],
            "reasked_total": session_metrics["reasked_total"],
            "resolved_total": session_metrics["resolved_total"],
            "follow_up_without_feedback_total": session_metrics["follow_up_without_feedback_total"],
            "feedback_coverage": session_metrics["feedback_coverage"],
            "thumbs_up_rate": session_metrics["thumbs_up_rate"],
            "thumbs_down_rate": session_metrics["thumbs_down_rate"],
            "positive_feedback_rate": session_metrics["positive_feedback_rate"],
            "negative_feedback_rate": session_metrics["negative_feedback_rate"],
            "reask_rate": session_metrics["reask_rate"],
            "one_turn_resolution_rate": session_metrics["one_turn_resolution_rate"],
        }
    )


def _finalize_feedback_stats(totals: dict[str, Any]) -> dict[str, Any]:
    summary = {
        "sessions_scanned": totals["sessions_scanned"],
        "responses_total": totals["responses_total"],
        "tracked_responses_total": totals["tracked_responses_total"],
        "responses_with_feedback": totals["responses_with_feedback"],
        "feedback_total": totals["feedback_total"],
        "thumb_up_total": totals["thumb_up_total"],
        "thumb_down_total": totals["thumb_down_total"],
        "outcomes_total": totals["outcomes_total"],
        "positive_feedback_total": totals["positive_feedback_total"],
        "negative_feedback_total": totals["negative_feedback_total"],
        "reasked_total": totals["reasked_total"],
        "resolved_total": totals["resolved_total"],
        "follow_up_without_feedback_total": totals["follow_up_without_feedback_total"],
        "feedback_coverage": _safe_ratio(
            totals["responses_with_feedback"], totals["tracked_responses_total"]
        ),
        "thumbs_up_rate": _safe_ratio(totals["thumb_up_total"], totals["feedback_total"]),
        "thumbs_down_rate": _safe_ratio(totals["thumb_down_total"], totals["feedback_total"]),
        "positive_feedback_rate": _safe_ratio(
            totals["positive_feedback_total"], totals["tracked_responses_total"]
        ),
        "negative_feedback_rate": _safe_ratio(
            totals["negative_feedback_total"], totals["tracked_responses_total"]
        ),
        "reask_rate": _safe_ratio(totals["reasked_total"], totals["tracked_responses_total"]),
        "one_turn_resolution_rate": _safe_ratio(
            totals["resolved_total"] + totals["positive_feedback_total"],
            totals["tracked_responses_total"],
        ),
    }

    channels = {
        channel: {
            **channel_totals,
            "feedback_coverage": _safe_ratio(
                channel_totals["responses_with_feedback"], channel_totals["tracked_responses_total"]
            ),
            "thumbs_up_rate": _safe_ratio(
                channel_totals["thumb_up_total"], channel_totals["feedback_total"]
            ),
            "thumbs_down_rate": _safe_ratio(
                channel_totals["thumb_down_total"], channel_totals["feedback_total"]
            ),
            "positive_feedback_rate": _safe_ratio(
                channel_totals["positive_feedback_total"], channel_totals["tracked_responses_total"]
            ),
            "negative_feedback_rate": _safe_ratio(
                channel_totals["negative_feedback_total"], channel_totals["tracked_responses_total"]
            ),
            "reask_rate": _safe_ratio(
                channel_totals["reasked_total"], channel_totals["tracked_responses_total"]
            ),
            "one_turn_resolution_rate": _safe_ratio(
                channel_totals["resolved_total"] + channel_totals["positive_feedback_total"],
                channel_totals["tracked_responses_total"],
            ),
        }
        for channel, channel_totals in sorted(totals["channels"].items())
    }

    return {
        "summary": summary,
        "channels": channels,
        "sessions": sorted(totals["sessions"] or [], key=lambda item: item["session_key"]),
    }


def _channel_from_session_key(session_key: str) -> str:
    if not session_key or "__" not in session_key:
        return "unknown"
    parts = session_key.split("__")
    if len(parts) < 2:
        return "unknown"
    return f"{parts[0]}__{parts[1]}"


def _safe_ratio(numerator: int, denominator: int) -> float:
    if denominator <= 0:
        return 0.0
    return round(numerator / denominator, 4)


def _parse_datetime(value: str | None) -> datetime | None:
    if not value:
        return None
    normalized = value.replace("Z", "+00:00")
    return datetime.fromisoformat(normalized)


def _sort_value(payload: dict[str, Any], key: str) -> Any:
    value = payload.get(key)
    if key == "updated_at":
        parsed = _parse_datetime(value)
        return parsed or datetime.min
    if isinstance(value, (int, float)):
        return value
    return value or 0


def _format_metric_value(value: Any, value_type: str) -> str:
    if value_type == "percent":
        return f"{float(value) * 100:.1f}%"
    return str(value)


def _format_markdown_table(
    headers: list[str], rows: list[tuple[Any, ...]], *, empty_message: str
) -> str:
    if not rows:
        return empty_message

    header_row = "| " + " | ".join(headers) + " |"
    separator_row = "| " + " | ".join("---" for _ in headers) + " |"
    data_rows = ["| " + " | ".join(str(cell) for cell in row) + " |" for row in rows]
    return "\n".join([header_row, separator_row, *data_rows])
