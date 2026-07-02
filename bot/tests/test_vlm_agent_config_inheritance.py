# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0
"""Tests for inheriting top-level VLM settings into bot.agents."""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from vikingbot.config.loader import _merge_vlm_model_config  # noqa: E402
from vikingbot.config.schema import Config  # noqa: E402


def test_bot_agents_inherits_vlm_provider_settings_when_unset():
    bot_data = {}
    vlm_data = {
        "model": "qwen-plus",
        "provider": "dashscope",
        "api_key": "sk-test",
        "forward_api_key": False,
        "api_base": "https://dashscope.example/v1",
        "temperature": 0.1,
        "thinking": False,
        "timeout": 77.0,
        "max_tokens": 8192,
        "max_retries": 5,
        "extra_headers": {"X-Test": "1"},
        "extra_request_body": {"seed": 7},
        "api_version": "2026-01-01",
        "stream": True,
    }

    _merge_vlm_model_config(bot_data, vlm_data)
    config = Config.model_validate(bot_data)

    assert config.agents.model == "qwen-plus"
    assert config.agents.provider == "dashscope"
    assert config.agents.api_key == "sk-test"
    assert config.agents.forward_api_key is False
    assert config.agents.api_base == "https://dashscope.example/v1"
    assert config.agents.temperature == 0.1
    assert config.agents.thinking is False
    assert config.agents.timeout == 77.0
    assert config.agents.max_tokens == 8192
    assert config.agents.max_retries == 5
    assert config.agents.extra_headers == {"X-Test": "1"}
    assert config.agents.extra_request_body == {"seed": 7}
    assert config.agents.api_version == "2026-01-01"
    assert config.agents.stream is True


def test_bot_agents_explicit_settings_override_vlm_settings():
    bot_data = {
        "agents": {
            "model": "bot-model",
            "temperature": 0.4,
            "max_tokens": 2048,
            "extra_request_body": {"bot": True},
        }
    }
    vlm_data = {
        "model": "vlm-model",
        "temperature": 0.1,
        "max_tokens": 8192,
        "extra_request_body": {"vlm": True},
        "provider": "dashscope",
    }

    _merge_vlm_model_config(bot_data, vlm_data)
    config = Config.model_validate(bot_data)

    assert config.agents.model == "bot-model"
    assert config.agents.temperature == 0.4
    assert config.agents.max_tokens == 2048
    assert config.agents.extra_request_body == {"bot": True}
    assert config.agents.provider == "dashscope"
