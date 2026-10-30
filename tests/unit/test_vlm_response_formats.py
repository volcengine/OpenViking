# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0
"""Tests for VLM response format handling (Issue #801)."""

from types import SimpleNamespace

import pytest

from openviking.models.vlm.backends.litellm_vlm import LiteLLMVLMProvider
from openviking.models.vlm.backends.openai_vlm import OpenAIVLM
from openviking.models.vlm.backends.volcengine_vlm import VolcEngineVLM
from openviking.models.vlm.base import VLMBase, VLMResponse


class TestVLMBaseResponseFormats:
    """Test VLMBase handles various response formats correctly."""

    class ConcreteVLM(VLMBase):
        """Concrete VLM implementation for testing."""

        def get_completion(self, prompt: str, thinking: bool = False) -> str:
            pass

        async def get_completion_async(self, prompt: str, thinking: bool = False) -> str:
            pass

        def get_vision_completion(
            self,
            prompt: str,
            images,
            thinking: bool = False,
        ) -> str:
            pass

        async def get_vision_completion_async(
            self,
            prompt: str,
            images,
            thinking: bool = False,
        ) -> str:
            pass

    @pytest.fixture()
    def vlm(self):
        return self.ConcreteVLM(
            {
                "api_key": "sk-test",
                "api_base": "https://api.openai.com/v1",
                "model": "gpt-4o-mini",
            }
        )

    def test_extract_content_from_str_response(self, vlm):
        assert (
            vlm._extract_content_from_response("plain string response") == "plain string response"
        )

    def test_extract_content_from_standard_openai_response(self, vlm):
        response = SimpleNamespace(
            choices=[SimpleNamespace(message=SimpleNamespace(content="standard response content"))]
        )
        assert vlm._extract_content_from_response(response) == "standard response content"


@pytest.mark.parametrize(
    ("vlm_type", "config"),
    [
        (OpenAIVLM, {"provider": "openai", "model": "gpt-5.6-terra"}),
        (LiteLLMVLMProvider, {"provider": "litellm", "model": "gpt-5.6-terra"}),
        (VolcEngineVLM, {"provider": "volcengine", "model": "test-model"}),
    ],
    ids=["openai", "litellm", "volcengine"],
)
def test_build_vlm_response_from_str_with_tools(vlm_type, config):
    vlm = vlm_type(config)
    response = vlm._build_vlm_response("plain string response", has_tools=True)

    assert isinstance(response, VLMResponse)
    assert response.content == "plain string response"
    assert response.tool_calls == []
    assert response.finish_reason == "stop"
    assert response.usage == {}


@pytest.mark.parametrize(
    ("vlm_type", "config"),
    [
        (OpenAIVLM, {"provider": "openai", "model": "gpt-5.6-terra"}),
        (LiteLLMVLMProvider, {"provider": "litellm", "model": "gpt-5.6-terra"}),
        (VolcEngineVLM, {"provider": "volcengine", "model": "test-model"}),
    ],
    ids=["openai", "litellm", "volcengine"],
)
def test_text_request_drops_empty_assistant_but_preserves_tool_only_turn(vlm_type, config):
    vlm = vlm_type(config)
    tool_call = {
        "id": "call-1",
        "type": "function",
        "function": {"name": "read", "arguments": "{}"},
    }
    messages = [
        {"role": "user", "content": "start"},
        {"role": "assistant", "content": None},
        {"role": "assistant", "content": "   "},
        {"role": "assistant", "content": None, "tool_calls": [tool_call]},
        {"role": "tool", "tool_call_id": "call-1", "content": "ok"},
    ]

    kwargs = vlm._build_text_kwargs(messages=messages)

    assert kwargs["messages"] == [messages[0], messages[3], messages[4]]
    assert messages[1] == {"role": "assistant", "content": None}


@pytest.mark.asyncio
async def test_openai_async_completion_from_str_with_tools(monkeypatch):
    async def create(**_kwargs):
        return "plain string response"

    client = SimpleNamespace(
        chat=SimpleNamespace(completions=SimpleNamespace(create=create)),
    )
    vlm = OpenAIVLM({"provider": "openai", "model": "gpt-5.6-terra"})
    monkeypatch.setattr(vlm, "get_async_client", lambda: client)

    response = await vlm.get_completion_async(
        messages=[{"role": "user", "content": "hello"}],
        tools=[
            {
                "type": "function",
                "function": {
                    "name": "test_tool",
                    "description": "A test tool",
                    "parameters": {"type": "object", "properties": {}},
                },
            }
        ],
    )

    assert isinstance(response, VLMResponse)
    assert response.content == "plain string response"
    assert response.tool_calls == []
    assert response.finish_reason == "stop"


@pytest.mark.asyncio
async def test_openai_async_completion_with_empty_tools_preserves_usage(monkeypatch):
    usage = SimpleNamespace(
        prompt_tokens=11,
        completion_tokens=4,
        total_tokens=15,
        prompt_tokens_details=None,
        completion_tokens_details=None,
    )
    raw_response = SimpleNamespace(
        choices=[
            SimpleNamespace(
                message=SimpleNamespace(content="plain response", tool_calls=None),
                finish_reason="stop",
            )
        ],
        usage=usage,
    )

    async def create(**kwargs):
        assert "tools" not in kwargs
        return raw_response

    client = SimpleNamespace(
        chat=SimpleNamespace(completions=SimpleNamespace(create=create)),
    )
    vlm = OpenAIVLM({"provider": "openai", "model": "gpt-5.6-terra"})
    monkeypatch.setattr(vlm, "get_async_client", lambda: client)

    response = await vlm.get_completion_async(
        messages=[{"role": "user", "content": "hello"}],
        tools=[],
    )

    assert isinstance(response, VLMResponse)
    assert response.content == "plain response"
    assert response.tool_calls == []
    assert response.finish_reason == "stop"
    assert response.usage == {
        "prompt_tokens": 11,
        "completion_tokens": 4,
        "total_tokens": 15,
        "prompt_tokens_details": None,
    }


@pytest.mark.parametrize(
    ("vlm_type", "config"),
    [
        (OpenAIVLM, {"provider": "openai", "model": "gpt-5.6-terra"}),
        (LiteLLMVLMProvider, {"provider": "litellm", "model": "gpt-5.6-terra"}),
        (VolcEngineVLM, {"provider": "volcengine", "model": "test-model"}),
    ],
    ids=["openai", "litellm", "volcengine"],
)
def test_tool_completion_preserves_success_with_partial_usage(
    monkeypatch,
    vlm_type,
    config,
):
    calls = 0
    raw_response = SimpleNamespace(
        choices=[
            SimpleNamespace(
                message=SimpleNamespace(content="plain response", tool_calls=None),
                finish_reason="stop",
            )
        ],
        usage=SimpleNamespace(prompt_tokens=None, completion_tokens=None, total_tokens=17),
    )

    def complete(**_kwargs):
        nonlocal calls
        calls += 1
        return raw_response

    vlm = vlm_type(config)
    if vlm_type is LiteLLMVLMProvider:
        monkeypatch.setattr(
            "openviking.models.vlm.backends.litellm_vlm.completion",
            complete,
        )
    else:
        client = SimpleNamespace(
            chat=SimpleNamespace(completions=SimpleNamespace(create=complete)),
        )
        monkeypatch.setattr(vlm, "get_client", lambda: client)

    response = vlm.get_completion(
        messages=[{"role": "user", "content": "hello"}],
        tools=[
            {
                "type": "function",
                "function": {
                    "name": "test_tool",
                    "description": "Test tool",
                    "parameters": {"type": "object", "properties": {}},
                },
            }
        ],
    )

    assert calls == 1 and isinstance(response, VLMResponse) and response.content == "plain response"
    assert response.usage["prompt_tokens"] == 0
    assert response.usage["completion_tokens"] == 0
    assert response.usage["total_tokens"] == 17
    tracked = vlm.get_token_usage()["total_usage"]
    assert (
        tracked["prompt_tokens"],
        tracked["completion_tokens"],
        tracked["call_count"],
    ) == (0, 0, 1)
