# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0
"""Tests for ExtractLoop truncation handling and thinking passthrough.

Regression tests for https://github.com/volcengine/issues/6096, reworked after
review of PR #6104: truncation is detected only from an explicit
``finish_reason == "length"``, checked before parsing; ``tools=[]`` is passed so
real backends still return a ``VLMResponse`` with finish_reason and usage on the
patch-merge path (where ``PatchMergeContextProvider.get_tools() == []``); and
budget exhaustion is terminal — no cap doubling, no replay of the truncated
output, and no completion_tokens-based truncation inference.
"""

import json
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, Mock, patch

import pytest

from openviking.models.vlm.base import ToolCall, VLMResponse
from openviking.session.memory.dataclass import MemoryField, MemoryFile, MemoryTypeSchema
from openviking.session.memory.extract_loop import ExtractLoop
from openviking.session.memory.merge_op import FieldType, MergeOp
from openviking.session.memory.page_id_map import PageIdMap

_DEFAULT_CAP = 32768

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
        # The real patch-merge path returns [] — this is what forced _call_llm
        # down the tools-less branch under review.
        return []

    def get_extract_context(self):
        return SimpleNamespace(page_id_map=PageIdMap())

    def get_output_language(self):
        return "en"

    def instruction(self):
        return "Extract memory operations."

    async def prefetch(self):
        return []


def _length_response(
    completion_tokens: int = _DEFAULT_CAP,
    finish_reason: str = "length",
    content: str = _TRUNCATED_PROGRAM,
) -> VLMResponse:
    return VLMResponse(
        content=content,
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
        self.calls.append({**kwargs, "messages": list(kwargs["messages"])})
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


async def _run_loop(loop, *, output_format="python"):
    config = SimpleNamespace(
        memory=SimpleNamespace(link_enabled=False, extraction_output_format=output_format)
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
        # Await inside the patch context: run() is a coroutine, so returning it
        # would let the patches expire before the code under test executes.
        return await loop.run()


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


class TestToolsDisabledStillUsesVLMResponse:
    @pytest.mark.asyncio
    async def test_tools_is_empty_list_not_none(self):
        vlm = ScriptedVLM(["sdk.commit()"])
        loop = _make_loop(vlm)
        await _run_loop(loop)

        assert len(vlm.calls) == 1
        assert vlm.calls[0]["tools"] == []

    @pytest.mark.asyncio
    async def test_empty_tools_response_from_real_volcengine_backend(self):
        """On the patch-merge path (tools=[]) the real volcengine backend must
        still return a VLMResponse carrying finish_reason/usage, with the
        inline thinking stripped from the content."""
        volcengine_vlm = pytest.importorskip(
            "openviking.models.vlm.backends.volcengine_vlm"
        ).VolcEngineVLM
        raw = SimpleNamespace(
            choices=[
                SimpleNamespace(
                    message=SimpleNamespace(
                        content="<think>reasoning</think>sdk.commit()", tool_calls=None
                    ),
                    finish_reason="stop",
                )
            ],
            usage=SimpleNamespace(
                prompt_tokens=10,
                completion_tokens=5,
                total_tokens=15,
                prompt_tokens_details=None,
                completion_tokens_details=None,
            ),
        )

        async def create(**_kwargs):
            return raw

        vlm = volcengine_vlm({"provider": "volcengine", "model": "test-model"})
        vlm.get_async_client = lambda: SimpleNamespace(
            chat=SimpleNamespace(completions=SimpleNamespace(create=create))
        )
        loop = _make_loop(vlm)

        operations, _tools = await _run_loop(loop)

        assert operations is not None
        # Backend-level check: with tools=[] the backend still returns a
        # VLMResponse (finish_reason/usage preserved) whose content had the
        # inline thinking stripped by _clean_response.
        response = await vlm.get_completion_async(
            messages=[{"role": "user", "content": "hi"}], tools=[]
        )
        assert isinstance(response, VLMResponse)
        assert response.finish_reason == "stop"
        assert response.usage["completion_tokens"] == 5
        assert response.content == "sdk.commit()"


class TestTruncationDetection:
    @pytest.mark.asyncio
    async def test_length_finish_reason_is_terminal_even_when_program_parses(self):
        """A truncated program that still parses must NOT be accepted as
        operations — trailing writes/deletes would be silently lost."""
        vlm = ScriptedVLM(
            [_length_response(content="sdk.commit()  # actually truncated upstream")]
        )
        loop = _make_loop(vlm)

        with pytest.raises(RuntimeError, match="output-token budget"):
            await _run_loop(loop)

        # Fail fast: exactly one call, no same-cap format retry.
        assert len(vlm.calls) == 1
        assert vlm.calls[0]["max_tokens"] == _DEFAULT_CAP

    @pytest.mark.asyncio
    async def test_length_finish_reason_error_names_cap_usage_and_preview(self):
        vlm = ScriptedVLM([_length_response(completion_tokens=32768)])
        loop = _make_loop(vlm)

        with pytest.raises(RuntimeError) as exc_info:
            await _run_loop(loop)

        message = str(exc_info.value)
        assert "finish_reason='length'" in message
        assert f"output cap={_DEFAULT_CAP}" in message
        assert "completion_tokens=32768" in message
        assert "vlm.max_tokens" in message

    @pytest.mark.asyncio
    async def test_length_through_real_volcengine_backend_is_budget_exhausted(self):
        """The reviewer's main repro: with the real volcengine backend and
        tools=[], finish_reason='length' used to be classified as parse_error
        because the backend returned a bare str."""
        volcengine_vlm = pytest.importorskip(
            "openviking.models.vlm.backends.volcengine_vlm"
        ).VolcEngineVLM
        raw = SimpleNamespace(
            choices=[
                SimpleNamespace(
                    message=SimpleNamespace(
                        content=_TRUNCATED_PROGRAM, tool_calls=None
                    ),
                    finish_reason="length",
                )
            ],
            usage=SimpleNamespace(
                prompt_tokens=20214,
                completion_tokens=32768,
                total_tokens=52982,
                prompt_tokens_details=None,
                completion_tokens_details=None,
            ),
        )

        async def create(**_kwargs):
            return raw

        vlm = volcengine_vlm({"provider": "volcengine", "model": "test-model"})
        vlm.get_async_client = lambda: SimpleNamespace(
            chat=SimpleNamespace(completions=SimpleNamespace(create=create))
        )
        loop = _make_loop(vlm)

        with pytest.raises(RuntimeError, match="finish_reason='length'"):
            await _run_loop(loop)


class TestNoTokenCounterInference:
    @pytest.mark.asyncio
    async def test_stop_at_cap_is_not_truncation(self):
        """finish_reason='stop' that lands exactly on the cap is a complete
        response, not budget exhaustion: an ordinary format error gets the
        regular single retry with the cap unchanged and no assistant replay."""
        vlm = ScriptedVLM(
            [_length_response(finish_reason="stop"), "sdk.commit()"]
        )
        loop = _make_loop(vlm)

        operations, _tools = await _run_loop(loop)

        assert operations is not None
        assert len(vlm.calls) == 2
        # No cap doubling: the retry reused the same output budget.
        assert vlm.calls[1]["max_tokens"] == _DEFAULT_CAP
        # No replay of the failed output as an assistant message.
        assert _assistant_contents(vlm.calls[1]["messages"]) == []

    @pytest.mark.asyncio
    @pytest.mark.parametrize(
        ("invalid_program", "diagnostic"),
        [
            ("this is not a python program", "invalid Python syntax"),
            ("sdk.unknown()", "unknown SDK method sdk.unknown()"),
        ],
    )
    async def test_ordinary_format_error_retry_keeps_cap_and_includes_diagnostic(
        self, invalid_program, diagnostic
    ):
        vlm = ScriptedVLM([invalid_program, "sdk.commit()"])
        loop = _make_loop(vlm)

        await _run_loop(loop)

        assert len(vlm.calls) == 2
        assert vlm.calls[1]["max_tokens"] == _DEFAULT_CAP
        assert _assistant_contents(vlm.calls[1]["messages"]) == []
        assert "Previous model output" not in "\n".join(_user_contents(vlm.calls[0]["messages"]))
        retry_prompt = "\n".join(_user_contents(vlm.calls[1]["messages"]))
        assert "Parser error:" in retry_prompt
        assert diagnostic in retry_prompt
        assert json.dumps(invalid_program) in retry_prompt

    @pytest.mark.asyncio
    async def test_retry_parses_experience_update_and_preserves_ending_quote(self):
        body = '## Situation\n最后一步是"直接执行"'
        invalid_program = f'experiences_1.content.update("""{body}""")'
        fixed_program = f"experiences_1.content.update('''{body}''')"
        vlm = ScriptedVLM([invalid_program, fixed_program])
        loop = _make_loop(vlm)
        uri = "viking://user/test/memories/experiences/sample.md"
        schema = MemoryTypeSchema(
            memory_type="experiences",
            description="Experiences",
            directory="viking://user/{{ user_space }}/memories/experiences",
            filename_template="sample.md",
            fields=[
                MemoryField(name="content", field_type=FieldType.STRING, merge_op=MergeOp.REPLACE)
            ],
        )
        loop.context_provider.get_memory_schemas = lambda _ctx: [schema]
        loop.context_provider.read_file_contents = {
            uri: MemoryFile(uri=uri, content="Old content", memory_type="experiences")
        }

        operations, _ = await _run_loop(loop)

        assert len(vlm.calls) == 2
        retry_prompt = "\n".join(_user_contents(vlm.calls[1]["messages"]))
        assert json.dumps(invalid_program, ensure_ascii=False) in retry_prompt
        assert "unterminated string literal" in retry_prompt
        assert "triple-single or triple-double quotes" in retry_prompt
        assert len(operations.upsert_operations) == 1
        operation = operations.upsert_operations[0]
        assert operation.uris == [uri]
        assert operation.memory_fields["content"] == body

    @pytest.mark.asyncio
    async def test_large_failed_program_is_excerpted_for_the_format_retry(self):
        invalid_program = 'head = "begin"\n' + "#" * 9000 + "missing-middle" + "#" * 9000 + "\n("
        vlm = ScriptedVLM([invalid_program, "sdk.commit()"])

        await _run_loop(_make_loop(vlm))

        retry_prompt = "\n".join(_user_contents(vlm.calls[1]["messages"]))
        assert "Middle of previous output omitted" in retry_prompt
        assert "missing-middle" not in retry_prompt
        assert json.dumps('head = "begin"', ensure_ascii=False)[1:-1] in retry_prompt
        assert "was never closed" in retry_prompt

    @pytest.mark.asyncio
    async def test_json_format_retry_keeps_its_existing_prompt(self):
        invalid_output = "not a JSON document"
        vlm = ScriptedVLM([invalid_output, '{"preferences": [], "delete_ids": []}'])

        operations, _ = await _run_loop(_make_loop(vlm), output_format="json")

        assert operations is not None
        assert len(vlm.calls) == 2
        retry_prompt = "\n".join(_user_contents(vlm.calls[1]["messages"]))
        assert "Your previous output could not be parsed as valid JSON" in retry_prompt
        assert invalid_output not in retry_prompt


class TestExtractionContentBoundary:
    @pytest.mark.asyncio
    @pytest.mark.parametrize("with_tools", [False, True])
    @pytest.mark.parametrize("structured", [False, True])
    async def test_extraction_cleans_local_text_without_mutating_response(
        self, with_tools, structured
    ):
        raw_content = "<think>internal reasoning</think>\n  sdk.commit()  "
        usage = {"prompt_tokens": 10, "completion_tokens": 5, "total_tokens": 15}
        response = (
            VLMResponse(content=raw_content, finish_reason="stop", usage=usage.copy())
            if structured
            else raw_content
        )
        vlm = ScriptedVLM([response])
        loop = _make_loop(vlm)
        loop.context_provider.get_tools = lambda: ["read"] if with_tools else []

        operations, _ = await _run_loop(loop)

        assert operations is not None
        assert len(vlm.calls) == 1
        assert bool(vlm.calls[0]["tools"]) is with_tools
        if structured:
            assert response.content == raw_content
            assert response.finish_reason == "stop"
            assert response.usage == usage

    @pytest.mark.asyncio
    @pytest.mark.parametrize("finish_reason", ["tool_calls", "length"])
    async def test_tool_call_respects_finish_reason(self, finish_reason):
        response = VLMResponse(
            content="<think>planning</think>I will read the file.",
            tool_calls=[ToolCall(id="read-1", name="read", arguments={"uri": "viking://user/test"})],
            finish_reason=finish_reason,
        )
        vlm = ScriptedVLM([response, VLMResponse(content="<think>done</think>sdk.commit()")])
        loop = _make_loop(vlm)
        loop.context_provider.get_tools = lambda: ["read"]
        execute = AsyncMock(return_value={"content": "test"})
        loop.context_provider.execute_tool = execute

        if finish_reason == "length":
            with pytest.raises(RuntimeError, match="output-token budget"):
                await _run_loop(loop)
            execute.assert_not_awaited()
            assert len(vlm.calls) == 1
        else:
            operations, _ = await _run_loop(loop)
            assert operations is not None
            execute.assert_awaited_once()
            assert len(vlm.calls) == 2
        assert response.content == "<think>planning</think>I will read the file."
