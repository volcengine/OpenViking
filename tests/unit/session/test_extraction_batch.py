# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0

from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from openviking.message import ContextPart, Message, TextPart, ToolPart
from openviking.models.vlm.base import ToolCall, VLMResponse
from openviking.session.checkpoints import CheckpointRequest
from openviking.session.extraction_batch import (
    ExtractionBatchLimits,
    bound_oversized_extraction_messages,
    estimate_extraction_message_tokens,
    plan_extraction_batches,
    resolve_extraction_batch_limits,
)
from openviking.session.session import Session
from openviking.utils.token_estimation import estimate_text_tokens


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


def test_model_window_batches_sessions_without_an_auto_commit_policy():
    limits = resolve_extraction_batch_limits(None, prompt_token_limit=11469)

    assert limits == ExtractionBatchLimits(max_message_tokens=11469, max_messages=None)
    assert limits.enabled is True


def test_model_window_caps_a_policy_that_exceeds_it():
    limits = resolve_extraction_batch_limits(
        {"pending_token_threshold": 150000, "message_count_threshold": 100},
        prompt_token_limit=11469,
    )

    assert limits == ExtractionBatchLimits(max_message_tokens=11469, max_messages=100)


def test_policy_threshold_still_wins_when_it_is_the_smaller_one():
    limits = resolve_extraction_batch_limits(
        {"pending_token_threshold": 8000},
        prompt_token_limit=11469,
    )

    assert limits.max_message_tokens == 8000


def test_unknown_model_window_keeps_the_policy_only_path():
    limits = resolve_extraction_batch_limits(
        {"pending_token_threshold": 150000, "message_count_threshold": 100}, None
    )

    assert limits == ExtractionBatchLimits(max_message_tokens=150000, max_messages=100)


def test_a_window_sized_oversized_archive_splits_into_multiple_passes():
    messages = [_message(f"m{index}", text="word " * 3000) for index in range(3)]

    # One message fits the window on its own; any pair of them does not.
    window_limit = messages[0].estimated_tokens

    batches = plan_extraction_batches(
        messages,
        resolve_extraction_batch_limits(None, prompt_token_limit=window_limit),
    )

    assert [[message.id for message in batch.messages] for batch in batches] == [
        ["m0"],
        ["m1"],
        ["m2"],
    ]
    assert all(batch.estimated_tokens <= window_limit for batch in batches)


def test_messages_inside_the_ceiling_are_not_touched():
    message = _message("m0", text="short")
    limits = ExtractionBatchLimits(max_message_tokens=1000)

    bounded, trimmed = bound_oversized_extraction_messages([message], limits)

    assert trimmed == 0
    assert bounded[0] is message


def test_without_a_ceiling_nothing_is_trimmed():
    messages = [_message("m0", text="word " * 4000)]

    bounded, trimmed = bound_oversized_extraction_messages(messages, ExtractionBatchLimits())

    assert trimmed == 0
    assert bounded == messages


def test_a_single_oversized_message_is_trimmed_inside_the_ceiling():
    message = _message("m0", text="word " * 4000)
    limits = ExtractionBatchLimits(max_message_tokens=1000)
    assert message.estimated_tokens > 1000

    bounded, trimmed = bound_oversized_extraction_messages([message], limits)

    assert trimmed == 1
    assert bounded[0].id == "m0"
    assert bounded[0].role == "user"
    assert bounded[0].estimated_tokens <= 1000
    assert "truncated for extraction" in bounded[0].content
    # The stored message is untouched; only the extraction copy is trimmed.
    assert "truncated for extraction" not in message.content


def test_a_hydrated_tool_output_is_trimmed_but_keeps_its_identity():
    message = Message(
        id="m0",
        role="assistant",
        parts=[
            TextPart("ran a command"),
            ToolPart(
                tool_id="call_1",
                tool_name="terminal",
                tool_output="x " * 20000,
                tool_status="completed",
            ),
        ],
    )

    bounded, trimmed = bound_oversized_extraction_messages(
        [message], ExtractionBatchLimits(max_message_tokens=1500)
    )

    assert trimmed == 1
    tool_part = next(part for part in bounded[0].parts if isinstance(part, ToolPart))
    assert tool_part.tool_id == "call_1"
    assert tool_part.tool_name == "terminal"
    assert tool_part.tool_output.endswith("characters kept]")
    assert bounded[0].estimated_tokens <= 1500


def test_every_planned_pass_stays_inside_the_ceiling_with_twelve_thousand_tokens():
    # Fixture shape reported on #5894: 12 messages that together vastly exceed a
    # small-window ceiling must never reach the extraction callback in one pass.
    messages = [_message(f"m{index}", text="word " * 1000) for index in range(12)]
    limits = resolve_extraction_batch_limits(None, prompt_token_limit=4000)
    assert estimate_extraction_message_tokens(messages) > limits.max_message_tokens

    bounded, _ = bound_oversized_extraction_messages(messages, limits)
    batches = plan_extraction_batches(bounded, limits)

    assert len(batches) > 1
    assert all(batch.estimated_tokens <= limits.max_message_tokens for batch in batches)
    assert sum(len(batch.messages) for batch in batches) == 12


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
async def test_long_term_memory_batches_bound_an_oversized_message_before_extraction():
    session = Session(viking_fs=None)
    messages = [_message("u1", text="word " * 4000), _message("u2", text="small")]
    delivered = []

    async def extract(batch):
        delivered.append(sum(message.estimated_tokens for message in batch))
        return []

    async def record(operation_name, step, batch, fn):
        return await fn()

    await session._extract_long_term_memories_with_batching(
        messages=messages,
        limits=ExtractionBatchLimits(max_message_tokens=1000),
        archive_uri="viking://user/default/sessions/s1/history/archive_001",
        extract_batch=extract,
        record_batch=record,
    )

    # Nothing over the ceiling may ever reach the extraction callback.
    assert delivered
    assert max(delivered) <= 1000


def test_every_tool_output_of_a_multi_part_message_is_trimmed():
    message = Message(
        id="m",
        role="assistant",
        parts=[
            ToolPart(tool_id=f"c{index}", tool_name="terminal", tool_output="x " * 10000)
            for index in range(9)
        ],
    )

    bounded, trimmed = bound_oversized_extraction_messages(
        [message], ExtractionBatchLimits(max_message_tokens=4000)
    )

    assert trimmed == 1
    assert bounded[0].estimated_tokens <= 4000
    assert [part.tool_id for part in bounded[0].parts if isinstance(part, ToolPart)] == [
        f"c{index}" for index in range(9)
    ]


def test_a_tool_input_dominated_call_is_trimmed_not_only_its_output():
    message = Message(
        id="m",
        role="assistant",
        parts=[
            ToolPart(
                tool_id="call_1",
                tool_name="write_file",
                tool_input={"content": "x " * 40000},
                tool_output="saved",
                tool_status="completed",
            )
        ],
    )

    bounded, trimmed = bound_oversized_extraction_messages(
        [message], ExtractionBatchLimits(max_message_tokens=4000)
    )

    assert trimmed == 1
    assert bounded[0].estimated_tokens <= 4000
    tool_part = next(part for part in bounded[0].parts if isinstance(part, ToolPart))
    assert tool_part.tool_id == "call_1"
    assert tool_part.tool_output == "saved"
    assert tool_part.tool_input["content"].endswith("characters kept]")


def test_a_context_abstract_is_trimmed():
    message = Message(
        id="m",
        role="user",
        parts=[ContextPart(uri="viking://user/default/memories/profile.md", abstract="x " * 40000)],
    )

    bounded, trimmed = bound_oversized_extraction_messages(
        [message], ExtractionBatchLimits(max_message_tokens=4000)
    )

    assert trimmed == 1
    assert bounded[0].estimated_tokens <= 4000
    context_part = next(part for part in bounded[0].parts if isinstance(part, ContextPart))
    assert context_part.uri == "viking://user/default/memories/profile.md"
    assert context_part.abstract.endswith("characters kept]")


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "message",
    [
        pytest.param(_message("m", text="word " * 4000), id="long-text"),
        pytest.param(
            Message(
                id="m",
                role="assistant",
                parts=[
                    ToolPart(tool_id=f"c{index}", tool_name="terminal", tool_output="x " * 10000)
                    for index in range(9)
                ],
            ),
            id="nine-tool-outputs",
        ),
        pytest.param(
            Message(
                id="m",
                role="assistant",
                parts=[
                    ToolPart(
                        tool_id="call_1",
                        tool_name="write_file",
                        tool_input={"content": "x " * 40000},
                        tool_output="saved",
                        tool_status="completed",
                    )
                ],
            ),
            id="tool-input-dominated",
        ),
        pytest.param(
            Message(
                id="m",
                role="user",
                parts=[
                    ContextPart(
                        uri="viking://user/default/memories/profile.md", abstract="x " * 40000
                    )
                ],
            ),
            id="context-abstract",
        ),
        pytest.param(
            Message(
                id="m",
                role="assistant",
                parts=[
                    ToolPart(
                        tool_id="call_1",
                        tool_name="write_files",
                        tool_input={
                            "files": [
                                {"path": f"f{index}.txt", "content": "x " * 10000}
                                for index in range(9)
                            ]
                        },
                        tool_output="saved",
                        tool_status="completed",
                    )
                ],
            ),
            id="multi-leaf-tool-input",
        ),
    ],
)
async def test_nothing_above_the_ceiling_reaches_the_extraction_callback(message):
    session = Session(viking_fs=None)
    delivered = []
    original = [
        (type(part).__name__, getattr(part, "text", ""), getattr(part, "tool_output", ""))
        for part in message.parts
    ]

    async def extract(batch):
        delivered.append(sum(item.estimated_tokens for item in batch))
        return []

    async def record(operation_name, step, batch, fn):
        return await fn()

    await session._extract_long_term_memories_with_batching(
        messages=[message],
        limits=ExtractionBatchLimits(max_message_tokens=4000),
        archive_uri="viking://user/default/sessions/s1/history/archive_001",
        extract_batch=extract,
        record_batch=record,
    )

    assert delivered
    assert max(delivered) <= 4000
    assert [
        (type(part).__name__, getattr(part, "text", ""), getattr(part, "tool_output", ""))
        for part in message.parts
    ] == original


def test_a_tool_input_with_many_large_leaves_is_trimmed_across_all_leaves():
    message = Message(
        id="m",
        role="assistant",
        parts=[
            ToolPart(
                tool_id="call_1",
                tool_name="write_files",
                tool_input={
                    "files": [
                        {"path": f"f{index}.txt", "content": "x " * 10000} for index in range(9)
                    ]
                },
                tool_output="saved",
                tool_status="completed",
            )
        ],
    )
    assert message.estimated_tokens > 4000

    bounded, trimmed = bound_oversized_extraction_messages(
        [message], ExtractionBatchLimits(max_message_tokens=4000)
    )

    assert trimmed == 1
    assert bounded[0].estimated_tokens <= 4000
    tool_part = next(part for part in bounded[0].parts if isinstance(part, ToolPart))
    files = tool_part.tool_input["files"]
    assert tool_part.tool_id == "call_1"
    assert tool_part.tool_output == "saved"
    assert [entry["path"] for entry in files] == [f"f{index}.txt" for index in range(9)]
    assert all(entry["content"].endswith("characters kept]") for entry in files)


def test_an_unfittable_message_returns_the_smallest_achievable_form():
    message = Message(
        id="m",
        role="assistant",
        parts=[
            ToolPart(
                tool_id="call_1",
                tool_name="write_files",
                tool_input={
                    "files": [
                        {"path": f"f{index}.txt", "content": "x " * 2000} for index in range(400)
                    ]
                },
                tool_output="saved",
                tool_status="completed",
            )
        ],
    )
    limits = ExtractionBatchLimits(max_message_tokens=4000)

    bounded, trimmed = bound_oversized_extraction_messages([message], limits)

    assert trimmed == 1
    # Shortening alone cannot reach the ceiling here: 400 marked leaves still cost
    # more than the budget. The pass must still shrink to the smallest reachable
    # form and stay flagged so the caller reports it.
    assert bounded[0].estimated_tokens < message.estimated_tokens / 10
    assert plan_extraction_batches(bounded, limits)[0].oversized is True
