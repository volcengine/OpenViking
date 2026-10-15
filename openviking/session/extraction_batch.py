# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0
"""Phase 2 extraction batches derived from a session's auto-commit policy."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Callable, Iterable, List, Optional, Sequence

from openviking.message import Message
from openviking.session.auto_commit_policy import AutoCommitPolicy
from openviking.session.retention import UserTurn, build_turns
from openviking.session.topic_continuity import (
    ContinuityChecker,
    TopicContinuityOptions,
    default_continuity_check,
    should_skip_topic_group,
)


@dataclass(frozen=True)
class ExtractionBatchLimits:
    max_message_tokens: Optional[int] = None
    max_messages: Optional[int] = None

    @property
    def enabled(self) -> bool:
        return self.max_message_tokens is not None or self.max_messages is not None


@dataclass(frozen=True)
class ExtractionMessageBatch:
    messages: tuple[Message, ...]
    estimated_tokens: int
    oversized: bool = False
    owned_message_ids: Optional[frozenset[str]] = None

    def owned_messages(self) -> List[Message]:
        if self.owned_message_ids is None:
            return list(self.messages)
        owned = self.owned_message_ids
        return [message for message in self.messages if message.id in owned]


def resolve_extraction_batch_limits(auto_commit_policy: Any) -> ExtractionBatchLimits:
    if not isinstance(auto_commit_policy, dict) or not auto_commit_policy:
        return ExtractionBatchLimits()
    policy = AutoCommitPolicy.from_dict(auto_commit_policy)
    return ExtractionBatchLimits(
        max_message_tokens=(
            policy.pending_token_threshold if policy.pending_token_threshold > 0 else None
        ),
        max_messages=(
            policy.message_count_threshold if policy.message_count_threshold > 0 else None
        ),
    )


def estimate_extraction_message_tokens(messages: Iterable[Message]) -> int:
    return sum(int(message.estimated_tokens or 0) for message in messages)


def should_batch_long_term_extraction(
    limits: ExtractionBatchLimits,
    topic: Optional[TopicContinuityOptions],
) -> bool:
    return limits.enabled or (topic is not None and topic.enabled)


def _exceeds_limits(
    *,
    message_count: int,
    estimated_tokens: int,
    limits: ExtractionBatchLimits,
) -> bool:
    return bool(
        (limits.max_message_tokens is not None and estimated_tokens > limits.max_message_tokens)
        or (limits.max_messages is not None and message_count > limits.max_messages)
    )


def _turn_units(turn: UserTurn, limits: ExtractionBatchLimits) -> List[List[Message]]:
    messages = list(turn.messages)
    if not _exceeds_limits(
        message_count=len(messages),
        estimated_tokens=estimate_extraction_message_tokens(messages),
        limits=limits,
    ):
        return [messages]

    units: List[List[Message]] = []
    if turn.anchor is not None:
        units.append([turn.anchor])
    for step in turn.steps:
        step_messages = list(step.messages)
        if _exceeds_limits(
            message_count=len(step_messages),
            estimated_tokens=estimate_extraction_message_tokens(step_messages),
            limits=limits,
        ):
            units.extend([[message] for message in step_messages])
        elif step_messages:
            units.append(step_messages)
    return units or [messages]


def _flatten_turns(turns: Sequence[UserTurn]) -> List[Message]:
    return [message for turn in turns for message in turn.messages]


def _turns_continuous(
    previous: UserTurn,
    current: UserTurn,
    continuity_check: ContinuityChecker,
) -> bool:
    try:
        return bool(continuity_check(previous, current))
    except Exception:
        return True


def _with_owned_ids(
    batch: ExtractionMessageBatch,
    owned_message_ids: frozenset[str],
) -> ExtractionMessageBatch:
    return ExtractionMessageBatch(
        messages=batch.messages,
        estimated_tokens=batch.estimated_tokens,
        oversized=batch.oversized,
        owned_message_ids=owned_message_ids,
    )


def _size_split_topic_group(
    overlap_turns: Sequence[UserTurn],
    owned_turns: Sequence[UserTurn],
    limits: ExtractionBatchLimits,
) -> List[ExtractionMessageBatch]:
    owned_messages = _flatten_turns(owned_turns)
    if not owned_messages:
        return []
    owned_ids = frozenset(message.id for message in owned_messages)
    combined = _flatten_turns(overlap_turns) + owned_messages
    batches: List[ExtractionMessageBatch] = []
    for batch in plan_extraction_batches(combined, limits):
        batch_owned = frozenset(
            message.id for message in batch.messages if message.id in owned_ids
        )
        if not batch_owned:
            continue
        batches.append(_with_owned_ids(batch, batch_owned))
    return batches


def _group_turns_by_topic(
    turns: Sequence[UserTurn],
    topic: TopicContinuityOptions,
    continuity_check: ContinuityChecker,
) -> List[tuple[List[UserTurn], List[UserTurn]]]:
    """Return (overlap_prefix, owned_turns) groups in conversation order."""
    groups: List[tuple[List[UserTurn], List[UserTurn]]] = []
    current_owned: List[UserTurn] = [turns[0]]
    pending_overlap: List[UserTurn] = []
    overlap_turns = max(0, int(topic.overlap_turns or 0))
    max_turns = max(1, int(topic.max_turns or 1))

    def owned_turn_cap(overlap_prefix: Sequence[UserTurn]) -> int:
        if not overlap_prefix:
            return max_turns
        return max(1, max_turns - len(overlap_prefix))

    def flush(next_overlap: List[UserTurn]) -> None:
        nonlocal current_owned, pending_overlap
        groups.append((pending_overlap, current_owned))
        pending_overlap = next_overlap
        current_owned = []

    for current in turns[1:]:
        previous = current_owned[-1]
        continuous = _turns_continuous(previous, current, continuity_check)
        at_cap = len(current_owned) >= owned_turn_cap(pending_overlap)
        if continuous and not at_cap:
            current_owned.append(current)
            continue
        next_overlap: List[UserTurn] = []
        if continuous and at_cap and overlap_turns > 0:
            next_overlap = list(current_owned[-overlap_turns:])
        flush(next_overlap)
        current_owned = [current]

    if current_owned:
        groups.append((pending_overlap, current_owned))
    return groups


def plan_extraction_batches(
    messages: List[Message],
    limits: ExtractionBatchLimits,
) -> List[ExtractionMessageBatch]:
    if not messages:
        return []
    if not limits.enabled:
        return [
            ExtractionMessageBatch(
                messages=tuple(messages),
                estimated_tokens=estimate_extraction_message_tokens(messages),
            )
        ]

    units = [unit for turn in build_turns(messages) for unit in _turn_units(turn, limits)]
    batches: List[ExtractionMessageBatch] = []
    current: List[Message] = []
    current_tokens = 0

    def flush() -> None:
        nonlocal current, current_tokens
        if not current:
            return
        batches.append(
            ExtractionMessageBatch(
                messages=tuple(current),
                estimated_tokens=current_tokens,
                oversized=_exceeds_limits(
                    message_count=len(current),
                    estimated_tokens=current_tokens,
                    limits=limits,
                ),
            )
        )
        current = []
        current_tokens = 0

    for unit in units:
        if not unit:
            continue
        unit_tokens = estimate_extraction_message_tokens(unit)
        if current and _exceeds_limits(
            message_count=len(current) + len(unit),
            estimated_tokens=current_tokens + unit_tokens,
            limits=limits,
        ):
            flush()
        if _exceeds_limits(
            message_count=len(unit),
            estimated_tokens=unit_tokens,
            limits=limits,
        ):
            flush()
            batches.append(
                ExtractionMessageBatch(
                    messages=tuple(unit),
                    estimated_tokens=unit_tokens,
                    oversized=True,
                )
            )
            continue
        current.extend(unit)
        current_tokens += unit_tokens

    flush()
    return batches


def plan_long_term_extraction_batches(
    messages: List[Message],
    limits: ExtractionBatchLimits,
    *,
    topic: Optional[TopicContinuityOptions] = None,
    continuity_check: Optional[Callable[[UserTurn, UserTurn], bool]] = None,
) -> List[ExtractionMessageBatch]:
    """Plan long-term extraction batches, optionally grouping by topic first."""
    if not messages:
        return []
    if topic is None or not topic.enabled:
        return plan_extraction_batches(messages, limits)

    turns = build_turns(messages)
    if len(turns) < max(1, int(topic.min_turns_to_split or 1)):
        return plan_extraction_batches(messages, limits)

    checker = continuity_check or default_continuity_check(topic)
    batches: List[ExtractionMessageBatch] = []
    for overlap, owned in _group_turns_by_topic(turns, topic, checker):
        if should_skip_topic_group(owned, topic.skip_max_user_chars):
            continue
        batches.extend(_size_split_topic_group(overlap, owned, limits))
    return batches


__all__ = [
    "ExtractionBatchLimits",
    "ExtractionMessageBatch",
    "estimate_extraction_message_tokens",
    "plan_extraction_batches",
    "plan_long_term_extraction_batches",
    "resolve_extraction_batch_limits",
    "should_batch_long_term_extraction",
]
