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
