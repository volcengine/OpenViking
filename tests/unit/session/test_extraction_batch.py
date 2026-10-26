# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0

from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from openviking.message import Message, TextPart
from openviking.models.vlm.base import ToolCall, VLMResponse
from openviking.session.checkpoints import CheckpointRequest
from openviking.session.extraction_batch import (
    ExtractionBatchLimits,
    plan_extraction_batches,
    resolve_extraction_batch_limits,
)
from openviking.session.session import Session
from openviking.session.topic_continuity import turn_text
from openviking.utils.token_estimation import estimate_text_tokens
from openviking_cli.utils.config.memory_config import MemoryConfig, TopicContinuityConfig


def _message(message_id: str, role: str = "user", text: str = "content") -> Message:
    return Message(id=message_id, role=role, parts=[TextPart(text)])


@pytest.mark.asyncio
@pytest.mark.parametrize("failover", [False, True])
@pytest.mark.parametrize("configured_limit", [None, 128, 20000])
@pytest.mark.parametrize("path", ["create", "update", "fallback", "retry"])
async def test_summary_output_budget_respects_configured_limit(
    monkeypatch, configured_limit, path, failover
):
    monkeypatch.setattr(
        "openviking.session.session.get_openviking_config",
        lambda: SimpleNamespace(
            output_language_override="en",
            memory=SimpleNamespace(summary_budget_retry_enabled=True),
        ),
    )
    update = VLMResponse(
        tool_calls=[
            ToolCall(
                id="1",
                name="update_working_memory",
                arguments={"sections": {"Current State": {"op": "UPDATE", "content": "compact"}}},
            )
        ]
    )
    responses = {
        "create": ["compact"],
        "update": [update],
        "fallback": ["no tool call", "compact"],
        "retry": ["x" * 12000, "x" * 12000, "compact"],
    }
    completion = AsyncMock(side_effect=responses[path])
    vlm = SimpleNamespace(
        is_available=lambda: True,
        get_completion_async=completion,
        max_tokens=configured_limit,
    )
    if failover:
        from openviking.models.vlm.backends.openai_vlm import OpenAIVLM
        from openviking.models.vlm.base import FailoverVLM

        primary = OpenAIVLM({"model": "gpt-4o", "api_key": "test", "max_tokens": configured_limit})
        backup = OpenAIVLM({"model": "gpt-4o", "api_key": "test", "max_tokens": configured_limit})
        vlm = FailoverVLM(primary, backup)
        vlm.is_available = lambda: True
        vlm.get_completion_async = completion
    session = Session(viking_fs=None)
    session._get_vlm_config = AsyncMock(return_value=vlm)

    await session._generate_archive_summary_async(
        [_message("u1")],
        latest_archive_overview="## Current State\nPrevious work"
        if path in ("update", "fallback")
        else "",
        session_window_context=10000,
    )

    assert completion.await_count == len(responses[path])
    for index, call in enumerate(completion.await_args_list):
        kwargs = call.kwargs
        input_tokens = estimate_text_tokens(kwargs.get("prompt")) + sum(
            estimate_text_tokens(message["content"]) + 4 for message in kwargs.get("messages") or []
        )
        if kwargs.get("prompt"):
            input_tokens += 4
        if kwargs.get("tools"):
            import json

            input_tokens += estimate_text_tokens(json.dumps(kwargs["tools"], ensure_ascii=False))
        automatic_limit = 2500 if path == "retry" and index == 2 else 10000 - input_tokens
        expected_limit = (
            automatic_limit if configured_limit is None else min(automatic_limit, configured_limit)
        )
        assert kwargs["max_tokens"] == expected_limit
        assert input_tokens + kwargs["max_tokens"] <= 10000


@pytest.mark.asyncio
async def test_session_context_reserves_summary_before_active_messages():
    session = Session(viking_fs=None)
    summary = "Earlier decisions " * 10
    session._collect_session_context_components = AsyncMock(
        return_value={
            "messages": [_message("u1", text="latest question " * 100)],
            "latest_archive": {
                "overview": summary,
                "overview_tokens": estimate_text_tokens(summary),
            },
            "total_archives": 1,
            "failed_archives": 0,
        }
    )
    result = await session.get_session_context(token_budget=100)
    assert result["latest_archive_overview"] == summary
    assert result["messages"]
    assert result["estimatedTokens"] <= 100


@pytest.mark.asyncio
async def test_rolling_summary_stops_when_generated_summary_fills_material_window(monkeypatch):
    monkeypatch.setattr(
        "openviking.session.session._estimate_summary_template_tokens", lambda *args: 0
    )
    session = Session(viking_fs=None)
    session._get_vlm_config = AsyncMock(return_value=SimpleNamespace(is_available=lambda: True))
    generate = AsyncMock(return_value="x" * 280)
    session._generate_archive_summary_async = generate
    with pytest.raises(ValueError, match="summary exceeds the rolling material budget"):
        await session._generate_archive_summary_with_batching(
            [_message("u1", text="x" * 200), _message("u2", text="x" * 200)],
            latest_archive_overview="",
            session_window_context=100,
        )
    assert generate.await_count == 1


@pytest.mark.asyncio
async def test_rolling_summary_splits_a_single_oversized_message(monkeypatch):
    monkeypatch.setattr(
        "openviking.session.session._estimate_summary_template_tokens", lambda *args: 0
    )
    session = Session(viking_fs=None)
    session._get_vlm_config = AsyncMock(return_value=SimpleNamespace(is_available=lambda: True))
    chunks = []

    async def generate(messages, **kwargs):
        chunks.append(messages[0].content)
        assert messages[0].created_at == "2026-10-28T23:59:00Z"
        assert (
            sum(message.estimated_tokens for message in messages)
            + estimate_text_tokens(kwargs["latest_archive_overview"])
            <= 70
        )
        return "summary"

    session._generate_archive_summary_async = generate
    source = "word " * 200
    message = _message("u1", text=source)
    message.created_at = "2026-10-28T23:59:00Z"
    await session._generate_archive_summary_with_batching(
        [message], latest_archive_overview="", session_window_context=100
    )
    assert len(chunks) > 1
    assert "".join(chunks) == source


@pytest.mark.asyncio
async def test_rolling_summary_includes_compression_template_in_material_budget(monkeypatch):
    from openviking.session.session import _estimate_summary_template_tokens

    session = Session(viking_fs=None)
    session._get_vlm_config = AsyncMock(return_value=SimpleNamespace(is_available=lambda: True))
    prior = "## Current State\n" + "x" * 8000
    source = "x" * 14000
    chunks = []

    async def generate(messages, latest_archive_overview="", **kwargs):
        chunks.extend(message.content for message in messages)
        material_tokens = sum(message.estimated_tokens for message in messages)
        material_tokens += estimate_text_tokens(latest_archive_overview)
        material_tokens += _estimate_summary_template_tokens(latest_archive_overview)
        assert material_tokens <= int(8192 * 0.7)
        return "## Current State\ncompact"

    session._generate_archive_summary_async = AsyncMock(side_effect=generate)
    await session._generate_archive_summary_with_batching(
        [_message("u1", text=source)],
        latest_archive_overview=prior,
        session_window_context=8192,
    )
    assert len(chunks) > 1
    assert "".join(chunks) == source


@pytest.mark.asyncio
async def test_rolling_summary_with_real_update_template_fits_small_window(monkeypatch):
    monkeypatch.setattr(
        "openviking.session.session.get_openviking_config",
        lambda: SimpleNamespace(
            output_language_override="en",
            memory=SimpleNamespace(summary_budget_retry_enabled=True),
        ),
    )
    completion = AsyncMock(
        return_value=VLMResponse(
            tool_calls=[
                ToolCall(
                    id="1",
                    name="update_working_memory",
                    arguments={
                        "sections": {"Current State": {"op": "UPDATE", "content": "compact"}}
                    },
                )
            ]
        )
    )
    session = Session(viking_fs=None)
    session._get_vlm_config = AsyncMock(
        return_value=SimpleNamespace(is_available=lambda: True, get_completion_async=completion)
    )
    await session._generate_archive_summary_with_batching(
        [_message("u1", text="x" * 14000)],
        latest_archive_overview="## Current State\n" + "x" * 8000,
        session_window_context=8192,
    )
    assert completion.await_count > 1
    assert all(call.kwargs["max_tokens"] > 0 for call in completion.await_args_list)


@pytest.mark.asyncio
@pytest.mark.parametrize("path", ["missing_tool", "invalid_arguments", "checkpoint"])
async def test_update_fallback_and_checkpoint_check_final_summary_budget(monkeypatch, path):
    session = Session(viking_fs=None)
    checkpoint = path == "checkpoint"
    prior = "## Current State\n" + "x" * 12000
    response = (
        VLMResponse(
            tool_calls=[
                ToolCall(
                    id="1",
                    name="update_working_memory",
                    arguments={
                        "sections": {"Current State": {"op": "KEEP"}},
                        "checkpoint_summaries": ["checkpoint"],
                    },
                )
            ]
        )
        if checkpoint
        else "no tool call"
    )
    if path == "invalid_arguments":
        response = VLMResponse(
            tool_calls=[ToolCall(id="1", name="update_working_memory", arguments={})]
        )
    completion = AsyncMock(
        side_effect=[response, "compact"] if checkpoint else [response, "x" * 12000, "compact"]
    )
    vlm = SimpleNamespace(is_available=lambda: True, get_completion_async=completion)
    session._get_vlm_config = AsyncMock(return_value=vlm)
    monkeypatch.setattr(
        "openviking.session.session.get_openviking_config",
        lambda: SimpleNamespace(
            output_language_override="en",
            memory=SimpleNamespace(summary_budget_retry_enabled=True),
        ),
    )
    result = await session._generate_archive_summary_async(
        [_message("u1")],
        latest_archive_overview=prior,
        session_window_context=10000,
        checkpoint_requests=[
            CheckpointRequest(
                turn_anchor_message_id="u1",
                source_message_ids=("u1",),
                retained_message_token_budget=100,
                estimated_active_tokens=100,
            )
        ]
        if checkpoint
        else None,
    )
    assert (result.overview if checkpoint else result) == "compact"
    for call in completion.await_args_list:
        kwargs = call.kwargs
        input_tokens = estimate_text_tokens(kwargs.get("prompt")) + sum(
            estimate_text_tokens(message["content"]) + 4 for message in kwargs.get("messages") or []
        )
        if kwargs.get("prompt"):
            input_tokens += 4
        if kwargs.get("tools"):
            import json

            input_tokens += estimate_text_tokens(json.dumps(kwargs["tools"], ensure_ascii=False))
        assert kwargs["max_tokens"] == 10000 - input_tokens
    if checkpoint:
        assert result.checkpoint_summaries == ("checkpoint",)
    assert completion.await_args_list[-1].kwargs["messages"][0]["role"] == "system"
    if checkpoint:
        from openviking.session.working_memory import merge_wm_sections

        expected_overview = merge_wm_sections(prior, {"Current State": {"op": "KEEP"}})
    else:
        expected_overview = "x" * 12000
    assert completion.await_args_list[-1].kwargs["messages"][1]["content"] == expected_overview


@pytest.mark.asyncio
async def test_update_budget_retry_uses_complete_merged_summary(monkeypatch):
    from openviking.session.working_memory import merge_wm_sections

    prior = "## Current State\n" + "x" * 12000 + "\n\n## Key Facts & Decisions\n- Old fact"
    operations = {
        "Current State": {"op": "KEEP"},
        "Key Facts & Decisions": {"op": "APPEND", "items": ["New fact"]},
    }
    merged = merge_wm_sections(prior, operations)
    completion = AsyncMock(
        side_effect=[
            VLMResponse(
                tool_calls=[
                    ToolCall(
                        id="1",
                        name="update_working_memory",
                        arguments={"sections": operations},
                    )
                ]
            ),
            "compact",
        ]
    )
    session = Session(viking_fs=None)
    session._get_vlm_config = AsyncMock(
        return_value=SimpleNamespace(is_available=lambda: True, get_completion_async=completion)
    )
    monkeypatch.setattr(
        "openviking.session.session.get_openviking_config",
        lambda: SimpleNamespace(
            output_language_override="en",
            memory=SimpleNamespace(summary_budget_retry_enabled=True),
        ),
    )

    result = await session._generate_archive_summary_async(
        [_message("u1", text="New fact")],
        latest_archive_overview=prior,
        session_window_context=10000,
    )

    assert result == "compact"
    assert completion.await_count == 2
    retry = completion.await_args_list[-1].kwargs
    assert retry["messages"][1]["content"] == merged
    assert "Old fact" in merged and "New fact" in merged
    assert not retry.get("tools")
    assert "complete Working Memory" in retry["messages"][0]["content"]


@pytest.mark.asyncio
async def test_fallback_rejects_input_larger_than_model_window():
    session = Session(viking_fs=None)
    completion = AsyncMock()
    session._get_vlm_config = AsyncMock(
        return_value=SimpleNamespace(get_completion_async=completion)
    )
    with pytest.raises(ValueError, match="prompt exceeds session_window_context"):
        await session._fallback_generate_wm_creation(
            "source", [_message("u1")], session_window_context=20
        )
    completion.assert_not_awaited()


def _conversation(*turns: tuple[str, str]) -> list[Message]:
    messages: list[Message] = []
    for index, (user_text, assistant_text) in enumerate(turns, start=1):
        messages.append(_message(f"u{index}", text=user_text))
        messages.append(_message(f"a{index}", role="assistant", text=assistant_text))
    return messages


def _topic_enabled(**overrides) -> TopicContinuityConfig:
    values = {
        "enabled": True,
        "max_turns": 10,
        "overlap_turns": 1,
        "min_turns_to_split": 2,
        "skip_max_user_chars": 0,
    }
    values.update(overrides)
    return TopicContinuityConfig(**values)


def test_auto_commit_policy_resolves_phase2_batch_limits():
    limits = resolve_extraction_batch_limits(
        {
            "pending_token_threshold": 8000,
            "message_count_threshold": 40,
        }
    )

    assert limits == ExtractionBatchLimits(max_message_tokens=8000, max_messages=40)
    assert resolve_extraction_batch_limits(None).enabled is False
    assert resolve_extraction_batch_limits({}).enabled is False


def test_phase2_batches_split_an_oversized_batch_by_message_count():
    messages = [_message(f"m{index}") for index in range(5)]

    batches = plan_extraction_batches(
        messages,
        ExtractionBatchLimits(max_messages=2),
    )

    assert [[message.id for message in batch.messages] for batch in batches] == [
        ["m0", "m1"],
        ["m2", "m3"],
        ["m4"],
    ]


def test_phase2_batches_split_an_oversized_batch_by_token_count():
    messages = [_message(f"m{index}", text="word " * 80) for index in range(3)]
    one_message_tokens = messages[0].estimated_tokens

    batches = plan_extraction_batches(
        messages,
        ExtractionBatchLimits(max_message_tokens=one_message_tokens),
    )

    assert [[message.id for message in batch.messages] for batch in batches] == [
        ["m0"],
        ["m1"],
        ["m2"],
    ]
    assert all(batch.estimated_tokens <= one_message_tokens for batch in batches)


def test_phase2_batches_keep_a_turn_together_when_it_fits():
    messages = [
        _message("u1", text="question"),
        _message("a1", role="assistant", text="answer"),
        _message("u2", text="next"),
        _message("a2", role="assistant", text="done"),
    ]

    batches = plan_extraction_batches(
        messages,
        ExtractionBatchLimits(max_messages=2),
    )

    assert [[message.id for message in batch.messages] for batch in batches] == [
        ["u1", "a1"],
        ["u2", "a2"],
    ]


@pytest.mark.asyncio
async def test_working_memory_batches_carry_the_previous_summary_forward(monkeypatch):
    monkeypatch.setattr(
        "openviking.session.session._estimate_summary_template_tokens", lambda *args: 0
    )
    vlm = type("VLM", (), {"is_available": lambda self: True})()
    session = Session(
        viking_fs=None,
        vlm_resolver=type(
            "Resolver",
            (),
            {"get_vlm": AsyncMock(return_value=vlm)},
        )(),
    )
    messages = [_message(f"u{index}", text="x" * 100) for index in range(1, 4)]
    calls = []

    async def generate(batch, latest_archive_overview="", checkpoint_requests=None, **kwargs):
        calls.append(
            (
                [message.id for message in batch],
                latest_archive_overview,
                list(checkpoint_requests or []),
            )
        )
        return f"{latest_archive_overview}|{batch[0].id}"

    session._generate_archive_summary_async = AsyncMock(side_effect=generate)

    result = await session._generate_archive_summary_with_batching(
        messages,
        latest_archive_overview="previous",
        session_window_context=50,
    )

    assert result == "previous|u1|u2|u3"
    assert [(ids, previous) for ids, previous, _ in calls] == [
        (["u1"], "previous"),
        (["u2"], "previous|u1"),
        (["u3"], "previous|u1|u2"),
    ]


@pytest.mark.asyncio
async def test_working_memory_batches_reserve_room_for_rolling_summary(monkeypatch):
    monkeypatch.setattr(
        "openviking.session.session._estimate_summary_template_tokens", lambda *args: 0
    )
    vlm = type("VLM", (), {"is_available": lambda self: True})()
    session = Session(
        viking_fs=None,
        vlm_resolver=type(
            "Resolver",
            (),
            {"get_vlm": AsyncMock(return_value=vlm)},
        )(),
    )
    messages = [_message("u1"), _message("u2"), _message("u3")]
    token_by_id = {"u1": 40, "u2": 40, "u3": 40}
    calls = []

    monkeypatch.setattr(
        "openviking.session.session.estimate_extraction_message_tokens",
        lambda batch: sum(token_by_id[message.id] for message in batch),
    )
    monkeypatch.setattr(
        "openviking.session.session.estimate_text_tokens",
        lambda text: {"summary1": 45, "summary2": 45}.get(text, 0),
    )

    async def generate(batch, latest_archive_overview="", checkpoint_requests=None, **kwargs):
        calls.append(([message.id for message in batch], latest_archive_overview))
        return f"summary{len(calls)}"

    session._generate_archive_summary_async = AsyncMock(side_effect=generate)

    result = await session._generate_archive_summary_with_batching(
        messages,
        latest_archive_overview="",
        session_window_context=100,
    )

    assert result == "summary3"
    assert calls == [
        (["u1"], ""),
        (["u2"], "summary1"),
        (["u3"], "summary2"),
    ]


@pytest.mark.asyncio
async def test_working_memory_no_vlm_fallback_uses_all_messages(monkeypatch):
    messages = [_message("u1"), _message("u2"), _message("u3")]
    config = type("Config", (), {"vlm": None})()
    monkeypatch.setattr("openviking.session.session.get_openviking_config", lambda: config)
    session = Session(
        viking_fs=None,
        vlm_resolver=type(
            "Resolver",
            (),
            {"get_vlm": AsyncMock(return_value=None)},
        )(),
    )

    result = await session._generate_archive_summary_with_batching(
        messages,
        latest_archive_overview="",
    )

    assert result == "# Session Summary\n\n**Overview**: 3 turns, 3 messages"


@pytest.mark.asyncio
@pytest.mark.parametrize("path", ["create", "update", "fallback"])
@pytest.mark.parametrize(
    "message",
    [
        "Error code: 400 - InvalidParameter: Total tokens of multi-modal content "
        "and text exceed max message tokens.",
        "503 Service Unavailable",
    ],
)
async def test_working_memory_propagates_model_errors(monkeypatch, path, message):
    error = RuntimeError(message)
    completion = AsyncMock(side_effect=["no tool call", error] if path == "fallback" else error)
    vlm = SimpleNamespace(is_available=lambda: True, get_completion_async=completion)
    monkeypatch.setattr(
        "openviking.session.session.get_openviking_config",
        lambda: SimpleNamespace(output_language_override="en"),
    )
    session = Session(
        viking_fs=None,
        vlm_resolver=SimpleNamespace(get_vlm=AsyncMock(return_value=vlm)),
    )

    async def summarize():
        return await session._generate_archive_summary_async(
            [_message("u1")],
            latest_archive_overview="" if path == "create" else "## Current State\nPrevious work",
        )

    with pytest.raises(RuntimeError) as raised:
        await summarize()

    assert raised.value is error
    assert completion.await_count == (2 if path == "fallback" else 1)


@pytest.mark.asyncio
async def test_working_memory_prompt_uses_turn_created_at_in_messages(monkeypatch):
    prompts = []

    def render_prompt(_prompt_id, variables):
        prompts.append(dict(variables))
        return "prompt"

    monkeypatch.setattr("openviking.session.session._load_render_prompt", lambda: render_prompt)
    monkeypatch.setattr(
        "openviking.session.session.get_openviking_config",
        lambda: SimpleNamespace(output_language_override="en"),
    )
    vlm = SimpleNamespace(
        is_available=lambda: True,
        get_completion_async=AsyncMock(return_value="wm"),
    )
    session = Session(
        viking_fs=None,
        vlm_resolver=SimpleNamespace(get_vlm=AsyncMock(return_value=vlm)),
    )

    await session._generate_archive_summary_async(
        [
            Message(
                id="u0",
                role="user",
                parts=[TextPart("content")],
                created_at="2026-10-28T23:59:00Z",
            )
        ]
    )
    await session._generate_archive_summary_async(
        [
            Message(
                id="u1",
                role="user",
                parts=[TextPart("content")],
                created_at="2026-10-29T08:00:00Z",
            )
        ],
        latest_archive_overview="## Current State\nPrevious work",
    )

    assert all("created_at" not in prompt for prompt in prompts)
    assert '<turn created_at="2026-10-28">' in prompts[0]["messages"]
    assert '<turn created_at="2026-10-29">' in prompts[1]["messages"]


@pytest.mark.asyncio
async def test_long_term_memory_batches_follow_the_planned_limit():
    session = Session(viking_fs=None)
    messages = [_message("u1"), _message("u2"), _message("u3")]
    recorded_batches = []

    async def extract(batch):
        return [batch[0].id]

    async def record(operation_name, step, batch, fn):
        result = await fn()
        recorded_batches.append((operation_name, step, [message.id for message in batch]))
        return result

    result = await session._extract_long_term_memories_with_batching(
        messages=messages,
        limits=ExtractionBatchLimits(max_messages=1),
        archive_uri="viking://user/default/sessions/s1/history/archive_001",
        extract_batch=extract,
        record_batch=record,
    )

    assert result == ["u1", "u2", "u3"]
    assert [message_ids for _, _, message_ids in recorded_batches] == [
        ["u1"],
        ["u2"],
        ["u3"],
    ]
    assert all(step == "long_term" for _, step, _ in recorded_batches)


@pytest.mark.asyncio
async def test_long_term_batching_flag_off_is_one_extract_call():
    session = Session(viking_fs=None)
    messages = _conversation(("q1", "a1"), ("q2", "a2"))
    recorded_batches = []

    async def extract(batch):
        return [message.id for message in batch]

    async def record(operation_name, step, batch, fn):
        result = await fn()
        recorded_batches.append([message.id for message in batch])
        return result

    await session._extract_long_term_memories_with_batching(
        messages=messages,
        limits=ExtractionBatchLimits(),
        archive_uri="viking://user/default/sessions/s1/history/archive_001",
        extract_batch=extract,
        record_batch=record,
    )
    assert recorded_batches == [["u1", "a1", "u2", "a2"]]


@pytest.mark.asyncio
async def test_long_term_batching_flag_on_calls_once_per_topic_group():
    session = Session(viking_fs=None)
    messages = _conversation(("login", "ok"), ("dinner", "noodles"))
    recorded = []
    extract_ids = []

    async def extract_and_store(batch):
        extract_ids.append([message.id for message in batch])
        return [message.id for message in batch]

    async def record(operation_name, step, batch, fn):
        result = await fn()
        recorded.append(
            {
                "owned": [message.id for message in batch],
                "operation": operation_name,
                "result": result,
            }
        )
        return result

    await session._extract_long_term_memories_with_batching(
        messages=messages,
        limits=ExtractionBatchLimits(),
        archive_uri="viking://user/default/sessions/s1/history/archive_001",
        extract_batch=extract_and_store,
        record_batch=record,
        topic=_topic_enabled(overlap_turns=1),
        continuity_check=lambda _previous, current: "dinner" not in turn_text(current),
    )
    assert extract_ids == [["u1", "a1"], ["u2", "a2"]]
    assert [item["owned"] for item in recorded] == [["u1", "a1"], ["u2", "a2"]]
    assert all(
        item["operation"].startswith("long_term_memory_extraction_batch_") for item in recorded
    )


@pytest.mark.asyncio
async def test_long_term_batching_records_owned_ids_only_with_overlap():
    session = Session(viking_fs=None)
    messages = _conversation(("t1", "a1"), ("t2", "a2"), ("t3", "a3"))
    owned_ids = []
    extract_ids = []

    async def extract(batch):
        extract_ids.append([message.id for message in batch])
        return []

    async def record(operation_name, step, batch, fn):
        owned_ids.append([message.id for message in batch])
        return await fn()

    await session._extract_long_term_memories_with_batching(
        messages=messages,
        limits=ExtractionBatchLimits(),
        archive_uri="viking://user/default/sessions/s1/history/archive_001",
        extract_batch=extract,
        record_batch=record,
        topic=_topic_enabled(max_turns=2, overlap_turns=1),
        continuity_check=lambda _previous, _current: True,
    )
    assert extract_ids == [["u1", "a1", "u2", "a2"], ["u2", "a2", "u3", "a3"]]
    assert owned_ids == [["u1", "a1", "u2", "a2"], ["u3", "a3"]]


@pytest.mark.asyncio
async def test_long_term_batching_with_policy_limits_after_topic_groups():
    session = Session(viking_fs=None)
    messages = _conversation(("same", "one"), ("same", "two"))
    extract_ids = []

    async def extract(batch):
        extract_ids.append([message.id for message in batch])
        return []

    async def record(operation_name, step, batch, fn):
        return await fn()

    await session._extract_long_term_memories_with_batching(
        messages=messages,
        limits=ExtractionBatchLimits(max_messages=2),
        archive_uri="viking://user/default/sessions/s1/history/archive_001",
        extract_batch=extract,
        record_batch=record,
        topic=_topic_enabled(),
        continuity_check=lambda _previous, _current: True,
    )
    assert extract_ids == [["u1", "a1"], ["u2", "a2"]]


class _RecordingCompressor:
    def __init__(self) -> None:
        self.calls: list[list[str]] = []

    async def extract_long_term_memories(self, *, messages, **_kwargs):
        self.calls.append([message.id for message in messages])
        return []


class _TaskTracker:
    async def build_queue_status(self, *_args, **_kwargs):
        return {}

    async def update_stage(self, *_args, **_kwargs):
        return None

    async def wait_for_task_work(self, *_args, **_kwargs):
        return None

    async def start(self, *_args, **_kwargs) -> None:
        return None

    async def complete(self, *_args, **_kwargs) -> None:
        return None

    async def fail(self, *_args, **_kwargs) -> None:
        return None


async def _run_phase2(
    monkeypatch,
    messages: list[Message],
    *,
    topic: TopicContinuityConfig | None = None,
) -> tuple[_RecordingCompressor, list[dict]]:
    compressor = _RecordingCompressor()
    session = Session(viking_fs=None, session_compressor=compressor)
    meta_updates: list[dict] = []
    memory = MemoryConfig()
    if topic is not None:
        memory.topic_continuity = topic
    config = SimpleNamespace(memory=memory)
    monkeypatch.setattr(
        "openviking.session.session.get_openviking_config",
        lambda: config,
    )
    monkeypatch.setattr(
        "openviking_cli.utils.config.open_viking_config.get_openviking_config",
        lambda: config,
    )
    monkeypatch.setattr(
        "openviking_cli.utils.config.get_openviking_config",
        lambda: config,
    )
    monkeypatch.setattr(
        "openviking.service.task_tracker.get_task_tracker",
        lambda: _TaskTracker(),
    )

    async def _capture_meta(_archive_uri, updates, lease_ref=None):
        meta_updates.append(dict(updates))

    session._merge_archive_meta = _capture_meta
    session._merge_and_save_commit_meta = AsyncMock()
    session._stage_midterm_memories = AsyncMock(return_value=1)
    session._publish_midterm_memories_for_recall = AsyncMock(return_value=1)
    session._reconcile_midterm_long_term_conversion = AsyncMock(return_value=(1, 1))
    await session._run_memory_extraction(
        task_id="task-topic",
        archive_uri="viking://user/default/sessions/s1/history/archive_001",
        messages=messages,
        first_message_id=messages[0].id,
        last_message_id=messages[-1].id,
        memory_policy={"working_memory": {"enabled": False}},
        auto_commit_policy=None,
    )
    if memory.topic_continuity.enabled:
        session._stage_midterm_memories.assert_awaited_once()
        session._publish_midterm_memories_for_recall.assert_awaited_once()
        session._reconcile_midterm_long_term_conversion.assert_awaited_once()
    else:
        session._stage_midterm_memories.assert_not_awaited()
        session._publish_midterm_memories_for_recall.assert_not_awaited()
        session._reconcile_midterm_long_term_conversion.assert_not_awaited()
    return compressor, meta_updates


def _completed_long_term_ids(meta_updates: list[dict]) -> list[set[str]]:
    completed = []
    for update in meta_updates:
        steps = update.get("completed_memory_steps") or {}
        if "long_term" in steps:
            completed.append(set(steps["long_term"]))
    return completed


@pytest.mark.asyncio
async def test_commit_path_flag_off_extracts_once_without_policy(monkeypatch):
    messages = _conversation(("q1", "a1"), ("q2", "a2"))
    compressor, meta_updates = await _run_phase2(monkeypatch, messages)
    assert compressor.calls == [["u1", "a1", "u2", "a2"]]
    assert _completed_long_term_ids(meta_updates) == [{"u1", "a1", "u2", "a2"}]


@pytest.mark.asyncio
async def test_commit_path_flag_on_stays_one_batch_when_continuous(monkeypatch):
    messages = _conversation(("login", "ok"), ("still login", "fixed"))
    compressor, _meta_updates = await _run_phase2(
        monkeypatch,
        messages,
        topic=TopicContinuityConfig(enabled=True),
    )
    assert compressor.calls == [["u1", "a1", "u2", "a2"]]


@pytest.mark.asyncio
async def test_commit_path_flag_on_splits_on_max_turns_and_records_owned_ids(monkeypatch):
    messages = _conversation(("t1", "a1"), ("t2", "a2"), ("t3", "a3"))
    compressor, meta_updates = await _run_phase2(
        monkeypatch,
        messages,
        topic=TopicContinuityConfig(enabled=True, max_turns=2, overlap_turns=1),
    )
    assert compressor.calls == [
        ["u1", "a1", "u2", "a2"],
        ["u2", "a2", "u3", "a3"],
    ]
    completed = _completed_long_term_ids(meta_updates)
    assert completed[0] == {"u1", "a1", "u2", "a2"}
    assert completed[1] - completed[0] == {"u3", "a3"}
