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


def test_agents_config_defaults_max_tokens_unset():
    assert AgentsConfig().max_tokens is None
    assert AgentsConfig(max_tokens=8192).max_tokens == 8192


def test_agents_openviking_retention_defaults_to_turn_budget_values():
    config = AgentsConfig()

    assert config.commit_keep_recent_count == 10
    assert AgentsConfig(commit_keep_recent_count=7).commit_keep_recent_count == 7
    assert config.commit_keep_recent_turn_count == 3
    assert config.commit_retained_message_token_budget == 6_000
    assert config.commit_min_raw_tail_steps == 1


def test_make_provider_passes_default_thinking_to_vlm_adapter(monkeypatch):
    monkeypatch.setitem(
        sys.modules,
        "prompt_toolkit",
        SimpleNamespace(PromptSession=object),
    )
    monkeypatch.setitem(
        sys.modules,
        "prompt_toolkit.formatted_text",
        SimpleNamespace(HTML=lambda value: value, FormattedText=lambda value: value),
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
    monkeypatch.setitem(
        sys.modules,
        "prompt_toolkit.styles",
        SimpleNamespace(
            Style=SimpleNamespace(from_dict=lambda value: value),
        ),
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
    assert "max_tokens" not in captured


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("configured_max_tokens", "expected_max_tokens"),
    [(None, None), (8192, 8192)],
)
async def test_vlm_adapter_volcengine_stream_respects_optional_max_tokens(
    configured_max_tokens,
    expected_max_tokens,
):
    captured = {}

    class FakeCompletions:
        async def create(self, **kwargs):
            captured.update(kwargs)

            async def chunks():
                yield SimpleNamespace(
                    choices=[
                        SimpleNamespace(
                            finish_reason="stop",
                            delta=SimpleNamespace(
                                reasoning_content=None,
                                content="ok",
                                tool_calls=[],
                            ),
                        )
                    ],
                    usage=None,
                )

            return chunks()

    vlm = SimpleNamespace(
        provider="volcengine",
        model="ep-test",
        temperature=0.0,
        max_tokens=configured_max_tokens,
        thinking=False,
        extra_headers=None,
        get_async_client=lambda: SimpleNamespace(
            chat=SimpleNamespace(completions=FakeCompletions())
        ),
    )
    provider = VLMProviderAdapter(
        vlm,
        default_model="ep-test",
        langfuse_client=SimpleNamespace(enabled=False, _client=None),
    )

    events = [
        event
        async for event in provider.chat_stream(
            messages=[{"role": "user", "content": "hi"}],
        )
    ]

    assert events[-1].response.content == "ok"
    if expected_max_tokens is None:
        assert "max_tokens" not in captured
    else:
        assert captured["max_tokens"] == expected_max_tokens


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
