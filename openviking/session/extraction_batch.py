# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0
"""Phase 2 extraction batches derived from a session's auto-commit policy."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Iterable, List, Optional

from openviking.message import Message
from openviking.session.auto_commit_policy import AutoCommitPolicy
from openviking.session.retention import UserTurn, build_turns


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


__all__ = [
    "ExtractionBatchLimits",
    "ExtractionMessageBatch",
    "estimate_extraction_message_tokens",
    "plan_extraction_batches",
    "resolve_extraction_batch_limits",
]
