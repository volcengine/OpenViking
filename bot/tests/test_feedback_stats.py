import json

import pytest

from vikingbot.observability.feedback_stats import (
    FEEDBACK_STATS_SORT_FIELDS,
    build_feedback_stats_display,
    compute_feedback_stats,
    format_feedback_stats_markdown,
    format_feedback_stats_table,
    select_feedback_stats,
    validate_feedback_stats_sort_by,
)


def test_compute_feedback_stats_aggregates_minimal_metrics(temp_dir):
    sessions_dir = temp_dir / "bot" / "sessions"
    sessions_dir.mkdir(parents=True)

    (sessions_dir / "cli__default__session-1.jsonl").write_text(
        "\n".join(
            [
                json.dumps(
                    {
                        "_type": "metadata",
                        "session_key": "cli__default__session-1",
                        "metadata": {
                            "feedback_events": [
                                {"response_id": "resp-1", "feedback_type": "thumb_up"}
                            ],
                            "response_outcomes": {
                                "resp-1": {"outcome_label": "positive_feedback"},
                                "resp-2": {"outcome_label": "reasked"},
                            },
                        },
                    }
                ),
                json.dumps(
                    {
                        "role": "assistant",
                        "content": "answer 1",
                        "response_id": "resp-1",
                        "timestamp": "2026-05-01T10:00:00",
                    }
                ),
                json.dumps(
                    {
                        "role": "assistant",
                        "content": "answer 2",
                        "response_id": "resp-2",
                        "timestamp": "2026-05-01T10:01:00",
                    }
                ),
            ]
        ),
        encoding="utf-8",
    )

    (sessions_dir / "bot_api__demo__session-2.jsonl").write_text(
        "\n".join(
            [
                json.dumps(
                    {
                        "_type": "metadata",
                        "session_key": "bot_api__demo__session-2",
                        "metadata": {
                            "feedback_events": [
                                {"response_id": "resp-3", "feedback_type": "thumb_down"},
                                {"response_id": "resp-3", "feedback_type": "thumb_down"},
                            ],
                            "response_outcomes": {
                                "resp-3": {"outcome_label": "negative_feedback"},
                                "resp-4": {"outcome_label": "resolved"},
                                "resp-5": {"outcome_label": "follow_up_without_feedback"},
                            },
                        },
                    }
                ),
                json.dumps(
                    {
                        "role": "assistant",
                        "content": "answer 3",
                        "response_id": "resp-3",
                        "timestamp": "2026-05-03T10:00:00",
                    }
                ),
                json.dumps(
                    {
                        "role": "assistant",
                        "content": "answer 4",
                        "response_id": "resp-4",
                        "timestamp": "2026-05-03T10:01:00",
                    }
                ),
                json.dumps(
                    {
                        "role": "assistant",
                        "content": "answer 5",
                        "response_id": "resp-5",
                        "timestamp": "2026-05-03T10:02:00",
                    }
                ),
            ]
        ),
        encoding="utf-8",
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
    sessions_dir = temp_dir / "bot" / "sessions"
    sessions_dir.mkdir(parents=True)

    (sessions_dir / "cli__default__session-1.jsonl").write_text(
        "\n".join(
            [
                json.dumps(
                    {
                        "_type": "metadata",
                        "session_key": "cli__default__session-1",
                        "created_at": "2026-05-01T10:00:00",
                        "updated_at": "2026-05-01T10:00:00",
                        "metadata": {
                            "feedback_events": [
                                {"response_id": "resp-1", "feedback_type": "thumb_up"}
                            ],
                            "response_outcomes": {
                                "resp-1": {"outcome_label": "positive_feedback"}
                            },
                        },
                    }
                ),
                json.dumps(
                    {
                        "role": "assistant",
                        "content": "answer 1",
                        "response_id": "resp-1",
                        "timestamp": "2026-05-01T10:00:00",
                    }
                ),
            ]
        )
        + "\n",
        encoding="utf-8",
    )
    (sessions_dir / "bot_api__demo__session-2.jsonl").write_text(
        "\n".join(
            [
                json.dumps(
                    {
                        "_type": "metadata",
                        "session_key": "bot_api__demo__session-2",
                        "created_at": "2026-05-03T10:00:00",
                        "updated_at": "2026-05-03T10:00:00",
                        "metadata": {
                            "feedback_events": [
                                {"response_id": "resp-2", "feedback_type": "thumb_down"}
                            ],
                            "response_outcomes": {
                                "resp-2": {"outcome_label": "negative_feedback"}
                            },
                        },
                    }
                ),
                json.dumps(
                    {
                        "role": "assistant",
                        "content": "answer 2",
                        "response_id": "resp-2",
                        "timestamp": "2026-05-03T10:00:00",
                    }
                ),
            ]
        )
        + "\n",
        encoding="utf-8",
    )

    channel_stats = compute_feedback_stats(temp_dir / "bot", channel="cli__default")
    assert channel_stats["summary"]["sessions_scanned"] == 1
    assert channel_stats["summary"]["thumb_up_total"] == 1
    assert channel_stats["channels"] == {"cli__default": channel_stats["channels"]["cli__default"]}

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


def test_compute_feedback_stats_includes_session_breakdown_when_requested(temp_dir):
    sessions_dir = temp_dir / "bot" / "sessions"
    sessions_dir.mkdir(parents=True)

    (sessions_dir / "cli__default__session-1.jsonl").write_text(
        "\n".join(
            [
                json.dumps(
                    {
                        "_type": "metadata",
                        "session_key": "cli__default__session-1",
                        "updated_at": "2026-05-01T10:00:00",
                        "metadata": {
                            "feedback_events": [
                                {"response_id": "resp-1", "feedback_type": "thumb_up"}
                            ],
                            "response_outcomes": {
                                "resp-1": {"outcome_label": "positive_feedback"},
                                "resp-2": {"outcome_label": "resolved"},
                            },
                        },
                    }
                ),
                json.dumps(
                    {
                        "role": "assistant",
                        "content": "answer 1",
                        "response_id": "resp-1",
                        "timestamp": "2026-05-01T10:00:00",
                    }
                ),
                json.dumps(
                    {
                        "role": "assistant",
                        "content": "answer 2",
                        "response_id": "resp-2",
                        "timestamp": "2026-05-01T10:01:00",
                    }
                ),
            ]
        )
        + "\n",
        encoding="utf-8",
    )

    stats = compute_feedback_stats(temp_dir / "bot", include_sessions=True)

    assert len(stats["sessions"]) == 1
    assert stats["sessions"][0]["session_key"] == "cli__default__session-1"
    assert stats["sessions"][0]["feedback_total"] == 1
    assert stats["sessions"][0]["one_turn_resolution_rate"] == 1.0


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


def test_select_feedback_stats_rejects_unknown_sort_field():
    with pytest.raises(ValueError, match="sort_by must be one of"):
        select_feedback_stats({"summary": {}, "channels": {}, "sessions": []}, sort_by="bad_metric")


def test_validate_feedback_stats_sort_by_accepts_known_field():
    assert validate_feedback_stats_sort_by("responses_total") == "responses_total"
    assert "negative_feedback_rate" in FEEDBACK_STATS_SORT_FIELDS


def test_format_feedback_stats_table_returns_summary_and_channel_rows():
    stats = {
        "summary": {
            "sessions_scanned": 1,
            "responses_total": 2,
            "responses_with_feedback": 1,
            "feedback_total": 1,
            "thumb_up_total": 1,
            "thumb_down_total": 0,
            "outcomes_total": 2,
            "positive_feedback_total": 1,
            "negative_feedback_total": 0,
            "reasked_total": 0,
            "resolved_total": 1,
            "follow_up_without_feedback_total": 0,
            "feedback_coverage": 0.5,
            "thumbs_up_rate": 1.0,
            "thumbs_down_rate": 0.0,
            "positive_feedback_rate": 0.5,
            "negative_feedback_rate": 0.0,
            "reask_rate": 0.0,
            "one_turn_resolution_rate": 1.0,
        },
        "channels": {
            "cli__default": {
                "responses_total": 2,
                "responses_with_feedback": 1,
                "feedback_total": 1,
                "thumb_up_total": 1,
                "thumb_down_total": 0,
                "outcomes_total": 2,
                "positive_feedback_total": 1,
                "negative_feedback_total": 0,
                "reasked_total": 0,
                "resolved_total": 1,
                "follow_up_without_feedback_total": 0,
                "feedback_coverage": 0.5,
                "thumbs_up_rate": 1.0,
                "thumbs_down_rate": 0.0,
                "positive_feedback_rate": 0.5,
                "negative_feedback_rate": 0.0,
                "reask_rate": 0.0,
                "one_turn_resolution_rate": 1.0,
            }
        },
        "sessions": [
            {
                "session_key": "cli__default__session-1",
                "channel": "cli__default",
                "updated_at": "2026-05-01T10:00:00",
                "responses_total": 2,
                "feedback_total": 1,
                "negative_feedback_total": 0,
                "reasked_total": 0,
                "one_turn_resolution_rate": 1.0,
            }
        ],
    }

    table_data = format_feedback_stats_table(stats)

    assert ("Sessions Scanned", "1") in table_data["summary_rows"]
    assert ("One-turn Resolution Rate", "100.0%") in table_data["summary_rows"]
    assert table_data["channel_rows"] == [
        ("cli__default", "2", "1", "50.0%", "100.0%", "0.0%", "100.0%")
    ]
    assert table_data["session_rows"] == [
        (
            "cli__default__session-1",
            "cli__default",
            "2026-05-01T10:00:00",
            "2",
            "1",
            "0",
            "0",
            "100.0%",
        )
    ]


def test_format_feedback_stats_markdown_returns_summary_and_tables():
    stats = {
        "summary": {
            "sessions_scanned": 1,
            "responses_total": 2,
            "responses_with_feedback": 1,
            "feedback_total": 1,
            "thumb_up_total": 1,
            "thumb_down_total": 0,
            "outcomes_total": 2,
            "positive_feedback_total": 1,
            "negative_feedback_total": 0,
            "reasked_total": 0,
            "resolved_total": 1,
            "follow_up_without_feedback_total": 0,
            "feedback_coverage": 0.5,
            "thumbs_up_rate": 1.0,
            "thumbs_down_rate": 0.0,
            "positive_feedback_rate": 0.5,
            "negative_feedback_rate": 0.0,
            "reask_rate": 0.0,
            "one_turn_resolution_rate": 1.0,
        },
        "channels": {
            "cli__default": {
                "responses_total": 2,
                "responses_with_feedback": 1,
                "feedback_total": 1,
                "thumb_up_total": 1,
                "thumb_down_total": 0,
                "outcomes_total": 2,
                "positive_feedback_total": 1,
                "negative_feedback_total": 0,
                "reasked_total": 0,
                "resolved_total": 1,
                "follow_up_without_feedback_total": 0,
                "feedback_coverage": 0.5,
                "thumbs_up_rate": 1.0,
                "thumbs_down_rate": 0.0,
                "positive_feedback_rate": 0.5,
                "negative_feedback_rate": 0.0,
                "reask_rate": 0.0,
                "one_turn_resolution_rate": 1.0,
            }
        },
        "sessions": [
            {
                "session_key": "cli__default__session-1",
                "channel": "cli__default",
                "updated_at": "2026-05-01T10:00:00",
                "responses_total": 2,
                "feedback_total": 1,
                "negative_feedback_total": 0,
                "reasked_total": 0,
                "one_turn_resolution_rate": 1.0,
            }
        ],
    }

    markdown_data = format_feedback_stats_markdown(stats)

    assert "**Sessions Scanned:** 1" in markdown_data["summary_markdown"]
    assert "**One-turn Resolution Rate:** 100.0%" in markdown_data["summary_markdown"]
    assert (
        "| Channel | Responses | Feedback | Coverage | Thumbs Up | Thumbs Down | Resolution |"
        in markdown_data["channels_markdown"]
    )
    assert (
        "| cli__default | 2 | 1 | 50.0% | 100.0% | 0.0% | 100.0% |"
        in markdown_data["channels_markdown"]
    )
    assert (
        "| Session | Channel | Updated At | Responses | Feedback | Negative | Reasked | Resolution |"
        in markdown_data["sessions_markdown"]
    )
    assert (
        "| cli__default__session-1 | cli__default | 2026-05-01T10:00:00 | 2 | 1 | 0 | 0 | 100.0% |"
        in markdown_data["sessions_markdown"]
    )


def test_build_feedback_stats_display_applies_filters_and_sorting(temp_dir):
    sessions_dir = temp_dir / "bot" / "sessions"
    sessions_dir.mkdir(parents=True)

    (sessions_dir / "cli__default__session-1.jsonl").write_text(
        "\n".join(
            [
                json.dumps(
                    {
                        "_type": "metadata",
                        "session_key": "cli__default__session-1",
                        "updated_at": "2026-05-01T10:00:00",
                        "metadata": {
                            "feedback_events": [
                                {"response_id": "resp-1", "feedback_type": "thumb_up"}
                            ],
                            "response_outcomes": {
                                "resp-1": {"outcome_label": "positive_feedback"},
                                "resp-2": {"outcome_label": "resolved"},
                            },
                        },
                    }
                ),
                json.dumps(
                    {
                        "role": "assistant",
                        "content": "answer 1",
                        "response_id": "resp-1",
                        "timestamp": "2026-05-01T10:00:00",
                    }
                ),
                json.dumps(
                    {
                        "role": "assistant",
                        "content": "answer 2",
                        "response_id": "resp-2",
                        "timestamp": "2026-05-01T10:01:00",
                    }
                ),
            ]
        )
        + "\n",
        encoding="utf-8",
    )
    (sessions_dir / "bot_api__demo__session-2.jsonl").write_text(
        "\n".join(
            [
                json.dumps(
                    {
                        "_type": "metadata",
                        "session_key": "bot_api__demo__session-2",
                        "updated_at": "2026-05-03T10:00:00",
                        "metadata": {
                            "feedback_events": [
                                {"response_id": "resp-2", "feedback_type": "thumb_down"},
                                {"response_id": "resp-2", "feedback_type": "thumb_down"},
                            ],
                            "response_outcomes": {
                                "resp-2": {"outcome_label": "negative_feedback"},
                            },
                        },
                    }
                ),
                json.dumps(
                    {
                        "role": "assistant",
                        "content": "answer 3",
                        "response_id": "resp-2",
                        "timestamp": "2026-05-03T10:00:00",
                    }
                ),
            ]
        )
        + "\n",
        encoding="utf-8",
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


def test_compute_feedback_stats_preserves_all_assistant_responses_total_but_uses_tracked_denominators(temp_dir):
    sessions_dir = temp_dir / "bot" / "sessions"
    sessions_dir.mkdir(parents=True)

    (sessions_dir / "cli__default__session-1.jsonl").write_text(
        "\n".join(
            [
                json.dumps(
                    {
                        "_type": "metadata",
                        "session_key": "cli__default__session-1",
                        "updated_at": "2026-05-01T10:00:00",
                        "metadata": {
                            "feedback_events": [
                                {"response_id": "resp-1", "feedback_type": "thumb_up"}
                            ],
                            "response_outcomes": {
                                "resp-1": {"outcome_label": "positive_feedback"}
                            },
                        },
                    }
                ),
                json.dumps(
                    {
                        "role": "assistant",
                        "content": "answer 1",
                        "response_id": "resp-1",
                        "timestamp": "2026-05-01T10:00:00",
                    }
                ),
                json.dumps(
                    {
                        "role": "assistant",
                        "content": "answer 2",
                        "response_id": "resp-2",
                        "timestamp": "2026-05-01T10:01:00",
                    }
                ),
            ]
        )
        + "\n",
        encoding="utf-8",
    )

    stats = compute_feedback_stats(temp_dir / "bot", include_sessions=True)

    assert stats["summary"]["responses_total"] == 2
    assert stats["summary"]["tracked_responses_total"] == 1
    assert stats["summary"]["outcomes_total"] == 1
    assert stats["summary"]["responses_with_feedback"] == 1
    assert stats["summary"]["feedback_coverage"] == 1.0
    assert stats["summary"]["positive_feedback_rate"] == 1.0
    assert stats["summary"]["one_turn_resolution_rate"] == 1.0
    assert stats["channels"]["cli__default"]["responses_total"] == 2
    assert stats["channels"]["cli__default"]["tracked_responses_total"] == 1
    assert stats["channels"]["cli__default"]["positive_feedback_rate"] == 1.0
    assert stats["sessions"][0]["responses_total"] == 2
    assert stats["sessions"][0]["tracked_responses_total"] == 1
    assert stats["sessions"][0]["positive_feedback_rate"] == 1.0


def test_compute_feedback_stats_counts_rating_feedback_via_outcomes(temp_dir):
    sessions_dir = temp_dir / "bot" / "sessions"
    sessions_dir.mkdir(parents=True)

    (sessions_dir / "cli__default__session-1.jsonl").write_text(
        "\n".join(
            [
                json.dumps(
                    {
                        "_type": "metadata",
                        "session_key": "cli__default__session-1",
                        "updated_at": "2026-05-01T10:00:00",
                        "metadata": {
                            "feedback_events": [
                                {
                                    "response_id": "resp-1",
                                    "feedback_type": "rating",
                                    "feedback_score": 1,
                                }
                            ],
                            "response_outcomes": {
                                "resp-1": {"outcome_label": "positive_feedback"},
                                "resp-2": {"outcome_label": "resolved"},
                            },
                        },
                    }
                ),
                json.dumps(
                    {
                        "role": "assistant",
                        "content": "answer 1",
                        "response_id": "resp-1",
                        "timestamp": "2026-05-01T10:00:00",
                    }
                ),
                json.dumps(
                    {
                        "role": "assistant",
                        "content": "answer 2",
                        "response_id": "resp-2",
                        "timestamp": "2026-05-01T10:01:00",
                    }
                ),
            ]
        )
        + "\n",
        encoding="utf-8",
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
    sessions_dir = temp_dir / "bot" / "sessions"
    sessions_dir.mkdir(parents=True)

    (sessions_dir / "cli__default__session-1.jsonl").write_text(
        "\n".join(
            [
                json.dumps(
                    {
                        "_type": "metadata",
                        "session_key": "cli__default__session-1",
                        "updated_at": "2026-05-01T10:00:00",
                        "metadata": {
                            "feedback_events": [
                                {"response_id": "resp-1", "feedback_type": "thumb_up"}
                            ],
                            "response_outcomes": {
                                "resp-1": {"outcome_label": "positive_feedback"},
                                "resp-2": {"outcome_label": "resolved"},
                            },
                        },
                    }
                ),
                json.dumps(
                    {
                        "role": "assistant",
                        "content": "answer 1",
                        "response_id": "resp-1",
                        "timestamp": "2026-05-01T10:00:00",
                    }
                ),
                json.dumps(
                    {
                        "role": "assistant",
                        "content": "legacy answer 2",
                        "response_id": "resp-legacy-2",
                        "timestamp": "2026-05-01T10:01:00",
                    }
                ),
                json.dumps(
                    {
                        "role": "assistant",
                        "content": "legacy answer 3",
                        "response_id": "resp-legacy-3",
                        "timestamp": "2026-05-01T10:02:00",
                    }
                ),
            ]
        )
        + "\n",
        encoding="utf-8",
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
