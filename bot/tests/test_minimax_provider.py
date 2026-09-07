# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0
"""Tests for MiniMax provider support (MiniMax-M3, MiniMax-M2.7, MiniMax-M2.7-highspeed)."""

from copy import deepcopy
from types import SimpleNamespace
from urllib.parse import urlparse

import pytest
from vikingbot.providers.registry import ProviderSpec, find_by_model, find_by_name
from vikingbot.providers.vlm_adapter import VLMProviderAdapter

from openviking.models.vlm.backends.litellm_vlm import LiteLLMVLMProvider


class TestMiniMaxRegistry:
    """Tests for MiniMax provider registry entries."""

    def test_minimax_spec_exists(self):
        """MiniMax must be registered in the PROVIDERS tuple."""
        spec = find_by_name("minimax")
        assert spec is not None, "MiniMax provider not found in registry"
        assert isinstance(spec, ProviderSpec)

    def test_minimax_spec_fields(self):
        """Verify MiniMax ProviderSpec has correct field values."""
        spec = find_by_name("minimax")
        assert spec.name == "minimax"
        assert spec.env_key == "MINIMAX_API_KEY"
        assert spec.display_name == "MiniMax"
        assert spec.litellm_prefix == "minimax"
        assert "minimax/" in spec.skip_prefixes
        assert spec.default_api_base == "https://api.minimax.io/v1"
        assert not spec.is_gateway
        assert not spec.is_local

    def test_minimax_m3_matched_by_keyword(self):
        """MiniMax-M3 should be matched to the minimax ProviderSpec."""
        spec = find_by_model("MiniMax-M3")
        assert spec is not None, "MiniMax-M3 not matched to any provider"
        assert spec.name == "minimax"

    def test_minimax_m2_7_matched_by_keyword(self):
        """MiniMax-M2.7 should be matched to the minimax ProviderSpec."""
        spec = find_by_model("MiniMax-M2.7")
        assert spec is not None, "MiniMax-M2.7 not matched to any provider"
        assert spec.name == "minimax"

    def test_minimax_m2_7_highspeed_matched_by_keyword(self):
        """MiniMax-M2.7-highspeed should be matched to the minimax ProviderSpec."""
        spec = find_by_model("MiniMax-M2.7-highspeed")
        assert spec is not None, "MiniMax-M2.7-highspeed not matched to any provider"
        assert spec.name == "minimax"

    def test_minimax_keyword_is_case_insensitive(self):
        """Model name matching must be case-insensitive."""
        for model in ("minimax-m2.7", "MINIMAX-M2.7", "MiniMax-M2.7", "MiniMax-m3"):
            spec = find_by_model(model)
            assert spec is not None, f"{model!r} not matched"
            assert spec.name == "minimax"

    def test_minimax_api_base_uses_international_domain(self):
        """Default API base must point to the international endpoint."""
        spec = find_by_name("minimax")
        parsed = urlparse(spec.default_api_base)
        assert parsed.scheme == "https"
        assert parsed.hostname == "api.minimax.io", (
            "Default base URL must use international domain api.minimax.io, "
            "not the mainland China domain api.minimaxi.com"
        )


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
    assert messages == original
