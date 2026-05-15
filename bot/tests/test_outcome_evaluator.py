from datetime import datetime

from vikingbot.observability.outcome import evaluate_response_outcome


def test_evaluate_response_outcome_marks_resolved_without_follow_up():
    evaluation = evaluate_response_outcome(
        [
            {
                "role": "assistant",
                "content": "hello",
                "response_id": "resp-1",
                "timestamp": "2026-04-30T00:00:00",
            }
        ],
        "resp-1",
        now=datetime.fromisoformat("2026-04-30T00:05:00"),
    )

    assert evaluation is not None
    assert evaluation.outcome_label == "resolved"
    assert evaluation.resolved_in_one_turn is True
    assert evaluation.reask_within_10m is False
    assert evaluation.clarification_turns == 0
    assert evaluation.follow_up_without_feedback is False


def test_evaluate_response_outcome_prefers_positive_feedback():
    evaluation = evaluate_response_outcome(
        [
            {
                "role": "assistant",
                "content": "hello",
                "response_id": "resp-1",
                "timestamp": "2026-04-30T00:00:00",
            },
            {
                "role": "user",
                "content": "thanks",
                "timestamp": "2026-04-30T00:01:00",
            },
        ],
        "resp-1",
        feedback_events=[
            {
                "response_id": "resp-1",
                "feedback_type": "thumb_up",
            }
        ],
        now=datetime.fromisoformat("2026-04-30T00:02:00"),
    )

    assert evaluation is not None
    assert evaluation.outcome_label == "positive_feedback"
    assert evaluation.resolved_in_one_turn is True
    assert evaluation.reask_within_10m is False
    assert evaluation.clarification_turns == 0
    assert evaluation.follow_up_without_feedback is False


def test_evaluate_response_outcome_marks_negative_feedback():
    evaluation = evaluate_response_outcome(
        [
            {
                "role": "assistant",
                "content": "hello",
                "response_id": "resp-1",
                "timestamp": "2026-04-30T00:00:00",
            }
        ],
        "resp-1",
        feedback_events=[
            {
                "response_id": "resp-1",
                "feedback_type": "thumb_down",
            }
        ],
        now=datetime.fromisoformat("2026-04-30T00:02:00"),
    )

    assert evaluation is not None
    assert evaluation.outcome_label == "negative_feedback"
    assert evaluation.resolved_in_one_turn is False
    assert evaluation.follow_up_without_feedback is False


def test_evaluate_response_outcome_maps_positive_rating_to_positive_feedback():
    evaluation = evaluate_response_outcome(
        [
            {
                "role": "assistant",
                "content": "hello",
                "response_id": "resp-1",
                "timestamp": "2026-04-30T00:00:00",
            },
            {
                "role": "user",
                "content": "thanks",
                "timestamp": "2026-04-30T00:01:00",
            },
        ],
        "resp-1",
        feedback_events=[
            {
                "response_id": "resp-1",
                "feedback_type": "rating",
                "feedback_score": 1,
            }
        ],
        now=datetime.fromisoformat("2026-04-30T00:02:00"),
    )

    assert evaluation is not None
    assert evaluation.outcome_label == "positive_feedback"
    assert evaluation.resolved_in_one_turn is True
    assert evaluation.reask_within_10m is False
    assert evaluation.clarification_turns == 0
    assert evaluation.follow_up_without_feedback is False
    assert evaluation.evidence["feedback_score"] == 1.0


def test_evaluate_response_outcome_maps_negative_rating_to_negative_feedback():
    evaluation = evaluate_response_outcome(
        [
            {
                "role": "assistant",
                "content": "hello",
                "response_id": "resp-1",
                "timestamp": "2026-04-30T00:00:00",
            }
        ],
        "resp-1",
        feedback_events=[
            {
                "response_id": "resp-1",
                "feedback_type": "rating",
                "feedback_score": -1,
            }
        ],
        now=datetime.fromisoformat("2026-04-30T00:02:00"),
    )

    assert evaluation is not None
    assert evaluation.outcome_label == "negative_feedback"
    assert evaluation.resolved_in_one_turn is False
    assert evaluation.follow_up_without_feedback is False
    assert evaluation.evidence["feedback_score"] == -1.0


def test_evaluate_response_outcome_keeps_heuristic_outcome_for_neutral_rating():
    evaluation = evaluate_response_outcome(
        [
            {
                "role": "assistant",
                "content": "hello",
                "response_id": "resp-1",
                "timestamp": "2026-04-30T00:00:00",
            }
        ],
        "resp-1",
        feedback_events=[
            {
                "response_id": "resp-1",
                "feedback_type": "rating",
                "feedback_score": 0,
            }
        ],
        now=datetime.fromisoformat("2026-04-30T00:02:00"),
    )

    assert evaluation is not None
    assert evaluation.outcome_label == "resolved"
    assert evaluation.resolved_in_one_turn is True
    assert evaluation.evidence["feedback_score"] == 0.0


def test_evaluate_response_outcome_marks_reasked_within_window():
    evaluation = evaluate_response_outcome(
        [
            {
                "role": "assistant",
                "content": "hello",
                "response_id": "resp-1",
                "timestamp": "2026-04-30T00:00:00",
            },
            {
                "role": "user",
                "content": "that did not help",
                "timestamp": "2026-04-30T00:05:00",
            },
        ],
        "resp-1",
        now=datetime.fromisoformat("2026-04-30T00:06:00"),
    )

    assert evaluation is not None
    assert evaluation.outcome_label == "reasked"
    assert evaluation.resolved_in_one_turn is False
    assert evaluation.reask_within_10m is True
    assert evaluation.clarification_turns == 1
    assert evaluation.follow_up_without_feedback is False


def test_evaluate_response_outcome_marks_follow_up_without_feedback():
    evaluation = evaluate_response_outcome(
        [
            {
                "role": "assistant",
                "content": "hello",
                "response_id": "resp-1",
                "timestamp": "2026-04-30T00:00:00",
            },
            {
                "role": "user",
                "content": "another question later",
                "timestamp": "2026-04-30T00:20:00",
            },
        ],
        "resp-1",
        now=datetime.fromisoformat("2026-04-30T00:21:00"),
    )

    assert evaluation is not None
    assert evaluation.outcome_label == "follow_up_without_feedback"
    assert evaluation.resolved_in_one_turn is False
    assert evaluation.reask_within_10m is False
    assert evaluation.clarification_turns == 1
    assert evaluation.follow_up_without_feedback is True
