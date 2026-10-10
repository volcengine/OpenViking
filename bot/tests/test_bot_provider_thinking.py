from types import SimpleNamespace

import pytest
from vikingbot.providers.base import tool_call_to_message_dict
from vikingbot.providers.vlm_adapter import VLMProviderAdapter

from openviking.models.vlm.backends.litellm_vlm import (
    LiteLLMVLMProvider as OpenVikingLiteLLMVLMProvider,
)


def test_vlm_adapter_exposes_only_native_tool_result_media_backends():
    langfuse = SimpleNamespace()

    codex = VLMProviderAdapter(
        SimpleNamespace(provider="openai-codex"),
        default_model="gpt-5.3-codex",
        langfuse_client=langfuse,
    )
    anthropic = VLMProviderAdapter(
        SimpleNamespace(provider="anthropic"),
        default_model="claude-sonnet",
        langfuse_client=langfuse,
    )
    openai = VLMProviderAdapter(
        SimpleNamespace(provider="openai"),
        default_model="gpt-4o",
        langfuse_client=langfuse,
    )

    assert codex.supports_tool_result_media() is True
    assert anthropic.supports_tool_result_media() is True
    assert openai.supports_tool_result_media() is False

    mixed_failover = VLMProviderAdapter(
        SimpleNamespace(
            provider="anthropic",
            primary=SimpleNamespace(provider="anthropic"),
            backup=SimpleNamespace(provider="openai"),
        ),
        default_model="claude-sonnet",
        langfuse_client=langfuse,
    )
    assert mixed_failover.supports_tool_result_media() is False


def test_vlm_adapter_uses_litellm_resolved_provider_for_tool_result_media():
    vlm = OpenVikingLiteLLMVLMProvider(
        {
            "provider": "litellm",
            "model": "claude-sonnet-4-5",
        }
    )
    provider = VLMProviderAdapter(
        vlm,
        default_model="claude-sonnet-4-5",
        langfuse_client=SimpleNamespace(),
    )

    assert vlm.resolved_provider() == "anthropic"
    assert provider.supports_tool_result_media() is True


def test_make_provider_passes_default_thinking_to_vlm_adapter(monkeypatch):
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
@pytest.mark.parametrize("thinking", [True, False])
@pytest.mark.parametrize(
    ("backend", "model", "resolved_model"),
    [
        ("dashscope", "qwen-plus", "dashscope/qwen-plus"),
        ("openai", "gpt-4o", "gpt-4o"),
        ("gemini", "gemini/gemini-2.5-pro", "gemini/gemini-2.5-pro"),
        ("zhipu", "glm-4", "zhipu/glm-4"),
    ],
)
async def test_vlm_adapter_routes_litellm_thinking_parameters(
    monkeypatch, thinking, backend, model, resolved_model
):
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
            "provider": backend,
            "model": model,
            "api_key": "sk-test",
            "thinking": thinking,
        }
    )
    provider = VLMProviderAdapter(vlm, default_model=model)

    response = await provider.chat(messages=[{"role": "user", "content": "hi"}])

    assert response.content == "ok"
    assert captured["model"] == resolved_model
    if backend == "dashscope":
        assert captured["extra_body"] == {"enable_thinking": thinking}
    else:
        assert "extra_body" not in captured
    assert "thinking" not in captured
    assert "reasoning_effort" not in captured


@pytest.mark.asyncio
async def test_vlm_adapter_replays_gemini_tool_call_thought_signature(monkeypatch):
    from litellm.litellm_core_utils.prompt_templates.factory import (
        convert_to_gemini_tool_call_invoke,
    )

    from openviking.models.vlm.backends.litellm_vlm import LiteLLMVLMProvider

    captured = []

    async def fake_acompletion(**kwargs):
        captured.append(kwargs)
        if len(captured) == 1:
            message = SimpleNamespace(
                content=None,
                tool_calls=[
                    SimpleNamespace(
                        id="call-weather",
                        function=SimpleNamespace(name="weather", arguments='{"city":"Singapore"}'),
                        extra_content={"google": {"thought_signature": "signed-provider-state"}},
                        provider_specific_fields={"thought_signature": "native-litellm-state"},
                    )
                ],
            )
            finish_reason = "tool_calls"
        else:
            message = SimpleNamespace(content="done", tool_calls=None)
            finish_reason = "stop"
        return SimpleNamespace(
            choices=[SimpleNamespace(message=message, finish_reason=finish_reason)],
            usage=None,
        )

    monkeypatch.setattr(
        "openviking.models.vlm.backends.litellm_vlm.acompletion",
        fake_acompletion,
    )
    tools = [
        {
            "type": "function",
            "function": {"name": "weather", "parameters": {"type": "object"}},
        }
    ]
    vlm = LiteLLMVLMProvider(
        {
            "provider": "gemini",
            "model": "gemini/gemini-2.5-pro",
            "api_key": "test-key",
            "thinking": True,
        }
    )
    provider = VLMProviderAdapter(vlm, default_model=vlm.model)

    first_response = await provider.chat(
        messages=[{"role": "user", "content": "Check the weather"}], tools=tools
    )
    assistant_message = {
        "role": "assistant",
        "content": " ",
        "tool_calls": [tool_call_to_message_dict(first_response.tool_calls[0])],
    }
    second_response = await provider.chat(
        messages=[
            assistant_message,
            {
                "role": "tool",
                "tool_call_id": "call-weather",
                "name": "weather",
                "content": "sunny",
            },
        ],
        tools=tools,
    )

    replayed = captured[1]["messages"][0]["tool_calls"][0]
    assert replayed["extra_content"] == {"google": {"thought_signature": "signed-provider-state"}}
    assert replayed["provider_specific_fields"] == {"thought_signature": "native-litellm-state"}
    gemini_parts = convert_to_gemini_tool_call_invoke(
        captured[1]["messages"][0], model="gemini-2.5-pro"
    )
    assert gemini_parts[0]["thoughtSignature"] == "native-litellm-state"
    assert second_response.content == "done"


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("configured_max_tokens", "expected_max_tokens"),
    [(None, None), (8192, 8192)],
)
@pytest.mark.parametrize("thinking", [True, False])
async def test_vlm_adapter_volcengine_stream_respects_optional_max_tokens(
    thinking,
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
        thinking=thinking,
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
    assert captured["thinking"] == {"type": "enabled" if thinking else "disabled"}
    if expected_max_tokens is None:
        assert "max_tokens" not in captured
    else:
        assert captured["max_tokens"] == expected_max_tokens
