# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0
from openviking_cli.utils.config.vlm_config import VLMConfig


def test_default_provider_selects_configured_provider_with_api_key():
    config = VLMConfig(
        model="gpt-4o-mini",
        default_provider="openai",
        providers={
            "litellm": {"api_key": "sk-litellm"},
            "openai": {"api_key": "sk-openai"},
        },
    )

    provider_config, provider_name = config.get_provider_config()

    assert provider_name == "openai"
    assert provider_config["api_key"] == "sk-openai"


def test_explicit_provider_takes_precedence_over_default_provider():
    config = VLMConfig(
        model="gpt-4o-mini",
        provider="litellm",
        default_provider="openai",
        providers={
            "litellm": {"api_key": "sk-litellm"},
            "openai": {"api_key": "sk-openai"},
        },
    )

    provider_config, provider_name = config.get_provider_config()

    assert provider_name == "litellm"
    assert provider_config["api_key"] == "sk-litellm"


def test_default_provider_without_credentials_falls_back_to_usable_provider():
    config = VLMConfig(
        model="gpt-4o-mini",
        default_provider="openai",
        providers={
            "openai": {},
            "litellm": {"api_key": "sk-litellm"},
        },
    )

    provider_config, provider_name = config.get_provider_config()
    result = config._build_vlm_config_dict()

    assert provider_name == "litellm"
    assert provider_config["api_key"] == "sk-litellm"
    assert result["provider"] == "litellm"
    assert result["api_key"] == "sk-litellm"


def test_unknown_default_provider_falls_back_to_usable_provider():
    config = VLMConfig(
        model="gpt-4o-mini",
        default_provider="missing-provider",
        providers={"openai": {"api_key": "sk-openai"}},
    )

    provider_config, provider_name = config.get_provider_config()

    assert provider_name == "openai"
    assert provider_config["api_key"] == "sk-openai"
