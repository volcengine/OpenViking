import contextlib
import sys
from types import SimpleNamespace

import pytest

from vikingbot.config.schema import AgentsConfig
from vikingbot.providers.litellm_provider import LiteLLMProvider
from vikingbot.providers.vlm_adapter import VLMProviderAdapter


def test_agents_config_defaults_thinking_enabled():
    assert AgentsConfig().thinking is True
    assert AgentsConfig(thinking=False).thinking is False


def test_make_provider_passes_default_thinking_to_vlm_adapter(monkeypatch):
    monkeypatch.setitem(
        sys.modules,
        "prompt_toolkit",
        SimpleNamespace(PromptSession=object),
    )
    monkeypatch.setitem(
        sys.modules,
        "prompt_toolkit.formatted_text",
        SimpleNamespace(HTML=lambda value: value),
    )
    monkeypatch.setitem(
        sys.modules,
        "prompt_toolkit.history",
        SimpleNamespace(FileHistory=object),
    )
    monkeypatch.setitem(
        sys.modules,
        "prompt_toolkit.patch_stdout",
        SimpleNamespace(patch_stdout=lambda: contextlib.nullcontext()),
    )

    from vikingbot.cli.commands import _make_provider

    captured = {}

    def fake_create(config):
        captured.update(config)
        return SimpleNamespace(
            provider=config["provider"],
            model=config["model"],
            thinking=config["thinking"],
        )

    monkeypatch.setattr("openviking.models.vlm.base.VLMFactory.create", fake_create)

    provider = _make_provider(
        SimpleNamespace(
            agents=SimpleNamespace(
                model="ep-test",
                temperature=0.0,
                thinking=True,
                api_key="ak-test",
                api_base="https://example.invalid",
                provider="volcengine",
                extra_headers={},
                timeout=None,
            )
        )
    )

    assert captured["thinking"] is True
    assert provider._vlm.thinking is True


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

    provider = LiteLLMProvider(
        api_key="ak-test",
        default_model="volcengine/ep-test",
    )
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

    provider = LiteLLMProvider(
        api_key="sk-test",
        default_model="qwen-plus",
    )
    await provider.chat(messages=[{"role": "user", "content": "hi"}])

    assert captured["extra_body"] == {"enable_thinking": True}


@pytest.mark.asyncio
async def test_vlm_adapter_preserves_dashscope_thinking(monkeypatch):
    from openviking.models.vlm.backends.litellm_vlm import LiteLLMVLMProvider

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

    monkeypatch.setattr(
        "openviking.models.vlm.backends.litellm_vlm.acompletion",
        fake_acompletion,
    )

    vlm = LiteLLMVLMProvider(
        {
            "provider": "dashscope",
            "model": "qwen-plus",
            "api_key": "sk-test",
            "thinking": True,
        }
    )
    provider = VLMProviderAdapter(vlm, default_model="qwen-plus")

    response = await provider.chat(messages=[{"role": "user", "content": "hi"}])

    assert response.content == "ok"
    assert captured["model"] == "dashscope/qwen-plus"
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

    provider = LiteLLMProvider(
        api_key="sk-test",
        default_model="gpt-4o",
    )
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

    provider = LiteLLMProvider(
        api_key="sk-test",
        default_model="gpt-5",
    )
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


@pytest.mark.asyncio
async def test_litellm_bot_provider_does_not_send_dashscope_param_to_gemini(monkeypatch):
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
        api_key="sk-test",
        default_model="gemini/gemini-2.5-pro",
    )
    await provider.chat(messages=[{"role": "user", "content": "hi"}])

    assert "thinking" not in captured
    assert "extra_body" not in captured
    assert "reasoning_effort" not in captured


@pytest.mark.asyncio
async def test_litellm_bot_provider_does_not_send_dashscope_param_to_zhipu(monkeypatch):
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
        api_key="sk-test",
        default_model="glm-4",
    )
    await provider.chat(messages=[{"role": "user", "content": "hi"}])

    assert "thinking" not in captured
    assert "extra_body" not in captured
    assert "reasoning_effort" not in captured
