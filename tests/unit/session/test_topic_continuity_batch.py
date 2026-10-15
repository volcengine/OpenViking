# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0

from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from typing import Any

import pytest
from pydantic import ValidationError

from openviking.message import Message, TextPart
from openviking.session.extraction_batch import (
    ExtractionBatchLimits,
    plan_extraction_batches,
    plan_long_term_extraction_batches,
    should_batch_long_term_extraction,
)
from openviking.session.retention import build_turns
from openviking.session.topic_continuity import (
    HeuristicContinuityChecker,
    SmallModelContinuityChecker,
    TopicContinuityOptions,
    parse_same_topic,
    resolve_topic_continuity_check,
    should_skip_topic_group,
    turn_text,
)
from openviking_cli.utils.config.memory_config import MemoryConfig, TopicContinuityConfig


def _message(
    message_id: str,
    role: str = "user",
    text: str = "content",
    created_at: str | None = None,
) -> Message:
    return Message(
        id=message_id,
        role=role,
        parts=[TextPart(text)],
        created_at=created_at,
    )


def _conversation(*turns: tuple[str, str]) -> list[Message]:
    messages: list[Message] = []
    for index, (user_text, assistant_text) in enumerate(turns, start=1):
        messages.append(_message(f"u{index}", text=user_text))
        messages.append(_message(f"a{index}", role="assistant", text=assistant_text))
    return messages


def _enabled(**overrides) -> TopicContinuityOptions:
    values = {
        "enabled": True,
        "max_turns": 10,
        "overlap_turns": 1,
        "min_turns_to_split": 2,
        "skip_max_user_chars": 0,
    }
    values.update(overrides)
    return TopicContinuityOptions(**values)


def test_memory_config_defaults_disable_topic_continuity():
    config = MemoryConfig()
    assert config.topic_continuity.enabled is False
    assert config.topic_continuity.max_turns == 10
    assert config.topic_continuity.overlap_turns == 1
    assert config.topic_continuity.min_turns_to_split == 2
    assert config.topic_continuity.time_gap_seconds == 0
    assert config.topic_continuity.embedding_similarity_threshold == 0.45
    assert config.topic_continuity.skip_max_user_chars == 0


def test_memory_config_rejects_unknown_topic_continuity_keys():
    with pytest.raises(ValidationError):
        TopicContinuityConfig(enabled=True, unknown_field=1)


def test_should_batch_long_term_extraction_is_independent_of_size_limits():
    limits = ExtractionBatchLimits()
    assert should_batch_long_term_extraction(limits, None) is False
    assert should_batch_long_term_extraction(limits, _enabled()) is True
    assert should_batch_long_term_extraction(
        ExtractionBatchLimits(max_messages=2), None
    ) is True


def test_topic_planner_disabled_matches_size_planner():
    messages = _conversation(("q1", "a1"), ("q2", "a2"))
    limits = ExtractionBatchLimits(max_messages=2)
    assert plan_long_term_extraction_batches(messages, limits) == plan_extraction_batches(
        messages, limits
    )
    assert plan_long_term_extraction_batches(
        messages, limits, topic=_enabled(enabled=False)
    ) == plan_extraction_batches(messages, limits)


def test_topic_planner_below_min_turns_does_not_split():
    messages = _conversation(("only", "one"))
    batches = plan_long_term_extraction_batches(
        messages,
        ExtractionBatchLimits(),
        topic=_enabled(min_turns_to_split=2),
        continuity_check=lambda _previous, _current: False,
    )
    assert [[message.id for message in batch.messages] for batch in batches] == [
        ["u1", "a1"]
    ]


def test_topic_planner_keeps_continuous_turns_together():
    messages = _conversation(("login", "ok"), ("still login", "fixed"))
    batches = plan_long_term_extraction_batches(
        messages,
        ExtractionBatchLimits(),
        topic=_enabled(),
        continuity_check=lambda _previous, _current: True,
    )
    assert [[message.id for message in batch.messages] for batch in batches] == [
        ["u1", "a1", "u2", "a2"]
    ]
    assert batches[0].owned_message_ids == frozenset({"u1", "a1", "u2", "a2"})


def test_topic_planner_splits_on_topic_change_without_overlap():
    messages = _conversation(("login failed", "check logs"), ("dinner", "noodles"))

    def split_on_dinner(previous, current) -> bool:
        return "dinner" not in turn_text(current)

    batches = plan_long_term_extraction_batches(
        messages,
        ExtractionBatchLimits(),
        topic=_enabled(overlap_turns=1),
        continuity_check=split_on_dinner,
    )
    assert [[message.id for message in batch.messages] for batch in batches] == [
        ["u1", "a1"],
        ["u2", "a2"],
    ]
    assert batches[0].owned_message_ids == frozenset({"u1", "a1"})
    assert batches[1].owned_message_ids == frozenset({"u2", "a2"})


def test_topic_planner_applies_overlap_only_on_max_turns_cuts():
    messages = _conversation(
        ("t1", "a1"),
        ("t2", "a2"),
        ("t3", "a3"),
        ("t4", "a4"),
        ("t5", "a5"),
    )
    batches = plan_long_term_extraction_batches(
        messages,
        ExtractionBatchLimits(),
        topic=_enabled(max_turns=2, overlap_turns=1),
        continuity_check=lambda _previous, _current: True,
    )
    assert [[message.id for message in batch.messages] for batch in batches] == [
        ["u1", "a1", "u2", "a2"],
        ["u2", "a2", "u3", "a3"],
        ["u3", "a3", "u4", "a4"],
        ["u4", "a4", "u5", "a5"],
    ]
    assert batches[0].owned_message_ids == frozenset({"u1", "a1", "u2", "a2"})
    assert batches[1].owned_message_ids == frozenset({"u3", "a3"})
    assert batches[2].owned_message_ids == frozenset({"u4", "a4"})
    assert batches[3].owned_message_ids == frozenset({"u5", "a5"})


def test_topic_planner_treats_checker_errors_as_continuous():
    messages = _conversation(("q1", "a1"), ("q2", "a2"))

    def boom(_previous, _current) -> bool:
        raise RuntimeError("classifier failed")

    batches = plan_long_term_extraction_batches(
        messages,
        ExtractionBatchLimits(),
        topic=_enabled(),
        continuity_check=boom,
    )
    assert [[message.id for message in batch.messages] for batch in batches] == [
        ["u1", "a1", "u2", "a2"]
    ]


def test_topic_planner_skips_short_user_groups():
    messages = _conversation(("hi", "hello"), ("login failed", "restart the service"))

    def split_after_hi(previous, current) -> bool:
        return "login" not in turn_text(current)

    batches = plan_long_term_extraction_batches(
        messages,
        ExtractionBatchLimits(),
        topic=_enabled(skip_max_user_chars=3),
        continuity_check=split_after_hi,
    )
    assert [[message.id for message in batch.messages] for batch in batches] == [
        ["u2", "a2"]
    ]


def test_topic_planner_returns_empty_for_no_messages():
    assert plan_long_term_extraction_batches([], ExtractionBatchLimits(), topic=_enabled()) == []


def test_topic_planner_applies_size_limits_after_grouping():
    messages = _conversation(("same", "one"), ("same", "two"))
    batches = plan_long_term_extraction_batches(
        messages,
        ExtractionBatchLimits(max_messages=2),
        topic=_enabled(),
        continuity_check=lambda _previous, _current: True,
    )
    assert [[message.id for message in batch.messages] for batch in batches] == [
        ["u1", "a1"],
        ["u2", "a2"],
    ]
    assert all(batch.owned_message_ids == frozenset(message.id for message in batch.messages)
               for batch in batches)


def test_heuristic_checker_splits_on_time_gap():
    start = datetime(2026, 1, 1, tzinfo=timezone.utc)
    messages = [
        _message("u1", text="first", created_at=start.isoformat()),
        _message("a1", role="assistant", text="ok", created_at=start.isoformat()),
        _message(
            "u2",
            text="later",
            created_at=(start + timedelta(seconds=120)).isoformat(),
        ),
        _message(
            "a2",
            role="assistant",
            text="ok",
            created_at=(start + timedelta(seconds=121)).isoformat(),
        ),
    ]
    turns = build_turns(messages)
    checker = HeuristicContinuityChecker(time_gap_seconds=60)
    assert checker(turns[0], turns[1]) is False


def test_heuristic_checker_uses_embedding_threshold():
    messages = _conversation(("alpha alpha", "ok"), ("beta beta", "ok"))
    turns = build_turns(messages)

    def embed(text: str) -> list[float]:
        return [1.0, 0.0] if "alpha" in text else [0.0, 1.0]

    checker = HeuristicContinuityChecker(
        embedding_similarity_threshold=0.5,
        embedder=embed,
    )
    assert checker(turns[0], turns[1]) is False


def test_heuristic_checker_fail_open_without_embedder():
    messages = _conversation(("alpha", "ok"), ("beta", "ok"))
    turns = build_turns(messages)
    checker = HeuristicContinuityChecker()
    assert checker(turns[0], turns[1]) is True


def test_should_skip_topic_group_respects_zero_limit():
    turns = build_turns(_conversation(("hi", "hello")))
    assert should_skip_topic_group(turns, 0) is False
    assert should_skip_topic_group(turns, 2) is True


def test_openviking_config_loads_topic_continuity_and_rejects_unknown_keys():
    from openviking_cli.utils.config.open_viking_config import OpenVikingConfig

    default_config = OpenVikingConfig.from_dict({})
    assert default_config.memory.topic_continuity.enabled is False
    assert default_config.memory.topic_continuity.max_turns == 10

    enabled_config = OpenVikingConfig.from_dict(
        {"memory": {"topic_continuity": {"enabled": True, "max_turns": 3}}}
    )
    assert enabled_config.memory.topic_continuity.enabled is True
    assert enabled_config.memory.topic_continuity.max_turns == 3
    assert enabled_config.memory.topic_continuity.time_gap_seconds == 0

    with pytest.raises(ValueError):
        OpenVikingConfig.from_dict(
            {"memory": {"topic_continuity": {"enabled": True, "unknown_field": 1}}}
        )


def test_parse_same_topic_fail_open_on_unclear_text():
    assert parse_same_topic('{"same_topic": false}') is False
    assert parse_same_topic("yes") is True
    assert parse_same_topic("the model wandered off") is True


class _Planner:
    def __init__(self, answers: list[Any]) -> None:
        self.answers = list(answers)
        self.prompts: list[str] = []
        self.kwargs: dict[str, Any] = {}

    async def get_completion_async(self, prompt: str, **kwargs: Any) -> str:
        self.prompts.append(prompt)
        self.kwargs = kwargs
        answer = self.answers.pop(0)
        if isinstance(answer, Exception):
            raise answer
        return answer


class _Resolver:
    def __init__(self, *, separate: bool, planner: _Planner | None = None) -> None:
        self.separate = separate
        self.planner = planner
        self.vlm_calls = 0
        self.planner_calls = 0

    async def has_separate_query_planner(self, account_id: str) -> bool:
        del account_id
        return self.separate

    async def get_vlm(self, account_id: str) -> Any:
        del account_id
        self.vlm_calls += 1
        raise AssertionError("extraction model must not classify topics")

    async def get_query_planner(self, account_id: str) -> Any:
        del account_id
        self.planner_calls += 1
        return self.planner


@pytest.mark.asyncio
async def test_small_model_same_topic_stays_one_group():
    planner = _Planner(['{"same_topic": true}'])
    resolver = _Resolver(separate=True, planner=planner)
    messages = _conversation(("login", "ok"), ("still login", "fixed"))
    checker = await resolve_topic_continuity_check(
        messages=messages,
        resolver=resolver,
        account_id="default",
    )
    batches = plan_long_term_extraction_batches(
        messages,
        ExtractionBatchLimits(),
        topic=_enabled(),
        continuity_check=checker,
    )
    assert [[message.id for message in batch.messages] for batch in batches] == [
        ["u1", "a1", "u2", "a2"]
    ]
    assert resolver.vlm_calls == 0
    assert resolver.planner_calls == 1
    assert planner.kwargs["thinking"] is False
    assert planner.kwargs["max_tokens"] == 64


@pytest.mark.asyncio
async def test_small_model_topic_change_splits_without_overlap():
    planner = _Planner(['{"same_topic": false}'])
    resolver = _Resolver(separate=True, planner=planner)
    messages = _conversation(("login failed", "check logs"), ("dinner", "noodles"))
    checker = await resolve_topic_continuity_check(
        messages=messages,
        resolver=resolver,
        account_id="default",
    )
    batches = plan_long_term_extraction_batches(
        messages,
        ExtractionBatchLimits(),
        topic=_enabled(overlap_turns=1),
        continuity_check=checker,
    )
    assert [[message.id for message in batch.messages] for batch in batches] == [
        ["u1", "a1"],
        ["u2", "a2"],
    ]
    assert batches[1].owned_message_ids == frozenset({"u2", "a2"})


@pytest.mark.asyncio
async def test_small_model_failure_stays_together():
    planner = _Planner([RuntimeError("timeout"), "not json"])
    resolver = _Resolver(separate=True, planner=planner)
    messages = _conversation(("q1", "a1"), ("q2", "a2"), ("q3", "a3"))
    checker = await resolve_topic_continuity_check(
        messages=messages,
        resolver=resolver,
        account_id="default",
    )
    batches = plan_long_term_extraction_batches(
        messages,
        ExtractionBatchLimits(),
        topic=_enabled(),
        continuity_check=checker,
    )
    assert [[message.id for message in batch.messages] for batch in batches] == [
        ["u1", "a1", "u2", "a2", "u3", "a3"]
    ]


@pytest.mark.asyncio
async def test_missing_query_planner_does_not_call_extraction_model():
    resolver = _Resolver(separate=False, planner=_Planner(['{"same_topic": false}']))
    messages = _conversation(("login", "ok"), ("dinner", "noodles"))
    checker = await resolve_topic_continuity_check(
        messages=messages,
        resolver=resolver,
        account_id="default",
    )
    assert checker is None
    assert resolver.vlm_calls == 0
    assert resolver.planner_calls == 0


@pytest.mark.asyncio
async def test_account_vlm_alone_is_not_a_topic_planner():
    from openviking.config.vlm import AccountVLMProvider

    class Host:
        def __init__(self, view: Any) -> None:
            self.view = view

        async def _view(self, account_id: str) -> Any:
            del account_id
            return self.view

    only_vlm = SimpleNamespace(
        account=SimpleNamespace(query_planner=None, vlm=object()),
        cluster=SimpleNamespace(query_planner=None),
    )
    host = Host(only_vlm)
    assert await AccountVLMProvider.has_dedicated_query_planner(host, "acct") is True
    assert await AccountVLMProvider.has_separate_query_planner(host, "acct") is False

    class Planner:
        def _has_any_config(self) -> bool:
            return True

    cluster_planner = SimpleNamespace(
        account=SimpleNamespace(query_planner=None, vlm=None),
        cluster=SimpleNamespace(query_planner=Planner()),
    )
    host = Host(cluster_planner)
    assert await AccountVLMProvider.has_separate_query_planner(host, "acct") is True


@pytest.mark.asyncio
async def test_small_model_checker_truncates_turn_text():
    long_text = "x" * 5000
    turns = build_turns(_conversation((long_text, "ok"), ("next", "ok")))
    planner = _Planner(['{"same_topic": true}'])
    checker = SmallModelContinuityChecker(planner.get_completion_async)
    assert await checker.same_topic(turns[0], turns[1]) is True
    assert len(planner.prompts[0]) < 6000
