"""Outcome evaluation helpers for feedback observability Phase 3."""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Any, Optional

REASK_WINDOW = timedelta(minutes=10)

_POSITIVE_FEEDBACK_PATTERNS = (
    re.compile(r"\b(thanks|thank you|thx|ty)\b", re.IGNORECASE),
    re.compile(r"\b(helpful|great|awesome|perfect|solved|works?)\b", re.IGNORECASE),
    re.compile(r"有帮助|帮到我了|解决了|搞定了|可以了|很好|太棒了|谢了|谢谢", re.IGNORECASE),
)
_NEGATIVE_FEEDBACK_PATTERNS = (
    re.compile(r"\b(not helpful|did not help|doesn't help|does not help|wrong|bad answer)\b", re.IGNORECASE),
    re.compile(r"没帮助|没有帮助|不对|答非所问|没解决|没有解决|还是不行|不行|有问题", re.IGNORECASE),
)
_FOLLOW_UP_SIGNAL_PATTERNS = (
    re.compile(r"\?"),
    re.compile(r"\b(how|what|why|can you|could you|please)\b", re.IGNORECASE),
    re.compile(r"怎么|为什么|能不能|可以帮我|请问|请继续", re.IGNORECASE),
)


@dataclass(frozen=True)
class DetectedFeedback:
    """Implicit feedback inferred from a user's natural-language reply."""

    feedback_type: str
    feedback_text: str
    feedback_reason: str = "natural_language"
    feedback_score: float | None = None


@dataclass(frozen=True)
class LLMFeedbackDecision:
    """Structured result for LLM-based natural-language feedback classification."""

    is_feedback: bool
    sentiment: str
    confidence: float


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


def detect_feedback_from_message(message: str) -> DetectedFeedback | None:
    """Infer a positive or negative feedback signal from a free-form user reply."""
    normalized = (message or "").strip()
    if not normalized:
        return None

    lowered = normalized.lower()
    has_positive = any(pattern.search(normalized) for pattern in _POSITIVE_FEEDBACK_PATTERNS)
    has_negative = any(pattern.search(normalized) for pattern in _NEGATIVE_FEEDBACK_PATTERNS)
    has_follow_up_signal = any(pattern.search(normalized) for pattern in _FOLLOW_UP_SIGNAL_PATTERNS)

    if has_positive and not has_negative and not has_follow_up_signal:
        return DetectedFeedback(
            feedback_type="thumb_up",
            feedback_text=normalized,
            feedback_score=1.0,
        )

    if has_negative and not has_positive:
        if "?" in normalized and not lowered.startswith(("why", "what", "how", "can you", "could you")):
            return DetectedFeedback(
                feedback_type="thumb_down",
                feedback_text=normalized,
                feedback_score=-1.0,
            )
        if not has_follow_up_signal or lowered.startswith(("not helpful", "did not help", "wrong", "bad answer")):
            return DetectedFeedback(
                feedback_type="thumb_down",
                feedback_text=normalized,
                feedback_score=-1.0,
            )

    return None


def normalize_llm_feedback_decision(payload: dict[str, Any]) -> LLMFeedbackDecision | None:
    """Normalize raw JSON output from an LLM feedback classifier."""
    if not isinstance(payload, dict):
        return None

    is_feedback = payload.get("is_feedback")
    sentiment = str(payload.get("sentiment") or "").strip().lower()
    confidence = payload.get("confidence")

    if not isinstance(is_feedback, bool):
        return None
    if sentiment not in {"positive", "negative", "none"}:
        return None
    if isinstance(confidence, bool) or not isinstance(confidence, (int, float)):
        return None

    normalized_confidence = max(0.0, min(1.0, float(confidence)))
    return LLMFeedbackDecision(
        is_feedback=is_feedback,
        sentiment=sentiment,
        confidence=normalized_confidence,
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
