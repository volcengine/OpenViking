# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0
"""Exercise real LiteLLM image converters without network or model calls."""

import json
from copy import deepcopy
from types import SimpleNamespace

import litellm
import pytest
from litellm.types.utils import LlmProviders
from litellm.utils import ProviderConfigManager
from vikingbot.providers.vlm_adapter import VLMProviderAdapter

from openviking.models.vlm.backends.litellm_vlm import LiteLLMVLMProvider


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "model,protocol",
    [
        ("openai/future-claude-vision", "openai"),
        ("dashscope/qwen3-vl-plus", "openai"),
        ("moonshot/kimi-k2.5", "openai"),
        ("zai/glm-4.6v", "openai"),
        ("openrouter/qwen/qwen3-vl-32b-instruct", "openai"),
        ("gemini/gemini-2.5-flash", "gemini"),
        ("ollama/llava", "ollama"),
        ("ollama_chat/llama3.2-vision", "ollama_chat"),
        ("bedrock/anthropic.claude-3-5-sonnet-20241022-v2:0", "bedrock"),
    ],
)
async def test_tool_images_survive_actual_litellm_protocol_conversion(monkeypatch, model, protocol):
    encoded = "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAwMCAO+a5XsAAAAASUVORK5CYII="
    image = {"type": "image_url", "image_url": {"url": f"data:image/png;base64,{encoded}"}}
    tools = [
        {
            "type": "function",
            "function": {"name": "read_image", "parameters": {"type": "object", "properties": {}}},
        }
    ]
    messages = [
        {"role": "user", "content": "Read the source image."},
        {
            "role": "assistant",
            "content": None,
            "reasoning_content": "Read the original evidence.",
            "tool_calls": [
                {
                    "id": "read-1",
                    "type": "function",
                    "function": {"name": "read_image", "arguments": "{}"},
                }
            ],
        },
        {
            "role": "tool",
            "tool_call_id": "read-1",
            "name": "read_image",
            "content": [{"type": "text", "text": "viking://resources/image.png"}, image],
        },
    ]
    original = deepcopy(messages)
    vlm = LiteLLMVLMProvider({"provider": "litellm", "model": model})
    captured = []

    async def convert(**kwargs):
        provider, raw_model = kwargs["model"].split("/", 1)
        config = ProviderConfigManager.get_provider_chat_config(
            model=raw_model, provider=LlmProviders(provider)
        )
        if isinstance(config, litellm.VertexGeminiConfig):
            # Gemini uses this converter in its custom sync/async request path.
            wire = config._transform_messages(messages=kwargs["messages"], model=raw_model)
        else:
            wire = config.transform_request(
                model=raw_model,
                messages=kwargs["messages"],
                optional_params={"tools": deepcopy(tools)},
                litellm_params={},
                headers={},
            )
        captured.append(wire)
        return "image delivered"

    monkeypatch.setattr("openviking.models.vlm.backends.litellm_vlm.acompletion", convert)
    adapter = VLMProviderAdapter(vlm, model, langfuse_client=SimpleNamespace(enabled=False))
    assert adapter.supports_tool_result_media()
    response = await adapter.chat(messages, tools=tools)
    assert response.content == "image delivered"
    wire = captured[0]
    assert "viking://resources/image.png" in json.dumps(wire)
    if protocol == "openai":
        assert wire["messages"][-1]["content"][-1] == image
        assert isinstance(wire["messages"][-2]["content"], str)
    elif protocol == "gemini":
        blobs = [p["inline_data"] for m in wire for p in m["parts"] if "inline_data" in p]
        assert blobs == [{"data": encoded, "mime_type": "image/png"}]
    elif protocol == "ollama":
        assert wire["images"] == [encoded]
    elif protocol == "ollama_chat":
        assert wire["messages"][-1]["images"] == [encoded]
    else:
        images = [p["image"] for m in wire["messages"] for p in m["content"] if "image" in p]
        assert images == [{"format": "png", "source": {"bytes": encoded}}]
    assert messages == original
