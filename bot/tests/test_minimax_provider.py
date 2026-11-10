# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0
"""Tests for MiniMax provider support (MiniMax-M3, MiniMax-M2.7, MiniMax-M2.7-highspeed)."""

from copy import deepcopy
from types import SimpleNamespace

import pytest
from vikingbot.providers.vlm_adapter import VLMProviderAdapter

from openviking.models.vlm.backends.litellm_vlm import LiteLLMVLMProvider


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "model",
    ["MiniMax-M3", "MiniMax-M2.7", "MiniMax-M2.7-highspeed", "minimax/MiniMax-M2.7"],
)
async def test_minimax_chat_uses_core_litellm_backend(monkeypatch, model):
    """The active adapter forwards MiniMax messages and resolves the provider prefix."""
    captured = {}

    async def fake_acompletion(**kwargs):
        captured.update(kwargs)
        return SimpleNamespace(
            choices=[
                SimpleNamespace(
                    message=SimpleNamespace(content="Hello!", tool_calls=None),
                    finish_reason="stop",
                )
            ],
            usage=None,
        )

    monkeypatch.setattr("openviking.models.vlm.backends.litellm_vlm.acompletion", fake_acompletion)
    vlm = LiteLLMVLMProvider({"provider": "minimax", "model": model})
    provider = VLMProviderAdapter(vlm, default_model=model)
    messages = [
        {"role": "system", "content": "You are a helpful assistant."},
        {"role": "user", "content": "Hi!"},
    ]
    original = deepcopy(messages)

    response = await provider.chat(messages=messages)

    assert response.content == "Hello!"
    assert captured["model"] == f"minimax/{model.removeprefix('minimax/')}"
    assert captured["messages"] == original
