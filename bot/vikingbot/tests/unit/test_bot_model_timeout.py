from types import SimpleNamespace
from unittest import mock

import pytest
from pydantic import ValidationError

from vikingbot.config.loader import _merge_vlm_model_config
from vikingbot.config.schema import AgentsConfig
from vikingbot.providers.litellm_provider import LiteLLMProvider


def _chat_response(content: str = "ok"):
    return SimpleNamespace(
        choices=[
            SimpleNamespace(
                message=SimpleNamespace(content=content, tool_calls=None),
                finish_reason="stop",
            )
        ],
        usage=SimpleNamespace(prompt_tokens=1, completion_tokens=1, total_tokens=2),
    )


def test_agents_config_accepts_timeout():
    config = AgentsConfig(timeout=120.0)

    assert config.timeout == 120.0


def test_agents_config_rejects_non_positive_timeout():
    with pytest.raises(ValidationError):
        AgentsConfig(timeout=0)


def test_bot_agents_timeout_inherits_vlm_timeout_when_omitted():
    bot_data = {"agents": {"model": "bot-model"}}

    _merge_vlm_model_config(bot_data, {"model": "vlm-model", "timeout": 180.0})

    assert bot_data["agents"]["model"] == "bot-model"
    assert bot_data["agents"]["timeout"] == 180.0


def test_bot_agents_timeout_inherits_vlm_default_timeout_when_omitted():
    bot_data = {"agents": {"model": "bot-model"}}

    _merge_vlm_model_config(bot_data, {"model": "vlm-model"})

    assert bot_data["agents"]["timeout"] == 60.0


def test_bot_agents_timeout_overrides_vlm_timeout():
    bot_data = {"agents": {"model": "bot-model", "timeout": 45.0}}

    _merge_vlm_model_config(bot_data, {"model": "vlm-model", "timeout": 180.0})

    assert bot_data["agents"]["timeout"] == 45.0


@pytest.mark.asyncio
async def test_litellm_provider_passes_timeout_to_chat_request(monkeypatch):
    captured = {}

    async def fake_acompletion(**kwargs):
        captured.update(kwargs)
        return _chat_response()

    monkeypatch.setattr("vikingbot.providers.litellm_provider.acompletion", fake_acompletion)

    provider = LiteLLMProvider(
        api_key="sk-test",
        api_base="https://example.invalid",
        default_model="openai/gpt-4o-mini",
        timeout=75.0,
    )
    response = await provider.chat(messages=[{"role": "user", "content": "hi"}])

    assert response.content == "ok"
    assert captured["timeout"] == 75.0


def test_volcengine_vlm_passes_timeout_to_sync_client():
    from openviking.models.vlm.backends.volcengine_vlm import VolcEngineVLM

    vlm = VolcEngineVLM(
        {
            "provider": "volcengine",
            "model": "doubao-test",
            "api_key": "ak-test",
            "api_base": "https://example.invalid/api/v3",
            "timeout": 88.0,
        }
    )

    with mock.patch("volcenginesdkarkruntime.Ark") as fake:
        vlm.get_client()

    assert fake.call_args.kwargs["timeout"] == 88.0
