# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0
"""Pure planning for turn-aware session retention.

The planner deliberately has no filesystem or model dependencies.  It groups
physical messages into logical user turns and atomic assistant steps, then
chooses the archive/live boundary under a token budget.
"""

from __future__ import annotations

import copy
from dataclasses import dataclass, field
from typing import Dict, Iterable, List, Optional

from openviking.message import Message
from openviking.message.part import ContextPart, TextPart, ToolPart
from openviking.utils.token_estimation import truncate_text_to_token_budget

RETENTION_MODE_TURN_BUDGET = "turn_budget"
_CHECKPOINT_TOKEN_RESERVE = 1024


def is_tool_transport(message: Message) -> bool:
    """Return whether a user-role message only transports tool results."""
    if message.message_kind == "tool_transport":
        return True
    if message.message_kind == "user_query":
        return False
    return (
        bool(message.parts)
        and message.role == "user"
        and all(isinstance(part, ToolPart) for part in message.parts)
    )


def is_user_query(message: Message) -> bool:
    """Return whether a message starts a real logical user turn."""
    if message.message_kind == "user_query":
        return True
    if message.message_kind == "tool_transport":
        return False
    return message.role == "user" and not is_tool_transport(message)


@dataclass
class AssistantStep:
    """One assistant response plus any following tool-transport messages."""

    messages: List[Message] = field(default_factory=list)

    @property
    def estimated_tokens(self) -> int:
        return sum(int(message.estimated_tokens or 0) for message in self.messages)


@dataclass
class UserTurn:
    """A real user query and everything before the next real user query."""

    anchor: Optional[Message] = None
    steps: List[AssistantStep] = field(default_factory=list)

    @property
    def messages(self) -> List[Message]:
        result: List[Message] = []
        if self.anchor is not None:
            result.append(self.anchor)
        for step in self.steps:
            result.extend(step.messages)
        return result

    @property
    def estimated_tokens(self) -> int:
        return sum(int(message.estimated_tokens or 0) for message in self.messages)


@dataclass
class RetentionPlan:
    """The complete, deterministic output of a retention decision."""

    archive_messages: List[Message]
    retained_messages: List[Message]
    turn_anchor: Optional[Message] = None
    checkpoint_source_message_ids: List[str] = field(default_factory=list)
    raw_tail_start_message_id: Optional[str] = None
    estimated_active_tokens: int = 0
    budget_exceeded: bool = False
    partial_turn: bool = False

    @property
    def has_archive_work(self) -> bool:
        return bool(self.archive_messages)


@dataclass
class ActiveMessageBudgetPlan:
    """A non-destructive, budgeted view of active messages."""

    messages: List[Message]
    estimated_tokens: int
    dropped_message_ids: List[str] = field(default_factory=list)
    truncated_message_ids: List[str] = field(default_factory=list)


def _append_to_turn(turn: UserTurn, message: Message) -> None:
    """Append a message without splitting assistant/tool-result relationships."""
    if is_tool_transport(message) and turn.steps:
        turn.steps[-1].messages.append(message)
        return

    if message.role == "assistant":
        turn.steps.append(AssistantStep(messages=[message]))
        return

    # Malformed/legacy input can contain transport messages before an assistant
    # response. Keep those physical messages atomic in a conservative step.
    turn.steps.append(AssistantStep(messages=[message]))


def build_turns(messages: Iterable[Message]) -> List[UserTurn]:
    """Group messages into logical turns while preserving their original order."""
    turns: List[UserTurn] = []
    current: Optional[UserTurn] = None

    for message in messages:
        if is_user_query(message):
            current = UserTurn(anchor=message)
            turns.append(current)
            continue

        if current is None:
            # Preserve a legacy assistant-only prefix as one conservative turn.
            current = UserTurn()
            turns.append(current)
        _append_to_turn(current, message)

    return turns


def _flatten_turns(turns: Iterable[UserTurn]) -> List[Message]:
    return [message for turn in turns for message in turn.messages]


def _message_tokens(messages: Iterable[Message]) -> int:
    return sum(int(message.estimated_tokens or 0) for message in messages)


def _truncate_message_group(
    messages: List[Message],
    token_budget: int,
) -> tuple[List[Message], List[str]]:
    """Return a virtual atomic group that fits ``token_budget``.

    Durable messages are never mutated. Text/context bodies and tool outputs are
    the only fields shortened. Tool IDs, names and result pairing remain intact;
    oversized tool inputs are replaced only when their fixed serialized cost
    would otherwise make the whole atomic Step impossible to return.
    """
    if token_budget <= 0 or not messages:
        return [], []
    if _message_tokens(messages) <= token_budget:
        return list(messages), []

    cloned = copy.deepcopy(messages)
    slots: List[tuple[Message, object, str, str]] = []
    truncated_ids: set[str] = set()

    # Prefer the most recent material when an atomic Step itself is oversized.
    for message in reversed(cloned):
        for part in reversed(message.parts):
            if isinstance(part, TextPart):
                slots.append((message, part, "text", part.text or ""))
                part.text = ""
            elif isinstance(part, ContextPart):
                slots.append((message, part, "abstract", part.abstract or ""))
                part.abstract = ""
            elif isinstance(part, ToolPart):
                slots.append((message, part, "tool_output", part.tool_output or ""))
                part.tool_output = ""

    # Tool inputs are structured and cannot be text-truncated safely. Preserve
    # them whenever possible, then remove oldest inputs until fixed metadata fits.
    if _message_tokens(cloned) > token_budget:
        for message in cloned:
            for part in message.parts:
                if not isinstance(part, ToolPart) or not part.tool_input:
                    continue
                part.tool_input = {}
                truncated_ids.add(message.id)
                if _message_tokens(cloned) <= token_budget:
                    break
            if _message_tokens(cloned) <= token_budget:
                break

    if _message_tokens(cloned) > token_budget:
        return [], []

    for _message, part, attribute, original in slots:
        if not original:
            continue
        used = _message_tokens(cloned)
        remaining = max(0, token_budget - used)
        if remaining <= 0:
            break
        fitted = truncate_text_to_token_budget(original, remaining)
        setattr(part, attribute, fitted)

    # Record every cleared/shortened slot, including older fields that received
    # no remaining budget at all. Keep enough tool metadata for callers to know
    # that the returned body is only a preview of the durable raw output.
    for message, part, attribute, original in slots:
        fitted = getattr(part, attribute)
        if fitted == original:
            continue
        truncated_ids.add(message.id)
        if isinstance(part, ToolPart):
            part.tool_output_truncated = True
            if part.tool_output_original_chars is None:
                part.tool_output_original_chars = len(original)
            part.tool_output_preview_chars = len(fitted)

    # The estimator and truncation helper share the same accounting, but keep a
    # defensive final guard so this function is a strict budget boundary.
    if _message_tokens(cloned) > token_budget:
        return [], []
    return cloned, sorted(truncated_ids)


def fit_active_messages_to_budget(
    messages: List[Message],
    *,
    token_budget: int,
) -> ActiveMessageBudgetPlan:
    """Build a strict, non-destructive active-message view under a token budget.

    Selection order follows the retention RFC: newest User anchor, final
    Assistant Step, checkpoint, recent Steps, then older complete Turns. An
    Assistant Step is selected or truncated atomically, so call/result transport
    messages never land on opposite sides of the returned boundary.
    """
    token_budget = max(0, int(token_budget or 0))
    if not messages or token_budget == 0:
        return ActiveMessageBudgetPlan(
            messages=[],
            estimated_tokens=0,
            dropped_message_ids=[message.id for message in messages],
        )

    total_tokens = _message_tokens(messages)
    if total_tokens <= token_budget:
        return ActiveMessageBudgetPlan(messages=list(messages), estimated_tokens=total_tokens)

    turns = build_turns(messages)
    message_indexes: Dict[int, int] = {id(message): index for index, message in enumerate(messages)}
    selected: Dict[int, Message] = {}
    truncated_ids: set[str] = set()
    used_tokens = 0

    def _select_group(
        group: List[Message],
        *,
        allow_truncate: bool,
        max_budget: Optional[int] = None,
    ) -> bool:
        nonlocal used_tokens
        if not group:
            return True
        remaining = max(0, token_budget - used_tokens)
        if max_budget is not None:
            remaining = min(remaining, max(0, int(max_budget)))
        group_tokens = _message_tokens(group)
        chosen = list(group)
        group_truncated: List[str] = []
        if group_tokens > remaining:
            if not allow_truncate or remaining <= 0:
                return False
            chosen, group_truncated = _truncate_message_group(group, remaining)
            if not chosen:
                return False

        for original, returned in zip(group, chosen, strict=True):
            selected[message_indexes[id(original)]] = returned
        used_tokens += _message_tokens(chosen)
        truncated_ids.update(group_truncated)
        return True

    latest = turns[-1]
    final_step = latest.steps[-1] if latest.steps else None
    anchor_group = [latest.anchor] if latest.anchor is not None else []
    final_group = final_step.messages if final_step is not None else []

    def _minimum_group_budget(group: List[Message]) -> Optional[int]:
        """Return the smallest budget for which a truncated group is selectable."""
        group_tokens = _message_tokens(group)
        if not group or group_tokens == 0:
            return 0

        low = 1
        high = min(group_tokens, token_budget)
        minimum: Optional[int] = None
        while low <= high:
            candidate = (low + high) // 2
            chosen, _ = _truncate_message_group(group, candidate)
            if chosen:
                minimum = candidate
                high = candidate - 1
            else:
                low = candidate + 1
        return minimum

    # Anchor and final Step are both mandatory. Reserve part of the hard budget
    # for the final Step before truncating the anchor; otherwise a long user
    # query can consume the entire budget and silently drop the latest answer.
    final_reserve = 0
    if anchor_group and final_group:
        anchor_minimum = _minimum_group_budget(anchor_group)
        final_minimum = _minimum_group_budget(final_group)
        if (
            anchor_minimum is not None
            and final_minimum is not None
            and anchor_minimum + final_minimum <= token_budget
        ):
            final_reserve = min(
                _message_tokens(final_group),
                max(final_minimum, token_budget // 2),
                token_budget - anchor_minimum,
            )

    if anchor_group:
        _select_group(
            anchor_group,
            allow_truncate=True,
            max_budget=token_budget - final_reserve,
        )
    if final_group:
        _select_group(final_group, allow_truncate=True)

    checkpoint_steps = [
        step
        for step in latest.steps[:-1]
        if any(message.message_kind == "checkpoint" for message in step.messages)
    ]
    for step in checkpoint_steps:
        if not _select_group(step.messages, allow_truncate=True):
            break

    checkpoint_ids = {id(step) for step in checkpoint_steps}
    for step in reversed(latest.steps[:-1]):
        if id(step) in checkpoint_ids:
            continue
        if not _select_group(step.messages, allow_truncate=False):
            break

    # Older Turns are useful only as a contiguous newest suffix and remain raw.
    for turn in reversed(turns[:-1]):
        if not _select_group(turn.messages, allow_truncate=False):
            break

    returned_messages = [selected[index] for index in sorted(selected)]
    returned_ids = set(selected)
    return ActiveMessageBudgetPlan(
        messages=returned_messages,
        estimated_tokens=_message_tokens(returned_messages),
        dropped_message_ids=[
            message.id for index, message in enumerate(messages) if index not in returned_ids
        ],
        truncated_message_ids=sorted(truncated_ids),
    )


def _partial_latest_turn_plan(
    older_turns: List[UserTurn],
    latest_turn: UserTurn,
    *,
    token_budget: int,
    min_raw_tail_steps: int,
) -> RetentionPlan:
    """Keep the latest user anchor and an atomic suffix of assistant steps."""
    anchor = latest_turn.anchor
    steps = latest_turn.steps
    min_tail = max(1, int(min_raw_tail_steps or 0)) if steps else 0

    retained_steps: List[AssistantStep] = list(steps[-min_tail:]) if min_tail else []
    retained_tokens = int(anchor.estimated_tokens or 0) if anchor is not None else 0
    retained_tokens += sum(step.estimated_tokens for step in retained_steps)

    # Leave room for the dedicated checkpoint summary generated alongside the
    # existing Working Memory output during Phase 2.
    # Mandatory anchor/final/tail messages are never dropped merely to satisfy
    # the budget; in that case budget_exceeded reports the lossless overflow.
    checkpoint_reserve = min(
        _CHECKPOINT_TOKEN_RESERVE,
        max(0, token_budget // 4),
    )
    raw_budget = max(0, token_budget - checkpoint_reserve)

    first_retained_step = len(steps) - len(retained_steps)
    for index in range(first_retained_step - 1, -1, -1):
        candidate = steps[index]
        if retained_tokens + candidate.estimated_tokens > raw_budget:
            break
        retained_steps.insert(0, candidate)
        retained_tokens += candidate.estimated_tokens
        first_retained_step = index

    archived_prefix = steps[:first_retained_step]
    archived_source_messages = [message for step in archived_prefix for message in step.messages]
    retained_messages: List[Message] = []
    if anchor is not None:
        retained_messages.append(anchor)
    retained_messages.extend(message for step in retained_steps for message in step.messages)

    archive_messages = _flatten_turns(older_turns)
    if archived_source_messages:
        # A copy of the anchor (same message id) makes the partial archive
        # understandable during Phase 2. Context assembly stable-deduplicates it
        # against the retained live anchor while the archive is pending/failed.
        if anchor is not None:
            archive_messages.append(anchor)
        archive_messages.extend(archived_source_messages)

    source_ids = [message.id for message in archived_source_messages]
    active_tokens = _message_tokens(retained_messages)
    return RetentionPlan(
        archive_messages=archive_messages,
        retained_messages=retained_messages,
        turn_anchor=anchor,
        checkpoint_source_message_ids=source_ids,
        raw_tail_start_message_id=(
            retained_steps[0].messages[0].id
            if retained_steps and retained_steps[0].messages
            else None
        ),
        estimated_active_tokens=active_tokens,
        budget_exceeded=active_tokens > token_budget,
        partial_turn=bool(archived_source_messages),
    )


def plan_retention(
    messages: List[Message],
    *,
    keep_recent_turn_count: int,
    token_budget: int,
    min_raw_tail_steps: int = 1,
) -> RetentionPlan:
    """Plan Turn-aware retention under both a turn count and token budget.

    Oldest complete turns are archived first. If the newest turn alone exceeds
    the budget, its user anchor and an atomic suffix of assistant steps remain
    live while early steps become checkpoint sources.
    """
    keep_recent_turn_count = max(0, int(keep_recent_turn_count or 0))
    token_budget = max(0, int(token_budget or 0))
    turns = build_turns(messages)

    if not turns or keep_recent_turn_count == 0:
        return RetentionPlan(archive_messages=list(messages), retained_messages=[])

    selected_start = max(0, len(turns) - keep_recent_turn_count)
    retained_turns = turns[selected_start:]

    # Enforce the token budget by evicting complete oldest turns. Never evict
    # the newest turn here; it is handled by the partial-turn fallback below.
    while (
        len(retained_turns) > 1 and sum(t.estimated_tokens for t in retained_turns) > token_budget
    ):
        selected_start += 1
        retained_turns = turns[selected_start:]

    older_turns = turns[:selected_start]
    latest_turn = retained_turns[-1]
    if latest_turn.estimated_tokens > token_budget:
        if latest_turn.anchor is None:
            # A legacy assistant-only prefix has no stable User anchor for a
            # partial-Turn checkpoint. Archive it as a complete unit so Phase 2
            # can summarize it without producing an invalid checkpoint plan.
            return RetentionPlan(
                archive_messages=_flatten_turns([*older_turns, latest_turn]),
                retained_messages=[],
            )
        return _partial_latest_turn_plan(
            older_turns,
            latest_turn,
            token_budget=token_budget,
            min_raw_tail_steps=min_raw_tail_steps,
        )

    retained_messages = _flatten_turns(retained_turns)
    return RetentionPlan(
        archive_messages=_flatten_turns(older_turns),
        retained_messages=retained_messages,
        estimated_active_tokens=_message_tokens(retained_messages),
    )


__all__ = [
    "AssistantStep",
    "ActiveMessageBudgetPlan",
    "RETENTION_MODE_TURN_BUDGET",
    "RetentionPlan",
    "UserTurn",
    "build_turns",
    "fit_active_messages_to_budget",
    "is_tool_transport",
    "is_user_query",
    "plan_retention",
]
