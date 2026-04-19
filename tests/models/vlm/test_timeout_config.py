# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0
"""Tests for ``vlm.timeout`` configuration propagation.

Before this was wired through, ``_build_openai_client_kwargs`` exposed a
``timeout`` parameter (#1208) but callers never passed it, so the default
60.0s was always used and end users could not override it via ``ov.conf``.
These tests lock in that the config value flows through to the underlying
OpenAI and LiteLLM clients.
"""

from unittest import mock

import pytest
from pydantic import ValidationError

from openviking.models.vlm.backends.openai_vlm import (
    OpenAIVLM,
    _build_openai_client_kwargs,
)
from openviking_cli.utils.config.vlm_config import VLMConfig


def test_vlm_config_accepts_timeout():
    cfg = VLMConfig(model="gpt-4o-mini", api_key="sk-x", timeout=120.0)
    assert cfg.timeout == 120.0


def test_vlm_config_timeout_defaults_to_60():
    cfg = VLMConfig(model="gpt-4o-mini", api_key="sk-x")
    assert cfg.timeout == 60.0


def test_vlm_config_rejects_non_positive_timeout():
    with pytest.raises(ValidationError):
        VLMConfig(model="gpt-4o-mini", api_key="sk-x", timeout=0)


def test_build_openai_client_kwargs_default_timeout():
    kwargs = _build_openai_client_kwargs("openai", "sk-x", "https://example.invalid", None, None)
    assert kwargs["timeout"] == 60.0


def test_build_openai_client_kwargs_custom_timeout():
    kwargs = _build_openai_client_kwargs(
        "openai",
        "sk-x",
        "https://example.invalid",
        None,
        None,
        timeout=120.0,
    )
    assert kwargs["timeout"] == 120.0


def test_openai_vlm_propagates_config_timeout():
    vlm = OpenAIVLM(
        {
            "provider": "openai",
            "model": "gpt-4o-mini",
            "api_key": "sk-x",
            "api_base": "https://example.invalid",
            "timeout": 120.0,
        }
    )
    assert vlm.timeout == 120.0

    with mock.patch("openviking.models.vlm.backends.openai_vlm.openai.OpenAI") as fake:
        vlm.get_client()
    assert fake.call_args.kwargs.get("timeout") == 120.0


def test_openai_vlm_defaults_to_60_timeout_when_config_omits_it():
    vlm = OpenAIVLM(
        {
            "provider": "openai",
            "model": "gpt-4o-mini",
            "api_key": "sk-x",
            "api_base": "https://example.invalid",
        }
    )
    assert vlm.timeout == 60.0

    with mock.patch("openviking.models.vlm.backends.openai_vlm.openai.OpenAI") as fake:
        vlm.get_client()
    assert fake.call_args.kwargs.get("timeout") == 60.0


def test_litellm_build_kwargs_includes_timeout():
    from openviking.models.vlm.backends.litellm_vlm import LiteLLMVLMProvider

    vlm = LiteLLMVLMProvider(
        {
            "provider": "openai",
            "model": "gpt-4o-mini",
            "api_key": "sk-x",
            "api_base": "https://example.invalid",
            "timeout": 90.0,
        }
    )
    kwargs = vlm._build_text_kwargs(prompt="hi")
    assert kwargs["timeout"] == 90.0
