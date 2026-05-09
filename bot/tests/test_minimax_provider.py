# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0
"""Tests for MiniMax provider support (MiniMax-M2.7, MiniMax-M2.7-highspeed)."""

from urllib.parse import urlparse

from vikingbot.providers.registry import ProviderSpec, find_by_model, find_by_name


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
        for model in ("minimax-m2.7", "MINIMAX-M2.7", "MiniMax-M2.7"):
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


class TestMiniMaxModelPrefixResolution:
    """Tests for LiteLLM model prefix resolution with MiniMax models."""

    def _resolve_model(self, model: str) -> str:
        """Reproduce _resolve_model logic from LiteLLMProvider."""
        from vikingbot.providers.registry import find_by_model

        spec = find_by_model(model)
        if spec and spec.litellm_prefix:
            if not any(model.startswith(s) for s in spec.skip_prefixes):
                model = f"{spec.litellm_prefix}/{model}"
        return model

    def test_m2_7_gets_minimax_prefix(self):
        """MiniMax-M2.7 should be prefixed as minimax/MiniMax-M2.7."""
        resolved = self._resolve_model("MiniMax-M2.7")
        assert resolved == "minimax/MiniMax-M2.7"

    def test_m2_7_highspeed_gets_minimax_prefix(self):
        """MiniMax-M2.7-highspeed should be prefixed as minimax/MiniMax-M2.7-highspeed."""
        resolved = self._resolve_model("MiniMax-M2.7-highspeed")
        assert resolved == "minimax/MiniMax-M2.7-highspeed"

    def test_already_prefixed_model_not_double_prefixed(self):
        """Model already carrying minimax/ prefix must not be double-prefixed."""
        resolved = self._resolve_model("minimax/MiniMax-M2.7")
        assert resolved == "minimax/MiniMax-M2.7"


class TestMiniMaxSystemMessageHandling:
    """Tests for MiniMax system message merging in both LLM providers."""

    # ------------------------------------------------------------------ #
    # Helpers
    # ------------------------------------------------------------------ #

    def _handle_system_litellm(self, model: str, messages: list[dict]) -> list[dict]:
        """Call the LiteLLMProvider._handle_system_message without a real provider."""
        from vikingbot.providers.litellm_provider import LiteLLMProvider

        # Instantiate without a real API key — we only call the static-ish helper.
        provider = LiteLLMProvider.__new__(LiteLLMProvider)
        provider._gateway = None
        return provider._handle_system_message(model, messages)

    def _handle_system_openai_compat(self, model: str, messages: list[dict]) -> list[dict]:
        """Call the OpenAICompatibleProvider._handle_system_message."""
        from vikingbot.providers.openai_compatible_provider import OpenAICompatibleProvider

        provider = OpenAICompatibleProvider.__new__(OpenAICompatibleProvider)
        return provider._handle_system_message(model, messages)

    # ------------------------------------------------------------------ #
    # LiteLLMProvider tests (model name after prefix resolution)
    # ------------------------------------------------------------------ #

    def test_litellm_system_message_merged_for_m2_7(self):
        """System message is merged into the first user message for minimax/MiniMax-M2.7."""
        messages = [
            {"role": "system", "content": "You are a helpful assistant."},
            {"role": "user", "content": "Hello!"},
        ]
        result = self._handle_system_litellm("minimax/MiniMax-M2.7", messages)
        assert all(m["role"] != "system" for m in result), "System message not removed"
        user_content = next(m["content"] for m in result if m["role"] == "user")
        assert "You are a helpful assistant." in user_content
        assert "Hello!" in user_content

    def test_litellm_system_message_merged_for_m2_7_highspeed(self):
        """System message is merged for minimax/MiniMax-M2.7-highspeed."""
        messages = [
            {"role": "system", "content": "Be concise."},
            {"role": "user", "content": "What is 2+2?"},
        ]
        result = self._handle_system_litellm("minimax/MiniMax-M2.7-highspeed", messages)
        assert all(m["role"] != "system" for m in result)
        user_content = next(m["content"] for m in result if m["role"] == "user")
        assert "Be concise." in user_content

    def test_litellm_multiple_system_messages_combined(self):
        """Multiple system messages are combined before merging."""
        messages = [
            {"role": "system", "content": "Rule 1."},
            {"role": "system", "content": "Rule 2."},
            {"role": "user", "content": "Go!"},
        ]
        result = self._handle_system_litellm("minimax/MiniMax-M2.7", messages)
        assert all(m["role"] != "system" for m in result)
        user_content = next(m["content"] for m in result if m["role"] == "user")
        assert "Rule 1." in user_content
        assert "Rule 2." in user_content

    def test_litellm_no_system_message_passthrough(self):
        """Messages without a system role are returned unchanged."""
        messages = [
            {"role": "user", "content": "Hello!"},
            {"role": "assistant", "content": "Hi there!"},
        ]
        result = self._handle_system_litellm("minimax/MiniMax-M2.7", messages)
        assert result == messages

    def test_litellm_non_minimax_model_not_affected(self):
        """System messages for non-MiniMax models must not be touched."""
        messages = [
            {"role": "system", "content": "You are a helpful assistant."},
            {"role": "user", "content": "Hello!"},
        ]
        result = self._handle_system_litellm("anthropic/claude-opus-4-5", messages)
        assert result == messages

    # ------------------------------------------------------------------ #
    # OpenAICompatibleProvider tests (raw model name, no prefix)
    # ------------------------------------------------------------------ #

    def test_openai_compat_system_message_merged_for_m2_7(self):
        """System message is merged for MiniMax-M2.7 in OpenAICompatibleProvider."""
        messages = [
            {"role": "system", "content": "You are a helpful assistant."},
            {"role": "user", "content": "Hello!"},
        ]
        result = self._handle_system_openai_compat("MiniMax-M2.7", messages)
        assert all(m["role"] != "system" for m in result)
        user_content = next(m["content"] for m in result if m["role"] == "user")
        assert "You are a helpful assistant." in user_content

    def test_openai_compat_no_system_message_passthrough(self):
        """Messages without a system role pass through unchanged."""
        messages = [
            {"role": "user", "content": "Hello!"},
        ]
        result = self._handle_system_openai_compat("MiniMax-M2.7", messages)
        assert result == messages

    def test_openai_compat_system_only_creates_user_message(self):
        """System-only messages create a synthetic user message."""
        messages = [
            {"role": "system", "content": "You are a bot."},
        ]
        result = self._handle_system_openai_compat("MiniMax-M2.7", messages)
        assert any(m["role"] == "user" for m in result)
        assert all(m["role"] != "system" for m in result)
