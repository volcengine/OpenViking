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
MAX_TARGETED_ROUNDS = 12
MAX_SCALE_STEPS = 24
# Shorter leaves (paths, statuses, flags) are left alone while anything larger can
# still be shortened; they carry structure rather than bulk.
MIN_SCALABLE_CHARS = 64

# One prompt-bearing string inside a message: which part, which field, and for
# tool arguments the path to the string leaf inside the argument object.
_PayloadSlot = tuple[int, str, tuple, int]


def _string_leaves(value: Any, path: tuple = ()) -> List[tuple[tuple, str]]:
    """Every non-empty string leaf under a tool argument object, with its path."""
    if isinstance(value, str):
        return [(path, value)] if value else []
    if isinstance(value, dict):
        return [leaf for key, item in value.items() for leaf in _string_leaves(item, (*path, key))]
    if isinstance(value, (list, tuple)):
        return [
            leaf
            for index, item in enumerate(value)
            for leaf in _string_leaves(item, (*path, index))
        ]
    return []


def _payload_slots(parts: List[Any]) -> List[_PayloadSlot]:
    """Every string that reaches the prompt, mirroring ``Message.estimated_tokens``.

    Text, context abstract, tool output and *every* string leaf of the tool
    arguments count towards the request, so leaving any of them unmanaged keeps a
    message over budget no matter how much of the rest is shortened.
    """
    slots: List[_PayloadSlot] = []
    for index, part in enumerate(parts):
        for field in ("text", "abstract", "tool_output"):
            text = getattr(part, field, None)
            if isinstance(text, str) and text:
                slots.append((index, field, (), len(text)))
        tool_input = getattr(part, "tool_input", None)
        if tool_input:
            for path, text in _string_leaves(tool_input):
                slots.append((index, "tool_input", path, len(text)))
    return slots


def _read_slot(part: Any, field: str, path: tuple) -> str:
    if field != "tool_input":
        return getattr(part, field, "") or ""
    value: Any = getattr(part, "tool_input", None)
    for key in path:
        value = value[key]
    return value if isinstance(value, str) else ""


def _replace_leaf(value: Any, path: tuple, replacement: str) -> Any:
    if not path:
        return replacement
    head, rest = path[0], path[1:]
    if isinstance(value, dict):
        updated_dict = dict(value)
        updated_dict[head] = _replace_leaf(value[head], rest, replacement)
        return updated_dict
    if isinstance(value, (list, tuple)):
        updated_list = list(value)
        updated_list[head] = _replace_leaf(value[head], rest, replacement)
        return updated_list
    return value


def _write_slot(part: Any, field: str, path: tuple, text: str) -> Any:
    if field == "tool_input":
        return replace(part, tool_input=_replace_leaf(part.tool_input, path, text))
    return replace(part, **{field: text})


def _shorten_slot(part: Any, field: str, path: tuple, keep_chars: int) -> Any:
    text = _read_slot(part, field, path)
    kept = text[: max(0, min(len(text), keep_chars))]
    marked = kept + TRUNCATION_MARKER.format(kept=len(kept), total=len(text))
    return _write_slot(part, field, path, marked)


def _estimated_tokens(message: Message, parts: List[Any]) -> int:
    return int(replace(message, parts=parts).estimated_tokens or 0)


def _bound_message(message: Message, ceiling: int) -> Message:
    """Trim one over-ceiling message until it fits the budget.

    The largest payloads are shortened first so short text survives intact; a
    final uniform pass over every prompt-bearing string guarantees the ceiling is
    met whenever there is enough reducible text, which is the only outcome the
    caller may rely on.
    """
    parts = list(message.parts)
    for _ in range(MAX_TARGETED_ROUNDS):
        if _estimated_tokens(message, parts) <= ceiling:
            return replace(message, parts=parts)
        slots = _payload_slots(parts)
        if not slots:
            return replace(message, parts=parts)
        index, field, path, length = max(slots, key=lambda slot: slot[3])
        parts[index] = _shorten_slot(parts[index], field, path, length // 2)

    bounded = replace(message, parts=parts)
    if _estimated_tokens(message, parts) <= ceiling:
        return bounded
    slots = _payload_slots(parts)
    scalable = [slot for slot in slots if slot[3] >= MIN_SCALABLE_CHARS] or slots
    if not scalable:
        return bounded
    floor_parts = list(parts)
    for index, field, path, _ in scalable:
        floor_parts[index] = _shorten_slot(floor_parts[index], field, path, 0)
    floor_trial = replace(message, parts=floor_parts)
    low, high = 0.0, 1.0
    fitted: Optional[Message] = None
    for _ in range(MAX_SCALE_STEPS):
        factor = (low + high) / 2
        scaled = list(parts)
        for index, field, path, length in scalable:
            scaled[index] = _shorten_slot(scaled[index], field, path, int(length * factor))
        trial = replace(message, parts=scaled)
        if int(trial.estimated_tokens or 0) <= ceiling:
            fitted = trial
            low = factor
        else:
            high = factor
    if fitted is not None:
        return fitted
    # Nothing fits: the floor is the smallest form reachable by shortening alone,
    # and the caller reports the pass as over budget rather than hiding it.
    if int(floor_trial.estimated_tokens or 0) < _estimated_tokens(message, parts):
        return floor_trial
    return bounded


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
