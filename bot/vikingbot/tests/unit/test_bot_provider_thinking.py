from types import SimpleNamespace

import pytest

from vikingbot.config.schema import AgentsConfig
from vikingbot.providers.litellm_provider import LiteLLMProvider


def test_agents_config_defaults_thinking_enabled():
    assert AgentsConfig().thinking is True
    assert AgentsConfig(thinking=False).thinking is False


@pytest.mark.asyncio
async def test_litellm_bot_provider_enables_volcengine_thinking(monkeypatch):
    captured = {}

    async def fake_acompletion(**kwargs):
        captured.update(kwargs)
        return SimpleNamespace(
            choices=[
                SimpleNamespace(
                    message=SimpleNamespace(content="ok", tool_calls=None),
                    finish_reason="stop",
                )
            ],
            usage=None,
        )

    monkeypatch.setattr("vikingbot.providers.litellm_provider.acompletion", fake_acompletion)

    provider = LiteLLMProvider(api_key="ak-test", default_model="volcengine/ep-test")
    await provider.chat(messages=[{"role": "user", "content": "hi"}])

    assert captured["thinking"] == {"type": "enabled"}


@pytest.mark.asyncio
async def test_litellm_bot_provider_enables_dashscope_thinking(monkeypatch):
    captured = {}

    async def fake_acompletion(**kwargs):
        captured.update(kwargs)
        return SimpleNamespace(
            choices=[
                SimpleNamespace(
                    message=SimpleNamespace(content="ok", tool_calls=None),
                    finish_reason="stop",
                )
            ],
            usage=None,
        )

    monkeypatch.setattr("vikingbot.providers.litellm_provider.acompletion", fake_acompletion)

    provider = LiteLLMProvider(api_key="sk-test", default_model="qwen-plus")
    await provider.chat(messages=[{"role": "user", "content": "hi"}])

    assert captured["extra_body"] == {"enable_thinking": True}


@pytest.mark.asyncio
async def test_litellm_bot_provider_does_not_send_thinking_to_generic_openai(monkeypatch):
    captured = {}

    async def fake_acompletion(**kwargs):
        captured.update(kwargs)
        return SimpleNamespace(
            choices=[
                SimpleNamespace(
                    message=SimpleNamespace(content="ok", tool_calls=None),
                    finish_reason="stop",
                )
            ],
            usage=None,
        )

    monkeypatch.setattr("vikingbot.providers.litellm_provider.acompletion", fake_acompletion)

    provider = LiteLLMProvider(api_key="sk-test", default_model="gpt-4o")
    await provider.chat(messages=[{"role": "user", "content": "hi"}])

    assert "thinking" not in captured
    assert "extra_body" not in captured


@pytest.mark.asyncio
async def test_litellm_bot_provider_enables_openai_reasoning_model(monkeypatch):
    captured = {}

    async def fake_acompletion(**kwargs):
        captured.update(kwargs)
        return SimpleNamespace(
            choices=[
                SimpleNamespace(
                    message=SimpleNamespace(content="ok", tool_calls=None),
                    finish_reason="stop",
                )
            ],
            usage=None,
        )

    monkeypatch.setattr("vikingbot.providers.litellm_provider.acompletion", fake_acompletion)

    provider = LiteLLMProvider(api_key="sk-test", default_model="gpt-5")
    await provider.chat(messages=[{"role": "user", "content": "hi"}])

    assert captured["reasoning_effort"] == "low"


@pytest.mark.asyncio
async def test_litellm_bot_provider_respects_thinking_disabled(monkeypatch):
    captured = {}

    async def fake_acompletion(**kwargs):
        captured.update(kwargs)
        return SimpleNamespace(
            choices=[
                SimpleNamespace(
                    message=SimpleNamespace(content="ok", tool_calls=None),
                    finish_reason="stop",
                )
            ],
            usage=None,
        )

    monkeypatch.setattr("vikingbot.providers.litellm_provider.acompletion", fake_acompletion)

    provider = LiteLLMProvider(
        api_key="ak-test",
        default_model="volcengine/ep-test",
        thinking=False,
    )
    await provider.chat(messages=[{"role": "user", "content": "hi"}])

    assert "thinking" not in captured
