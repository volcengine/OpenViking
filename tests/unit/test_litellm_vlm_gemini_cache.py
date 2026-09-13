# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0
"""Tests for Gemini cache_control stripping workaround.

LiteLLM has an open bug (BerriAI/litellm#17304, PR #25659) where the Gemini
context-caching path leaves `tool_choice` in optional_params while also
setting `cachedContent`. Gemini then rejects the combination with:

    "CachedContent can not be used with GenerateContent request setting
    system_instruction, tools or tool_config."

OpenViking's memory-extraction ReAct loop adds `cache_control: ephemeral`
markers and passes `tool_choice="auto"`, so every Gemini call with tools
hits this 400. The LiteLLM VLM backend works around it by stripping
`cache_control` markers from messages when the resolved provider is
`gemini`.
"""

from openviking.models.vlm.backends.litellm_vlm import LiteLLMVLMProvider


def _vlm(model: str) -> LiteLLMVLMProvider:
    return LiteLLMVLMProvider({"api_key": "fake", "model": model, "max_tokens": 256})


class TestGeminiCacheControlStripping:
    def test_cache_control_stripped_for_gemini_with_tools(self):
        vlm = _vlm("gemini-3.1-flash-lite-preview")
        messages = [{"role": "user", "content": "hi", "cache_control": {"type": "ephemeral"}}]
        tools = [{"type": "function", "function": {"name": "read", "parameters": {}}}]
        kwargs = vlm._build_kwargs(
            model="gemini/gemini-3.1-flash-lite-preview",
            messages=messages,
            tools=tools,
        )
        assert all("cache_control" not in m for m in kwargs["messages"])
        assert kwargs["messages"][0]["content"] == "hi"
        assert kwargs["tools"] == tools

    def test_cache_control_preserved_for_gemini_without_tools(self):
        vlm = _vlm("gemini-3.1-flash-lite-preview")
        messages = [{"role": "user", "content": "hi", "cache_control": {"type": "ephemeral"}}]
        kwargs = vlm._build_kwargs(
            model="gemini/gemini-3.1-flash-lite-preview",
            messages=messages,
        )
        assert kwargs["messages"][0].get("cache_control") == {"type": "ephemeral"}

    def test_cache_control_preserved_for_anthropic(self):
        vlm = _vlm("claude-haiku-4-5")
        messages = [{"role": "user", "content": "hi", "cache_control": {"type": "ephemeral"}}]
        tools = [{"type": "function", "function": {"name": "read", "parameters": {}}}]
        kwargs = vlm._build_kwargs(
            model="anthropic/claude-haiku-4-5",
            messages=messages,
            tools=tools,
        )
        assert kwargs["messages"][0].get("cache_control") == {"type": "ephemeral"}

    def test_non_dict_messages_left_intact(self):
        vlm = _vlm("gemini-3.1-flash-lite-preview")
        sentinel = object()
        messages = [sentinel]
        tools = [{"type": "function", "function": {"name": "read", "parameters": {}}}]
        kwargs = vlm._build_kwargs(
            model="gemini/gemini-3.1-flash-lite-preview",
            messages=messages,
            tools=tools,
        )
        assert kwargs["messages"] == [sentinel]

    def test_empty_assistant_turn_stays_removed_for_gemini_with_tools(self):
        # The Gemini cache_control strip must run on the already-sanitized
        # messages, not the raw input, so a structurally empty assistant turn
        # removed by sanitize_openai_messages is not reintroduced.
        vlm = _vlm("gemini-3.1-flash-lite-preview")
        messages = [
            {"role": "user", "content": "hi", "cache_control": {"type": "ephemeral"}},
            {"role": "assistant", "content": ""},
            {"role": "user", "content": "again"},
        ]
        tools = [{"type": "function", "function": {"name": "read", "parameters": {}}}]
        kwargs = vlm._build_kwargs(
            model="gemini/gemini-3.1-flash-lite-preview",
            messages=messages,
            tools=tools,
        )
        assert all(not (m["role"] == "assistant" and not m["content"]) for m in kwargs["messages"])
        assert [m["role"] for m in kwargs["messages"]] == ["user", "user"]
        assert all("cache_control" not in m for m in kwargs["messages"])
