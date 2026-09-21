from datetime import datetime

import pytest
from vikingbot.observability.outcome import evaluate_response_outcome


@pytest.mark.parametrize(
    "feedback, follow_up_minute, expected",
    [
        pytest.param(None, None, ("resolved", True, False, 0, False), id="resolved"),
        pytest.param(
            {"feedback_type": "thumb_up"},
            1,
            ("positive_feedback", True, False, 0, False),
            id="positive-feedback",
        ),
        pytest.param(
            {"feedback_type": "thumb_down"},
            None,
            ("negative_feedback", False, False, 0, False),
            id="negative-feedback",
        ),
        pytest.param(
            {"feedback_type": "rating", "feedback_score": 1},
            1,
            ("positive_feedback", True, False, 0, False),
            id="positive-rating",
        ),
        pytest.param(
            {"feedback_type": "rating", "feedback_score": -1},
            None,
            ("negative_feedback", False, False, 0, False),
            id="negative-rating",
        ),
        pytest.param(
            {"feedback_type": "rating", "feedback_score": 0},
            None,
            ("resolved", True, False, 0, False),
            id="neutral-rating",
        ),
        pytest.param(None, 5, ("reasked", False, True, 1, False), id="reasked"),
        pytest.param(
            None, 20, ("follow_up_without_feedback", False, False, 1, True), id="later-follow-up"
        ),
    ],
)
def test_response_outcome(feedback, follow_up_minute, expected):
    messages = [
        {
            "role": "assistant",
            "content": "hello",
            "response_id": "resp-1",
            "timestamp": "2026-04-30T00:00:00",
        }
    ]
    if follow_up_minute is not None:
        messages.append(
            {
                "role": "user",
                "content": "follow-up",
                "timestamp": f"2026-04-30T00:{follow_up_minute:02d}:00",
            }
        )
    events = [{"response_id": "resp-1", **feedback}] if feedback is not None else []
    evaluation = evaluate_response_outcome(
        messages,
        "resp-1",
        feedback_events=events,
        now=datetime(2026, 4, 30, 0, (follow_up_minute or 1) + 1),
    )
    assert evaluation is not None
    assert (
        evaluation.outcome_label,
        evaluation.resolved_in_one_turn,
        evaluation.reask_within_10m,
        evaluation.clarification_turns,
        evaluation.follow_up_without_feedback,
    ) == expected
    if feedback is not None and "feedback_score" in feedback:
        assert evaluation.evidence["feedback_score"] == feedback["feedback_score"]
