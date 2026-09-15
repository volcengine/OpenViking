# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0
"""Tests that Ollama's ``num_ctx`` reaches the request's ``options`` object.

LiteLLM emits ``extra_body`` keys at the top level of the Ollama JSON body, but
Ollama only honours ``num_ctx`` inside ``options``. The litellm backend must
therefore pass ``num_ctx`` as a direct kwarg, which LiteLLM's Ollama
transformation places under ``options`` next to ``temperature``.
"""

import litellm
from litellm.llms.ollama.chat.transformation import OllamaChatConfig

from openviking.models.vlm.backends.litellm_vlm import (
    OLLAMA_DEFAULT_NUM_CTX,
    LiteLLMVLMProvider,
)

MESSAGES = [{"role": "user", "content": "hello"}]


def _make_provider(model: str, **extra_config) -> LiteLLMVLMProvider:
    config = {
        "model": model,
        "provider": "litellm",
        "api_base": "http://127.0.0.1:11434",
        "temperature": 0.0,
        **extra_config,
    }
    return LiteLLMVLMProvider(config)


def _ollama_request_body(kwargs: dict) -> dict:
    """Run built kwargs through LiteLLM's own Ollama chat transformation."""
    model = kwargs["model"].split("/", 1)[1]
    passthrough = {
        k: v
        for k, v in kwargs.items()
        if k not in {"model", "messages", "api_base", "api_key", "timeout", "extra_headers"}
    }
    optional_params = litellm.get_optional_params(
        model=model, custom_llm_provider="ollama_chat", **passthrough
    )
    return OllamaChatConfig().transform_request(
        model=model,
        messages=kwargs["messages"],
        optional_params=optional_params,
        litellm_params={},
        headers={},
    )


class TestOllamaNumCtxKwargs:
    def test_default_num_ctx_is_direct_kwarg_not_extra_body(self):
        vlm = _make_provider("ollama/qwen3.5:4b")
        kwargs = vlm._build_kwargs(vlm._resolve_model("ollama/qwen3.5:4b"), MESSAGES)

        assert kwargs["num_ctx"] == OLLAMA_DEFAULT_NUM_CTX
        assert "num_ctx" not in kwargs["extra_body"]
        # think is a top-level Ollama field and stays in extra_body.
        assert kwargs["extra_body"] == {"think": False}

    def test_user_num_ctx_in_extra_request_body_is_promoted(self):
        vlm = _make_provider("ollama_chat/qwen3.5:4b", extra_request_body={"num_ctx": 32768})
        kwargs = vlm._build_kwargs(vlm._resolve_model("ollama_chat/qwen3.5:4b"), MESSAGES)

        assert kwargs["num_ctx"] == 32768
        assert "num_ctx" not in kwargs["extra_body"]
        # The provider's own config dict is not mutated by the promotion.
        assert vlm.extra_request_body == {"num_ctx": 32768}

    def test_options_dict_is_merged_into_kwargs(self):
        vlm = _make_provider(
            "ollama/qwen3.5:4b",
            extra_request_body={"options": {"num_ctx": 8192, "num_predict": 512}},
        )
        kwargs = vlm._build_kwargs(vlm._resolve_model("ollama/qwen3.5:4b"), MESSAGES)

        assert kwargs["num_ctx"] == 8192
        assert kwargs["num_predict"] == 512
        assert "options" not in kwargs["extra_body"]
        assert "options" not in kwargs

    def test_top_level_num_ctx_wins_over_options_num_ctx(self):
        vlm = _make_provider(
            "ollama/qwen3.5:4b",
            extra_request_body={"num_ctx": 4096, "options": {"num_ctx": 8192}},
        )
        kwargs = vlm._build_kwargs(vlm._resolve_model("ollama/qwen3.5:4b"), MESSAGES)

        assert kwargs["num_ctx"] == 4096

    def test_options_cannot_overwrite_reserved_kwargs(self):
        vlm = _make_provider(
            "ollama/qwen3.5:4b",
            extra_request_body={"options": {"model": "evil", "messages": [], "num_ctx": 8192}},
        )
        model = vlm._resolve_model("ollama/qwen3.5:4b")
        kwargs = vlm._build_kwargs(model, MESSAGES)

        assert kwargs["model"] == model
        assert kwargs["messages"] == MESSAGES
        assert kwargs["num_ctx"] == 8192

    def test_non_ollama_model_untouched(self):
        vlm = _make_provider("gpt-4o-mini", api_key="sk-test", extra_request_body={"seed": 7})
        kwargs = vlm._build_kwargs(vlm._resolve_model("gpt-4o-mini"), MESSAGES)

        assert "num_ctx" not in kwargs
        assert kwargs["extra_body"] == {"seed": 7}


class TestOllamaNumCtxReachesOptions:
    """End-to-end through LiteLLM's Ollama transformation, no server needed."""

    def test_default_num_ctx_lands_in_options_with_temperature(self):
        vlm = _make_provider("ollama/qwen3.5:4b")
        kwargs = vlm._build_kwargs(vlm._resolve_model("ollama/qwen3.5:4b"), MESSAGES)

        body = _ollama_request_body(kwargs)

        assert body["options"]["num_ctx"] == OLLAMA_DEFAULT_NUM_CTX
        assert body["options"]["temperature"] == 0.0
        assert "num_ctx" not in body

    def test_user_options_land_in_options_without_dropping_temperature(self):
        vlm = _make_provider(
            "ollama/qwen3.5:4b",
            extra_request_body={"options": {"num_ctx": 8192, "num_predict": 256}},
        )
        kwargs = vlm._build_kwargs(vlm._resolve_model("ollama/qwen3.5:4b"), MESSAGES)

        body = _ollama_request_body(kwargs)

        assert body["options"]["num_ctx"] == 8192
        assert body["options"]["num_predict"] == 256
        assert body["options"]["temperature"] == 0.0
