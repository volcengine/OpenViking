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


@pytest.mark.parametrize("builder_name", ["_build_text_kwargs", "_build_vision_kwargs"])
def test_sensenova_request_drops_historical_reasoning_content(builder_name):
    vlm = OpenAIVLM(
        {
            "provider": "openai",
            "model": "sensenova-6.8-flash-lite",
            "api_base": "https://token.sensenova.cn/v1",
            "reasoning_effort": "medium",
        }
    )
    assistant = {
        "role": "assistant",
        "content": " ",
        "reasoning_content": "private tool plan",
        "reasoning": "provider-compatible tool state",
        "tool_calls": [
            {
                "id": "call-1",
                "type": "function",
                "function": {"name": "read", "arguments": "{}"},
            }
        ],
    }
    messages = [
        {"role": "user", "content": "start"},
        assistant,
        {"role": "tool", "tool_call_id": "call-1", "content": "done"},
    ]

    kwargs = getattr(vlm, builder_name)(messages=messages)

    assert kwargs["messages"][1] == {
        key: value for key, value in assistant.items() if key != "reasoning_content"
    }
    assert kwargs["reasoning_effort"] == "medium"
    assert messages[1] == assistant


@pytest.mark.parametrize(
    "api_base",
    [
        "https://api.openai.com/v1",
        "https://token.sensenova.cn.example/v1",
        "http://127.0.0.1:8000/v1",
    ],
)
def test_other_openai_compatible_hosts_keep_historical_reasoning_content(api_base):
    vlm = OpenAIVLM(
        {
            "provider": "openai",
            "model": "reasoning-model",
            "api_base": api_base,
        }
    )
    assistant = {
        "role": "assistant",
        "content": "answer",
        "reasoning_content": "provider-required state",
    }

    kwargs = vlm._build_text_kwargs(messages=[assistant])

    assert kwargs["messages"] == [assistant]


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
