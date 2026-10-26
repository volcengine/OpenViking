# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0
"""Topic-continuity helpers for long-term memory extraction batching."""

from __future__ import annotations

import logging
from datetime import datetime
from typing import Any, Awaitable, Callable, Iterable, List, Optional, Protocol, Sequence

from openviking.message import Message
from openviking.session.retention import UserTurn, build_turns, is_user_query
from openviking.utils.time_utils import parse_iso_datetime
from openviking_cli.utils.config.memory_config import TopicContinuityConfig
from openviking_cli.utils.llm import parse_json_from_response

TURN_TEXT_CHAR_CAP = 2000
logger = logging.getLogger(__name__)

_SAME_TOPIC_TRUE = {"true", "yes", "same", "y"}
_SAME_TOPIC_FALSE = {"false", "no", "different", "n"}


class ContinuityChecker(Protocol):
    def __call__(self, previous: UserTurn, current: UserTurn) -> bool: ...


def turn_text(turn: UserTurn, char_cap: int = TURN_TEXT_CHAR_CAP) -> str:
    """Concatenate text parts from a turn, truncated for cheap classification."""
    chunks: List[str] = []
    remaining = max(0, int(char_cap or 0))
    for message in turn.messages:
        text = message.content or ""
        if not text or remaining <= 0:
            continue
        chunks.append(text[:remaining])
        remaining -= len(chunks[-1])
        if remaining <= 0:
            break
    return "".join(chunks)


def user_query_char_count(turns: Iterable[UserTurn]) -> int:
    total = 0
    for turn in turns:
        for message in turn.messages:
            if is_user_query(message):
                total += len(message.content or "")
    return total


def should_skip_topic_group(
    owned_turns: Sequence[UserTurn],
    skip_max_user_chars: int,
) -> bool:
    """Return True when owned user-query text is too short to extract."""
    limit = int(skip_max_user_chars or 0)
    if limit <= 0:
        return False
    return user_query_char_count(owned_turns) <= limit


def _message_created_at(message: Optional[Message]) -> Optional[datetime]:
    if message is None or not message.created_at:
        return None
    raw = message.created_at
    if isinstance(raw, datetime):
        return raw
    try:
        return parse_iso_datetime(str(raw))
    except (TypeError, ValueError):
        return None


def _turn_anchor_time(turn: UserTurn) -> Optional[datetime]:
    if turn.anchor is not None:
        parsed = _message_created_at(turn.anchor)
        if parsed is not None:
            return parsed
    for message in turn.messages:
        parsed = _message_created_at(message)
        if parsed is not None:
            return parsed
    return None


def _align_datetimes(left: datetime, right: datetime) -> tuple[datetime, datetime]:
    if left.tzinfo is None and right.tzinfo is not None:
        left = left.replace(tzinfo=right.tzinfo)
    elif left.tzinfo is not None and right.tzinfo is None:
        right = right.replace(tzinfo=left.tzinfo)
    return left, right


def default_continuity_check(options: TopicContinuityConfig) -> ContinuityChecker:
    """Without a dedicated topic model, only the configured time gap splits turns."""

    def check(previous: UserTurn, current: UserTurn) -> bool:
        if options.time_gap_seconds <= 0:
            return True
        previous_time, current_time = _turn_anchor_time(previous), _turn_anchor_time(current)
        if previous_time is None or current_time is None:
            return True
        previous_time, current_time = _align_datetimes(previous_time, current_time)
        return abs((current_time - previous_time).total_seconds()) <= options.time_gap_seconds

    return check


def parse_same_topic(response: Any) -> bool:
    """Return whether a model reply says the turns share a topic.

    An unclear reply stays continuous so a bad checker does not split one episode.
    """
    parsed = parse_json_from_response(str(response or ""))
    if isinstance(parsed, dict) and "same_topic" in parsed:
        value = parsed["same_topic"]
        if isinstance(value, bool):
            return value
        if isinstance(value, str):
            normalized = value.strip().lower()
            if normalized in _SAME_TOPIC_TRUE:
                return True
            if normalized in _SAME_TOPIC_FALSE:
                return False
    text = " ".join(str(response or "").split()).lower()
    if text in _SAME_TOPIC_TRUE:
        return True
    if text in _SAME_TOPIC_FALSE:
        return False
    return True


class SmallModelContinuityChecker:
    """Ask a cheap model whether two neighboring turns are still one topic."""

    def __init__(self, complete: Callable[..., Awaitable[Any]]) -> None:
        self._complete = complete

    async def same_topic(self, previous: UserTurn, current: UserTurn) -> bool:
        prompt = (
            "Decide whether these two neighboring user turns are still the same topic.\n"
            'Reply with JSON only: {"same_topic": true} or {"same_topic": false}.\n\n'
            f"Previous turn:\n{turn_text(previous)}\n\n"
            f"Next turn:\n{turn_text(current)}\n"
        )
        try:
            response = await self._complete(prompt, thinking=False, max_tokens=64)
        except Exception:
            logger.warning("Topic continuity check failed; keeping turns together", exc_info=True)
            return True
        return parse_same_topic(response)


def _turn_id(turn: UserTurn) -> str:
    anchor = turn.anchor
    if anchor is not None and anchor.id:
        return str(anchor.id)
    if turn.messages:
        return str(turn.messages[0].id)
    return ""


async def _separate_query_planner(resolver: Any, account_id: str) -> Any:
    """Return a dedicated query planner, never the extraction model."""
    if resolver is None:
        return None
    probe = getattr(resolver, "has_separate_query_planner", None)
    if probe is None:
        return None
    try:
        separate = probe(account_id)
        if isinstance(separate, Awaitable):
            separate = await separate
    except Exception:
        logger.warning("Topic continuity could not read the query planner", exc_info=True)
        return None
    if not separate:
        return None
    try:
        return await resolver.get_query_planner(account_id)
    except Exception:
        logger.warning("Topic continuity could not open the query planner", exc_info=True)
        return None


async def resolve_topic_continuity_check(
    *,
    messages: Sequence[Message],
    resolver: Any,
    account_id: str,
) -> Optional[ContinuityChecker]:
    """Precompute same-topic decisions from the dedicated query planner.

    Returns None when no separate planner is configured. The caller then keeps
    the time-gap and max_turns rules, and does not call the extraction model.
    """
    planner = await _separate_query_planner(resolver, account_id)
    if planner is None:
        return None
    complete = getattr(planner, "get_completion_async", None)
    if complete is None:
        return None
    turns = build_turns(list(messages))
    if len(turns) < 2:
        return None
    checker = SmallModelContinuityChecker(complete)
    decisions: dict[tuple[str, str], bool] = {}
    for previous, current in zip(turns, turns[1:]):
        decisions[(_turn_id(previous), _turn_id(current))] = await checker.same_topic(
            previous, current
        )

    def check(previous: UserTurn, current: UserTurn) -> bool:
        return decisions.get((_turn_id(previous), _turn_id(current)), True)

    return check


__all__ = [
    "ContinuityChecker",
    "SmallModelContinuityChecker",
    "TURN_TEXT_CHAR_CAP",
    "default_continuity_check",
    "parse_same_topic",
    "resolve_topic_continuity_check",
    "should_skip_topic_group",
    "turn_text",
    "user_query_char_count",
]
