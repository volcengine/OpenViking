# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0
"""Tests for routing a configured ``reasoning_effort`` through ``extra_body``.

``_is_reasoning_model`` gates the native ``reasoning_effort`` kwarg on the
gpt-5/o-series prefixes, so for every other model name the configured value
was silently dropped and OpenAI-compatible providers (e.g. GLM-5.3 on Z.AI,
which defaults to ``max`` when unset) ran at their server-side default (#4686).
"""

from openviking.models.vlm.backends.glm_vlm import GLMVLM
from openviking.models.vlm.backends.openai_vlm import OpenAIVLM


def _glm(effort="low", **extra):
    cfg = {
        "provider": "glm",
        "model": "glm-5.3-flash",
        "api_key": "sk-x",
        "api_base": "https://api.z.ai/api/coding/paas/v4",
    }
    if effort is not None:
        cfg["reasoning_effort"] = effort
    cfg.update(extra)
    return GLMVLM(cfg)


def test_configured_effort_reaches_text_kwargs_extra_body():
    kwargs = _glm("low")._build_text_kwargs("hello")
    assert kwargs["extra_body"]["reasoning_effort"] == "low"
    assert "reasoning_effort" not in kwargs  # native kwarg stays OpenAI-family only


def test_configured_effort_reaches_vision_kwargs_extra_body():
    kwargs = _glm("low")._build_vision_kwargs("describe this")
    assert kwargs["extra_body"]["reasoning_effort"] == "low"


def test_unconfigured_effort_is_not_sent():
    # Zero-regression guard: backends that never set the field must not start
    # receiving a defaulted reasoning_effort their API may reject.
    kwargs = _glm(None)._build_text_kwargs("hello")
    assert "reasoning_effort" not in kwargs.get("extra_body", {})
    assert "reasoning_effort" not in kwargs


def test_explicit_extra_request_body_wins():
    vlm = _glm("low", extra_request_body={"reasoning_effort": "high"})
    kwargs = vlm._build_text_kwargs("hello")
    assert kwargs["extra_body"]["reasoning_effort"] == "high"


def test_openai_reasoning_models_keep_native_kwarg():
    vlm = OpenAIVLM(
        {
            "provider": "openai",
            "model": "gpt-5-mini",
            "api_key": "sk-x",
            "api_base": "https://example.invalid",
            "reasoning_effort": "low",
        }
    )
    kwargs = vlm._build_text_kwargs("hello")
    assert kwargs["reasoning_effort"] == "low"
    assert "reasoning_effort" not in kwargs.get("extra_body", {})


def test_dashscope_path_keeps_enable_thinking_only():
    vlm = OpenAIVLM(
        {
            "provider": "openai",
            "model": "qwen3-max",
            "api_key": "sk-x",
            "api_base": "https://dashscope.aliyuncs.com/compatible-mode/v1",
            "reasoning_effort": "low",
        }
    )
    kwargs = vlm._build_text_kwargs("hello", thinking=True)
    assert kwargs["extra_body"]["enable_thinking"] is True
    assert "reasoning_effort" not in kwargs["extra_body"]
