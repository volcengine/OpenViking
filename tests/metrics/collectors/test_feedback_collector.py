# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0

from __future__ import annotations

import json

from openviking.metrics.collectors.feedback import FeedbackCollector


def test_feedback_collector_exports_summary_and_channel_gauges(
    registry, render_prometheus, tmp_path
):
    sessions_dir = tmp_path / "bot" / "sessions"
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
                                {"response_id": "resp-3", "feedback_type": "thumb_down"},
                                {"response_id": "resp-3", "feedback_type": "thumb_down"},
                            ],
                            "response_outcomes": {
                                "resp-3": {"outcome_label": "negative_feedback"},
                                "resp-4": {"outcome_label": "reasked"},
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
            ]
        )
        + "\n",
        encoding="utf-8",
    )

    FeedbackCollector(bot_data_path=tmp_path / "bot").collect(registry)
    text = render_prometheus(registry)

    assert 'openviking_feedback_sessions_scanned_total{valid="1"} 2.0' in text
    assert 'openviking_feedback_responses_total{valid="1"} 4.0' in text
    assert 'openviking_feedback_tracked_responses_total{valid="1"} 4.0' in text
    assert 'openviking_feedback_events_total{valid="1"} 3.0' in text
    assert 'openviking_feedback_coverage{valid="1"} 0.5' in text
    assert 'openviking_feedback_one_turn_resolution_rate{valid="1"} 0.5' in text
    assert (
        'openviking_feedback_channel_tracked_responses_total{channel="cli__default",valid="1"} 2.0'
        in text
    )
    assert 'openviking_feedback_channel_events_total{channel="cli__default",valid="1"} 1.0' in text
    assert 'openviking_feedback_channel_events_total{channel="bot_api__demo",valid="1"} 2.0' in text
    assert (
        'openviking_feedback_channel_thumbs_down_rate{channel="bot_api__demo",valid="1"} 1.0'
        in text
    )


def test_feedback_collector_reuses_last_values_with_valid_zero_on_failure(
    registry, render_prometheus, tmp_path, monkeypatch
):
    sessions_dir = tmp_path / "bot" / "sessions"
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
            ]
        )
        + "\n",
        encoding="utf-8",
    )

    collector = FeedbackCollector(bot_data_path=tmp_path / "bot")
    collector.collect(registry)

    def _boom(*args, **kwargs):
        raise RuntimeError("boom")

    monkeypatch.setattr("openviking.metrics.collectors.feedback.compute_feedback_stats", _boom)
    collector.collect(registry)
    text = render_prometheus(registry)

    assert 'openviking_feedback_events_total{valid="0"} 1.0' in text
    assert 'openviking_feedback_coverage{valid="0"} 1.0' in text
    assert 'openviking_feedback_channel_events_total{channel="cli__default",valid="0"} 1.0' in text
