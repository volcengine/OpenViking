# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0
"""Tests for `response_format` passthrough in VLM backends (issue #1541).

Memory extraction relies on the model returning a JSON object as message
content when tools are disabled. Weak models otherwise return plain prose,
which fails parsing and stores zero memories. The fix lets callers pass an
OpenAI-style ``response_format`` (e.g. ``{"type": "json_object"}``) that the
backends forward to the provider. These tests pin that passthrough and verify
that omitting it keeps the request body unchanged (no regression).
"""

from openviking.models.vlm.backends.litellm_vlm import LiteLLMVLMProvider
from openviking.models.vlm.backends.openai_vlm import OpenAIVLM

_JSON_OBJECT = {"type": "json_object"}


class TestOpenAIResponseFormat:
    def _vlm(self, **overrides):
        config = {
            "api_key": "sk-test",
            "model": "gpt-4o-mini",
            "api_base": "https://api.openai.com/v1",
            "max_tokens": 256,
        }
        config.update(overrides)
        return OpenAIVLM(config)

    def test_omitted_by_default(self):
        kwargs = self._vlm()._build_text_kwargs(prompt="hi")
        assert "response_format" not in kwargs

    def test_injected_when_provided(self):
        kwargs = self._vlm()._build_text_kwargs(prompt="hi", response_format=_JSON_OBJECT)
        assert kwargs["response_format"] == _JSON_OBJECT

    def test_injected_for_reasoning_model(self):
        kwargs = self._vlm(model="gpt-5-mini")._build_text_kwargs(
            prompt="hi", response_format=_JSON_OBJECT
        )
        assert kwargs["response_format"] == _JSON_OBJECT
        # reasoning-model translation still applies alongside response_format
        assert kwargs["max_completion_tokens"] == 256


class TestLiteLLMResponseFormat:
    def _vlm(self, **overrides):
        config = {
            "api_key": "sk-test",
            "model": "gpt-4o-mini",
            "api_base": "https://api.openai.com/v1",
            "max_tokens": 256,
        }
        config.update(overrides)
        return LiteLLMVLMProvider(config)

    def test_omitted_by_default(self):
        kwargs = self._vlm()._build_text_kwargs(prompt="hi")
        assert "response_format" not in kwargs

    def test_injected_when_provided(self):
        kwargs = self._vlm()._build_text_kwargs(prompt="hi", response_format=_JSON_OBJECT)
        assert kwargs["response_format"] == _JSON_OBJECT
