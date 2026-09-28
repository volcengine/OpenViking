import json

from vikingbot.observability.feedback_stats import (
    build_feedback_stats_display,
    compute_feedback_stats,
    select_feedback_stats,
)


def _write_session(bot_dir, key, responses, *, feedback, outcomes, **timestamps):
    directory = bot_dir / "sessions"
    directory.mkdir(parents=True, exist_ok=True)
    records = [
        {
            "_type": "metadata",
            "session_key": key,
            **timestamps,
            "metadata": {
                "feedback_events": feedback,
                "response_outcomes": {
                    response_id: {"outcome_label": label} for response_id, label in outcomes.items()
                },
            },
        }
    ]
    records.extend(
        {
            "role": "assistant",
            "content": "answer",
            "response_id": response_id,
            "timestamp": timestamp,
        }
        for response_id, timestamp in responses
    )
    (directory / f"{key}.jsonl").write_text(
        "\n".join(json.dumps(record) for record in records) + "\n",
        encoding="utf-8",
    )


def test_compute_feedback_stats_aggregates_minimal_metrics(temp_dir):
    _write_session(
        temp_dir / "bot",
        "cli__default__session-1",
        [("resp-1", "2026-05-01T10:00:00"), ("resp-2", "2026-05-01T10:01:00")],
        feedback=[{"response_id": "resp-1", "feedback_type": "thumb_up"}],
        outcomes={"resp-1": "positive_feedback", "resp-2": "reasked"},
    )

    _write_session(
        temp_dir / "bot",
        "bot_api__demo__session-2",
        [
            ("resp-3", "2026-05-03T10:00:00"),
            ("resp-4", "2026-05-03T10:01:00"),
            ("resp-5", "2026-05-03T10:02:00"),
        ],
        feedback=[
            {"response_id": "resp-3", "feedback_type": "thumb_down"},
            {"response_id": "resp-3", "feedback_type": "thumb_down"},
        ],
        outcomes={
            "resp-3": "negative_feedback",
            "resp-4": "resolved",
            "resp-5": "follow_up_without_feedback",
        },
    )

    stats = compute_feedback_stats(temp_dir / "bot")

    assert stats["summary"]["sessions_scanned"] == 2
    assert stats["summary"]["responses_total"] == 5
    assert stats["summary"]["tracked_responses_total"] == 5
    assert stats["summary"]["responses_with_feedback"] == 2
    assert stats["summary"]["feedback_total"] == 3
    assert stats["summary"]["thumb_up_total"] == 1
    assert stats["summary"]["thumb_down_total"] == 2
    assert stats["summary"]["positive_feedback_total"] == 1
    assert stats["summary"]["negative_feedback_total"] == 1
    assert stats["summary"]["reasked_total"] == 1
    assert stats["summary"]["resolved_total"] == 1
    assert stats["summary"]["follow_up_without_feedback_total"] == 1
    assert stats["summary"]["feedback_coverage"] == 0.4
    assert stats["summary"]["thumbs_up_rate"] == 0.3333
    assert stats["summary"]["thumbs_down_rate"] == 0.6667
    assert stats["summary"]["positive_feedback_rate"] == 0.2
    assert stats["summary"]["negative_feedback_rate"] == 0.2
    assert stats["summary"]["reask_rate"] == 0.2
    assert stats["summary"]["one_turn_resolution_rate"] == 0.4

    assert stats["channels"]["cli__default"]["responses_total"] == 2
    assert stats["channels"]["cli__default"]["thumbs_up_rate"] == 1.0
    assert stats["channels"]["bot_api__demo"]["responses_total"] == 3
    assert stats["channels"]["bot_api__demo"]["thumbs_down_rate"] == 1.0


def test_compute_feedback_stats_ignores_missing_sessions_dir(temp_dir):
    stats = compute_feedback_stats(temp_dir / "bot")

    assert stats["summary"]["sessions_scanned"] == 0
    assert stats["summary"]["responses_total"] == 0
    assert stats["summary"]["feedback_coverage"] == 0.0
    assert stats["channels"] == {}
    assert stats["sessions"] == []


def test_compute_feedback_stats_supports_filters(temp_dir):
    _write_session(
        temp_dir / "bot",
        "cli__default__session-1",
        [("resp-1", "2026-05-01T10:00:00")],
        feedback=[{"response_id": "resp-1", "feedback_type": "thumb_up"}],
        outcomes={"resp-1": "positive_feedback"},
        created_at="2026-05-01T10:00:00",
        updated_at="2026-05-01T10:00:00",
    )
    _write_session(
        temp_dir / "bot",
        "bot_api__demo__session-2",
        [("resp-2", "2026-05-03T10:00:00")],
        feedback=[{"response_id": "resp-2", "feedback_type": "thumb_down"}],
        outcomes={"resp-2": "negative_feedback"},
        created_at="2026-05-03T10:00:00",
        updated_at="2026-05-03T10:00:00",
    )

    channel_stats = compute_feedback_stats(temp_dir / "bot", channel="cli__default")
    assert channel_stats["summary"]["sessions_scanned"] == 1
    assert channel_stats["summary"]["thumb_up_total"] == 1
    assert list(channel_stats["channels"]) == ["cli__default"]

    session_stats = compute_feedback_stats(temp_dir / "bot", session_key="bot_api__demo__session-2")
    assert session_stats["summary"]["sessions_scanned"] == 1
    assert session_stats["summary"]["thumb_down_total"] == 1
    assert list(session_stats["channels"].keys()) == ["bot_api__demo"]

    time_filtered_stats = compute_feedback_stats(
        temp_dir / "bot",
        updated_since="2026-05-02T00:00:00",
        updated_until="2026-05-04T00:00:00",
    )
    assert time_filtered_stats["summary"]["sessions_scanned"] == 1
    assert time_filtered_stats["summary"]["responses_total"] == 1
    assert list(time_filtered_stats["channels"].keys()) == ["bot_api__demo"]


def test_select_feedback_stats_sorts_and_limits_channels_and_sessions():
    stats = {
        "summary": {"sessions_scanned": 3},
        "channels": {
            "cli__default": {"responses_total": 2, "negative_feedback_total": 0},
            "bot_api__demo": {"responses_total": 5, "negative_feedback_total": 2},
            "email__alerts": {"responses_total": 3, "negative_feedback_total": 1},
        },
        "sessions": [
            {"session_key": "s1", "updated_at": "2026-05-01T10:00:00"},
            {"session_key": "s2", "updated_at": "2026-05-03T10:00:00"},
            {"session_key": "s3", "updated_at": "2026-05-02T10:00:00"},
        ],
    }

    selected = select_feedback_stats(
        stats,
        sort_by="negative_feedback_total",
        top_n=2,
        session_limit=2,
    )

    assert list(selected["channels"].keys()) == ["bot_api__demo", "email__alerts"]
    assert [session["session_key"] for session in selected["sessions"]] == ["s2", "s3"]


def test_build_feedback_stats_display_applies_filters_and_sorting(temp_dir):
    _write_session(
        temp_dir / "bot",
        "cli__default__session-1",
        [("resp-1", "2026-05-01T10:00:00"), ("resp-2", "2026-05-01T10:01:00")],
        feedback=[{"response_id": "resp-1", "feedback_type": "thumb_up"}],
        outcomes={"resp-1": "positive_feedback", "resp-2": "resolved"},
        updated_at="2026-05-01T10:00:00",
    )
    _write_session(
        temp_dir / "bot",
        "bot_api__demo__session-2",
        [("resp-2", "2026-05-03T10:00:00")],
        feedback=[
            {"response_id": "resp-2", "feedback_type": "thumb_down"},
            {"response_id": "resp-2", "feedback_type": "thumb_down"},
        ],
        outcomes={"resp-2": "negative_feedback"},
        updated_at="2026-05-03T10:00:00",
    )

    display = build_feedback_stats_display(
        temp_dir / "bot",
        updated_since="2026-05-02T00:00:00",
        sort_by="feedback_total",
        top_n=1,
        include_sessions=True,
        session_limit=1,
    )

    assert "**Sessions Scanned:** 1" in display["summary_markdown"]
    assert "bot_api__demo" in display["channels_markdown"]
    assert "cli__default" not in display["channels_markdown"]
    assert "bot_api__demo__session-2" in display["sessions_markdown"]
    assert "cli__default__session-1" not in display["sessions_markdown"]


def test_compute_feedback_stats_counts_rating_feedback_via_outcomes(temp_dir):
    _write_session(
        temp_dir / "bot",
        "cli__default__session-1",
        [("resp-1", "2026-05-01T10:00:00"), ("resp-2", "2026-05-01T10:01:00")],
        feedback=[{"response_id": "resp-1", "feedback_type": "rating", "feedback_score": 1}],
        outcomes={"resp-1": "positive_feedback", "resp-2": "resolved"},
        updated_at="2026-05-01T10:00:00",
    )

    stats = compute_feedback_stats(temp_dir / "bot", include_sessions=True)

    assert stats["summary"]["responses_total"] == 2
    assert stats["summary"]["tracked_responses_total"] == 2
    assert stats["summary"]["responses_with_feedback"] == 1
    assert stats["summary"]["feedback_total"] == 1
    assert stats["summary"]["thumb_up_total"] == 0
    assert stats["summary"]["thumb_down_total"] == 0
    assert stats["summary"]["feedback_coverage"] == 0.5
    assert stats["summary"]["positive_feedback_total"] == 1
    assert stats["summary"]["positive_feedback_rate"] == 0.5
    assert stats["summary"]["one_turn_resolution_rate"] == 1.0
    assert stats["channels"]["cli__default"]["feedback_coverage"] == 0.5
    assert stats["sessions"][0]["positive_feedback_rate"] == 0.5


def test_compute_feedback_stats_uses_tracked_responses_as_rate_denominator(temp_dir):
    _write_session(
        temp_dir / "bot",
        "cli__default__session-1",
        [
            ("resp-1", "2026-05-01T10:00:00"),
            ("resp-legacy-2", "2026-05-01T10:01:00"),
            ("resp-legacy-3", "2026-05-01T10:02:00"),
        ],
        feedback=[{"response_id": "resp-1", "feedback_type": "thumb_up"}],
        outcomes={"resp-1": "positive_feedback", "resp-2": "resolved"},
        updated_at="2026-05-01T10:00:00",
    )

    stats = compute_feedback_stats(temp_dir / "bot", include_sessions=True)

    assert stats["summary"]["responses_total"] == 3
    assert stats["summary"]["tracked_responses_total"] == 2
    assert stats["summary"]["responses_with_feedback"] == 1
    assert stats["summary"]["feedback_coverage"] == 0.5
    assert stats["summary"]["positive_feedback_rate"] == 0.5
    assert stats["summary"]["one_turn_resolution_rate"] == 1.0
    assert stats["channels"]["cli__default"]["responses_total"] == 3
    assert stats["channels"]["cli__default"]["tracked_responses_total"] == 2
    assert stats["sessions"][0]["responses_total"] == 3
    assert stats["sessions"][0]["tracked_responses_total"] == 2
