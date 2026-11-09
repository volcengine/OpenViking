# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0
"""Phase 2 extraction batches derived from a session's auto-commit policy."""

from __future__ import annotations

from dataclasses import dataclass, replace
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


def _policy_batch_limits(auto_commit_policy: Any) -> ExtractionBatchLimits:
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


def resolve_extraction_batch_limits(
    auto_commit_policy: Any,
    prompt_token_limit: Optional[int] = None,
) -> ExtractionBatchLimits:
    """Batch limits for one Phase-2 long-term extraction pass.

    ``prompt_token_limit`` is the prompt budget of the model that runs the
    extraction, derived from its context window by
    :func:`openviking.models.vlm.context_window.resolve_prompt_token_limit`. It
    caps the policy threshold in both directions: sessions without an
    auto-commit policy are batched instead of sent in one unbounded call, and a
    policy that allows more than the model window cannot force a request the
    backend will silently truncate (which surfaces as ``parse_error``).
    """
    policy_limits = _policy_batch_limits(auto_commit_policy)
    candidates = [
        limit
        for limit in (policy_limits.max_message_tokens, prompt_token_limit)
        if limit is not None
    ]
    return ExtractionBatchLimits(
        max_message_tokens=min(candidates) if candidates else None,
        max_messages=policy_limits.max_messages,
    )


def estimate_extraction_message_tokens(messages: Iterable[Message]) -> int:
    return sum(int(message.estimated_tokens or 0) for message in messages)


# A single message can exceed the ceiling on its own (a hydrated tool output is
# the usual case). Splitting it would break tool call/result pairing, so the
# extraction copy is trimmed instead: a bounded, explicitly marked prompt beats an
# over-window request that the backend truncates and the extraction loses whole.
TRUNCATION_MARKER = "\n\n[truncated for extraction: {kept} of {total} characters kept]"
MIN_TRIMMED_PART_CHARS = 200
MAX_TRIM_ROUNDS = 8


def _trimmable_text(part: Any) -> Optional[str]:
    text = getattr(part, "text", None)
    if isinstance(text, str) and text:
        return text
    output = getattr(part, "tool_output", None)
    if isinstance(output, str) and output:
        return output
    return None


def _with_trimmed_text(part: Any, text: str) -> Any:
    if isinstance(getattr(part, "text", None), str):
        replacement = replace(part, text=text)
    else:
        replacement = replace(part, tool_output=text)
    return replacement


def _bound_message(message: Message, ceiling: int) -> Message:
    """Trim one over-ceiling message until it fits, largest part first."""
    parts = list(message.parts)
    for _ in range(MAX_TRIM_ROUNDS):
        candidate = replace(message, parts=parts)
        current_tokens = int(candidate.estimated_tokens or 0)
        if current_tokens <= ceiling:
            return candidate
        sizes = [
            (len(text), index)
            for index, part in enumerate(parts)
            if (text := _trimmable_text(part)) is not None
        ]
        if not sizes:
            return candidate
        _, largest = max(sizes)
        text = _trimmable_text(parts[largest]) or ""
        keep_ratio = max(0.1, ceiling / current_tokens * 0.95)
        kept_chars = max(MIN_TRIMMED_PART_CHARS, min(len(text), int(len(text) * keep_ratio)))
        trimmed = text[:kept_chars] + TRUNCATION_MARKER.format(kept=kept_chars, total=len(text))
        parts[largest] = _with_trimmed_text(parts[largest], trimmed)
    return replace(message, parts=parts)


def bound_oversized_extraction_messages(
    messages: List[Message],
    limits: ExtractionBatchLimits,
) -> tuple[List[Message], int]:
    """Fit every message inside the batch ceiling before planning passes.

    Returns the (possibly trimmed) messages and how many had to be trimmed, so the
    caller can make the loss visible in logs.
    """
    ceiling = limits.max_message_tokens
    if ceiling is None:
        return messages, 0
    bounded: List[Message] = []
    trimmed_count = 0
    for message in messages:
        if int(message.estimated_tokens or 0) <= ceiling:
            bounded.append(message)
            continue
        bounded.append(_bound_message(message, ceiling))
        trimmed_count += 1
    return bounded, trimmed_count


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
    "bound_oversized_extraction_messages",
    "ExtractionMessageBatch",
    "estimate_extraction_message_tokens",
    "plan_extraction_batches",
    "resolve_extraction_batch_limits",
]
