# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0
"""Tests for LiteLLM thinking parameter scoping to DashScope providers."""

from openviking.models.vlm.backends.litellm_vlm import LiteLLMVLMProvider


class TestLiteLLMThinkingParam:
    """Test that enable_thinking is only sent to DashScope-compatible providers."""

    def _make_provider(self, model: str, **extra_config) -> LiteLLMVLMProvider:
        config = {"model": model, "provider": "litellm", **extra_config}
        return LiteLLMVLMProvider(config)

    def test_litellm_dashscope_thinking_disabled(self):
        """DashScope model with thinking=False should set enable_thinking=False in extra_body."""
        vlm = self._make_provider("qwen-plus")
        model = vlm._resolve_model("qwen-plus")
        messages = [{"role": "user", "content": "hello"}]

        kwargs = vlm._build_kwargs(model, messages, thinking=False)

        assert "extra_body" in kwargs
        assert kwargs["extra_body"]["enable_thinking"] is False

    def test_litellm_dashscope_thinking_enabled(self):
        """DashScope model with thinking=True should set enable_thinking=True in extra_body."""
        vlm = self._make_provider("qwen-plus")
        model = vlm._resolve_model("qwen-plus")
        messages = [{"role": "user", "content": "hello"}]

        kwargs = vlm._build_kwargs(model, messages, thinking=True)

        assert "extra_body" in kwargs
        assert kwargs["extra_body"]["enable_thinking"] is True

    def test_litellm_non_dashscope_no_thinking_field(self):
        """Non-DashScope model should NOT have enable_thinking in extra_body."""
        vlm = self._make_provider("gpt-4o")
        model = vlm._resolve_model("gpt-4o")
        messages = [{"role": "user", "content": "hello"}]

        kwargs = vlm._build_kwargs(model, messages, thinking=False)

        extra_body = kwargs.get("extra_body", {})
        assert "enable_thinking" not in extra_body

    def test_litellm_non_dashscope_thinking_true_no_field(self):
        """Non-DashScope model with thinking=True should still NOT have enable_thinking."""
        vlm = self._make_provider("gpt-4o")
        model = vlm._resolve_model("gpt-4o")
        messages = [{"role": "user", "content": "hello"}]

        kwargs = vlm._build_kwargs(model, messages, thinking=True)

        extra_body = kwargs.get("extra_body", {})
        assert "enable_thinking" not in extra_body

    def test_litellm_anthropic_no_thinking_field(self):
        """Anthropic model should NOT have enable_thinking in extra_body."""
        vlm = self._make_provider("claude-3-opus")
        model = vlm._resolve_model("claude-3-opus")
        messages = [{"role": "user", "content": "hello"}]

        kwargs = vlm._build_kwargs(model, messages, thinking=False)

        extra_body = kwargs.get("extra_body", {})
        assert "enable_thinking" not in extra_body

    def test_litellm_ollama_no_thinking_field(self):
        """Ollama model should NOT have enable_thinking in extra_body."""
        vlm = self._make_provider("ollama/llama3")
        model = vlm._resolve_model("ollama/llama3")
        messages = [{"role": "user", "content": "hello"}]

        kwargs = vlm._build_kwargs(model, messages, thinking=True)

        extra_body = kwargs.get("extra_body", {})
        assert "enable_thinking" not in extra_body

    def test_dashscope_detected_via_model_name(self):
        """Provider detection via model name 'dashscope' keyword should trigger enable_thinking."""
        vlm = self._make_provider("dashscope/custom-model")
        model = vlm._resolve_model("dashscope/custom-model")
        messages = [{"role": "user", "content": "hello"}]

        kwargs = vlm._build_kwargs(model, messages, thinking=True)

        assert "extra_body" in kwargs
        assert kwargs["extra_body"]["enable_thinking"] is True
