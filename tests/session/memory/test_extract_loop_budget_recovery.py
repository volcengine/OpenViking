# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0
"""Tests for ExtractLoop reasoning-budget exhaustion handling and thinking passthrough.

Regression tests for https://github.com/volcengine/OpenViking/issues/6096:
a patch-merge response whose reasoning consumed the whole completion budget used
to be classified as an ordinary format error, and the single format retry reused
the same thinking mode and output cap without showing the model its truncated
program.
"""

from types import SimpleNamespace
from unittest.mock import MagicMock, Mock, patch

import pytest

from openviking.models.vlm.base import VLMResponse
from openviking.session.memory.dataclass import MemoryTypeSchema
from openviking.session.memory.extract_loop import ExtractLoop
from openviking.session.memory.page_id_map import PageIdMap

_DEFAULT_CAP = 32768
_RAISED_CAP = 65536

_TRUNCATED_PROGRAM = (
    "sdk.upsert_preference(\n"
    '    topic="booking",\n'
    '    content="""The agent should deduplicate requests because\n'
)


class FakeContextProvider:
    read_file_contents = {}

    def get_memory_schemas(self, ctx):
        return [
            MemoryTypeSchema(
                memory_type="preferences",
                description="Preferences",
                directory="viking://user/{user_space}/memories/preferences",
                filename_template="{topic}.md",
                fields=[],
            )
        ]

    def get_tools(self):
        return []

    def get_extract_context(self):
        return SimpleNamespace(page_id_map=PageIdMap())

    def get_output_language(self):
        return "en"

    def instruction(self):
        return "Extract memory operations."

    async def prefetch(self):
        return []


def _budget_exhausted_response(
    completion_tokens: int = _DEFAULT_CAP,
    finish_reason: str = "length",
) -> VLMResponse:
    return VLMResponse(
        content=_TRUNCATED_PROGRAM,
        finish_reason=finish_reason,
        usage={"prompt_tokens": 100, "completion_tokens": completion_tokens},
    )


class ScriptedVLM:
    """Fake VLM that records per-call kwargs and replays scripted responses."""

    model = "test-model"

    def __init__(self, responses):
        self.responses = iter(responses)
        self.calls = []

    async def get_completion_async(self, **kwargs):
        self.calls.append(kwargs)
        return next(self.responses)


def _user_contents(messages):
    return [message.get("content", "") for message in messages if message.get("role") == "user"]


def _assistant_contents(messages):
    return [
        message.get("content", "") for message in messages if message.get("role") == "assistant"
    ]


def _make_loop(vlm, **kwargs):
    isolation_handler = Mock()
    isolation_handler.get_read_scope.return_value = None
    isolation_handler.fill_identity_fields.side_effect = lambda item, **kwargs: item
    return ExtractLoop(
        vlm=vlm,
        viking_fs=MagicMock(),
        context_provider=FakeContextProvider(),
        isolation_handler=isolation_handler,
        max_iterations=1,
        **kwargs,
    )


def _run_loop(loop):
    config = SimpleNamespace(
        memory=SimpleNamespace(link_enabled=False, extraction_output_format="python")
    )
    with (
        patch(
            "openviking.session.memory.extract_loop.get_openviking_config",
            return_value=config,
        ),
        patch(
            "openviking_cli.utils.config.get_openviking_config",
            return_value=config,
        ),
    ):
        loop._check_unread_existing_files = _async_return({})
        loop._validate_patch_operations = _async_return([])
        loop.finalize_operations = _async_return(None)
        return loop.run()


def _async_return(value):
    async def _call(*args, **kwargs):
        return value

    return _call


class TestThinkingPassthrough:
    @pytest.mark.asyncio
    @pytest.mark.parametrize("thinking", [None, True, False])
    async def test_thinking_kwarg_is_forwarded_verbatim(self, thinking):
        vlm = ScriptedVLM(["sdk.commit()"])
        loop = _make_loop(vlm, thinking=thinking)
        await _run_loop(loop)

        assert len(vlm.calls) == 1
        assert vlm.calls[0]["thinking"] is thinking


class TestBudgetExhaustedRecovery:
    @pytest.mark.asyncio
    async def test_truncated_response_gets_failed_program_and_raised_cap_on_retry(self):
        vlm = ScriptedVLM([_budget_exhausted_response(), "sdk.commit()"])
        loop = _make_loop(vlm)

        operations, _tools = await _run_loop(loop)

        assert operations is not None
        assert len(vlm.calls) == 2
        # First attempt used the resolved default cap; thinking stays a passthrough.
        assert vlm.calls[0]["max_tokens"] == _DEFAULT_CAP
        assert vlm.calls[0]["thinking"] is None
        # The retry changes the conditions: bounded cap raise, not the same budget.
        assert vlm.calls[1]["max_tokens"] == _RAISED_CAP
        # The retry sees the model's own truncated program as an assistant message.
        retry_messages = vlm.calls[1]["messages"]
        assistant_contents = _assistant_contents(retry_messages)
        assert assistant_contents == [_TRUNCATED_PROGRAM]
        retry_user_contents = _user_contents(retry_messages)
        assert any("output-token budget" in content for content in retry_user_contents), (
            retry_user_contents
        )

    @pytest.mark.asyncio
    async def test_completion_tokens_at_cap_counts_as_exhausted_without_length_finish(self):
        vlm = ScriptedVLM(
            [
                _budget_exhausted_response(finish_reason="stop"),
                "sdk.commit()",
            ]
        )
        loop = _make_loop(vlm)

        await _run_loop(loop)

        assert len(vlm.calls) == 2
        assert _assistant_contents(vlm.calls[1]["messages"]) == [_TRUNCATED_PROGRAM]

    @pytest.mark.asyncio
    async def test_budget_exhausted_twice_fails_with_actionable_kind(self):
        vlm = ScriptedVLM([_budget_exhausted_response(), _budget_exhausted_response()])
        loop = _make_loop(vlm)

        with pytest.raises(RuntimeError, match="failure_kind=budget_exhausted"):
            await _run_loop(loop)

        # Recovery stays bounded: exactly one budget retry, cap raised once.
        assert len(vlm.calls) == 2
        assert vlm.calls[1]["max_tokens"] == _RAISED_CAP

    @pytest.mark.asyncio
    async def test_ordinary_format_error_retry_keeps_cap_and_no_assistant_program(self):
        vlm = ScriptedVLM(["this is not a python program", "sdk.commit()"])
        loop = _make_loop(vlm)

        await _run_loop(loop)

        assert len(vlm.calls) == 2
        assert vlm.calls[1]["max_tokens"] == _DEFAULT_CAP
        assert _assistant_contents(vlm.calls[1]["messages"]) == []
