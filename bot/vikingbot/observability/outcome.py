"""Outcome evaluation helpers for feedback observability Phase 3."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Any, Optional

REASK_WINDOW = timedelta(minutes=10)


@dataclass
class OutcomeEvaluation:
    """Structured outcome evaluation for a single assistant response."""

    response_id: str
    resolved_in_one_turn: bool
    reask_within_10m: bool
    clarification_turns: int
    follow_up_without_feedback: bool
    outcome_label: str
    evaluated_at: str
    evidence: dict[str, Any]

    def to_dict(self) -> dict[str, Any]:
        return {
            "response_id": self.response_id,
            "resolved_in_one_turn": self.resolved_in_one_turn,
            "reask_within_10m": self.reask_within_10m,
            "clarification_turns": self.clarification_turns,
            "follow_up_without_feedback": self.follow_up_without_feedback,
            "outcome_label": self.outcome_label,
            "evaluated_at": self.evaluated_at,
            "evidence": self.evidence,
        }


def evaluate_response_outcome(
    messages: list[dict[str, Any]],
    response_id: str,
    *,
    feedback_events: Optional[list[dict[str, Any]]] = None,
    now: Optional[datetime] = None,
) -> OutcomeEvaluation | None:
    """Evaluate the best-known outcome for a response from session history."""
    assistant_index = _find_response_index(messages, response_id)
    if assistant_index is None:
        return None

    assistant_message = messages[assistant_index]
    assistant_timestamp = _parse_timestamp(assistant_message)
    if assistant_timestamp is None:
        assistant_timestamp = now or datetime.now()

    following_messages = messages[assistant_index + 1 :]
    user_messages = [m for m in following_messages if m.get("role") == "user"]
    clarification_turns = len(user_messages)

    relevant_feedback = [
        event for event in (feedback_events or []) if event.get("response_id") == response_id
    ]
    latest_feedback = relevant_feedback[-1] if relevant_feedback else None
    feedback_type = latest_feedback.get("feedback_type") if latest_feedback else None
    feedback_score = _parse_feedback_score(latest_feedback) if latest_feedback else None

    reask_within_10m = False
    first_user_after_response = user_messages[0] if user_messages else None
    if first_user_after_response is not None:
        user_timestamp = _parse_timestamp(first_user_after_response)
        if user_timestamp is None:
            user_timestamp = now or datetime.now()
        reask_within_10m = user_timestamp - assistant_timestamp <= REASK_WINDOW

    resolved_in_one_turn = not user_messages
    follow_up_without_feedback = bool(user_messages) and not relevant_feedback

    if feedback_type == "thumb_down" or (
        feedback_type == "rating" and feedback_score is not None and feedback_score < 0
    ):
        outcome_label = "negative_feedback"
        resolved_in_one_turn = False
    elif feedback_type == "thumb_up" or (
        feedback_type == "rating" and feedback_score is not None and feedback_score > 0
    ):
        outcome_label = "positive_feedback"
        resolved_in_one_turn = True
        reask_within_10m = False
        clarification_turns = 0
        follow_up_without_feedback = False
    elif reask_within_10m:
        outcome_label = "reasked"
        resolved_in_one_turn = False
        follow_up_without_feedback = False
    elif resolved_in_one_turn:
        outcome_label = "resolved"
        follow_up_without_feedback = False
    elif follow_up_without_feedback:
        outcome_label = "follow_up_without_feedback"
    else:
        outcome_label = "follow_up"

    evaluated_at = (now or datetime.now()).isoformat()
    return OutcomeEvaluation(
        response_id=response_id,
        resolved_in_one_turn=resolved_in_one_turn,
        reask_within_10m=reask_within_10m,
        clarification_turns=clarification_turns,
        follow_up_without_feedback=follow_up_without_feedback,
        outcome_label=outcome_label,
        evaluated_at=evaluated_at,
        evidence={
            "feedback_type": feedback_type,
            "feedback_score": feedback_score,
            "user_follow_up_count": len(user_messages),
            "assistant_index": assistant_index,
        },
    )


def should_update_outcome(previous: Optional[dict[str, Any]], current: OutcomeEvaluation) -> bool:
    """Check whether a newly derived outcome meaningfully changes stored state."""
    if previous is None:
        return True
    return any(
        previous.get(field) != getattr(current, field)
        for field in (
            "resolved_in_one_turn",
            "reask_within_10m",
            "clarification_turns",
            "follow_up_without_feedback",
            "outcome_label",
        )
    )


def _find_response_index(messages: list[dict[str, Any]], response_id: str) -> Optional[int]:
    for index, message in enumerate(messages):
        if message.get("role") == "assistant" and message.get("response_id") == response_id:
            return index
    return None


def _parse_timestamp(message: dict[str, Any]) -> Optional[datetime]:
    timestamp = message.get("timestamp")
    if not isinstance(timestamp, str) or not timestamp:
        return None
    try:
        return datetime.fromisoformat(timestamp)
    except ValueError:
        return None


def _parse_feedback_score(feedback_event: dict[str, Any]) -> Optional[float]:
    score = feedback_event.get("feedback_score")
    if isinstance(score, bool) or score is None:
        return None
    if isinstance(score, (int, float)):
        return float(score)
    return None
