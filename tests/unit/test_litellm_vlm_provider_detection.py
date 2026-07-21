# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0
"""Tests for LiteLLM VLM provider detection and model prefix resolution."""

import os

import pytest

from openviking.models.vlm.backends.litellm_vlm import (
    LiteLLMVLMProvider,
    detect_provider_by_model,
)

NEMOTRON_MODEL = "nvidia/nemotron-3-nano-omni-30b-a3b-reasoning"
NEMOTRON_LITELLM_MODEL = f"nvidia_nim/{NEMOTRON_MODEL}"


def _vlm(model: str) -> LiteLLMVLMProvider:
    return LiteLLMVLMProvider({"model": model, "provider": "litellm"})


class TestLiteLLMVLMProviderDetection:
    def test_nvidia_nim_detected_by_nemotron_keyword(self):
        assert detect_provider_by_model(NEMOTRON_MODEL) == "nvidia_nim"

    def test_nvidia_nim_detected_by_existing_prefix(self):
        assert detect_provider_by_model(NEMOTRON_LITELLM_MODEL) == "nvidia_nim"

    def test_nvidia_nim_prefix_applied_to_nemotron_model(self):
        assert _vlm(NEMOTRON_MODEL)._resolve_model(NEMOTRON_MODEL) == NEMOTRON_LITELLM_MODEL

    def test_existing_nvidia_nim_prefix_is_not_rewritten(self):
        model = "nvidia_nim/nvidia/nim-fake"

        assert detect_provider_by_model(model) == "nvidia_nim"
        assert _vlm(model)._resolve_model(model) == model

    def test_nvidia_nim_setup_env_uses_nvidia_key(self, monkeypatch):
        monkeypatch.delenv("NVIDIA_NIM_API_KEY", raising=False)
        monkeypatch.delenv("OPENAI_API_KEY", raising=False)

        vlm = LiteLLMVLMProvider(
            {"api_key": "fake", "model": NEMOTRON_MODEL, "provider": "litellm"}
        )

        assert vlm._detected_provider == "nvidia_nim"
        assert "OPENAI_API_KEY" not in os.environ
        assert os.environ["NVIDIA_NIM_API_KEY"] == "fake"

    def test_openai_prefixed_nemotron_model_is_left_alone(self):
        model = f"openai/{NEMOTRON_MODEL}"

        assert _vlm(model)._resolve_model(model) == model

    @pytest.mark.parametrize(
        "model",
        [
            "github_copilot/claude-opus-4.8",
            "github_copilot/gpt-4o",
        ],
    )
    def test_github_copilot_route_is_left_alone(self, model):
        assert detect_provider_by_model(model) is None
        assert _vlm(model)._resolve_model(model) == model

    @pytest.mark.parametrize(
        ("model", "provider"),
        [
            ("gemini-3.1-flash-lite-preview", "gemini"),
            ("claude-haiku-4-5", "anthropic"),
            ("gpt-4o", "openai"),
        ],
    )
    def test_existing_provider_detection_still_matches(self, model, provider):
        assert detect_provider_by_model(model) == provider
