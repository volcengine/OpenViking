import json
from copy import deepcopy
from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock, MagicMock

import litellm
import pytest
from litellm.types.utils import LlmProviders
from litellm.utils import ProviderConfigManager
from vikingbot.integrations.langfuse import LangfuseClient
from vikingbot.providers.vlm_adapter import VLMProviderAdapter

from openviking.models.model_error import ModelError, ModelErrorKind
from openviking.models.vlm import FailoverVLM, MultiCredentialVLM, VLMFactory, VolcEngineVLM
from openviking.models.vlm.backends.litellm_vlm import (
    LiteLLMVLMProvider as OpenVikingLiteLLMVLMProvider,
)
from openviking.models.vlm.base import VLMResponse


@pytest.mark.asyncio
async def test_unknown_media_transport_and_backup_do_not_block_images(monkeypatch):
    completion = AsyncMock(return_value=VLMResponse(content="done"))
    unknown = SimpleNamespace(provider="openai", get_completion_async=completion)
    adapter = VLMProviderAdapter(unknown, "vision", langfuse_client=LangfuseClient())
    image = {"type": "image_url", "image_url": {"url": "data:image/png;base64,AA=="}}
    messages = [{"role": "tool", "tool_call_id": "read", "content": [image]}]
    assert (await adapter.chat(messages)).content == "done"
    assert completion.await_args.kwargs["messages"][-1]["content"][-1] == image

    primary = VLMFactory.create({"provider": "openai", "model": "gpt-4o"})
    monkeypatch.setattr(primary, "get_completion_async", completion)
    backup = OpenVikingLiteLLMVLMProvider({"provider": "litellm", "model": "sagemaker/custom"})
    for chain in (
        FailoverVLM(primary, backup),
        MultiCredentialVLM([primary, backup], credential_ids=["primary", "backup"]),
    ):
        adapter = VLMProviderAdapter(chain, "vision", langfuse_client=LangfuseClient())
        assert adapter.supports_tool_result_media()
        assert (await adapter.chat(messages)).content == "done"
        assert completion.await_args.kwargs["messages"][-1]["content"][-1] == image


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "model,mode",
    [
        ("openai/future-claude-vision", "user_message"),
        ("azure/deployment", "user_message"),
        ("dashscope/qwen3-vl-plus", "user_message"),
        ("moonshot/kimi-k2.5", "user_message"),
        ("zai/glm-4.6v", "user_message"),
        ("openrouter/qwen/qwen3-vl-32b-instruct", "user_message"),
        ("databricks/databricks-meta-llama-3-3-70b-instruct", "user_message"),
        ("hosted_vllm/private-vision-model", "user_message"),
        ("nvidia_nim/nvidia/llama-3.2-nv-vision-90b-v1", "user_message"),
        ("gemini/gemini-2.5-flash", "user_message"),
        ("vertex_ai/gemini-2.5-flash", "user_message"),
        ("anthropic/claude-sonnet-4-5", "native"),
        ("vertex_ai/claude-sonnet-4-5", "native"),
        ("azure_ai/claude-sonnet-4-5", "native"),
        ("bedrock/anthropic.claude-3-5-sonnet-20241022-v2:0", "user_message"),
        ("ollama/llava", "user_message"),
        ("ollama_chat/llama3.2-vision", "user_message"),
        ("github_copilot/gpt-4o", "user_message"),
        ("minimax/MiniMax-M3", "user_message"),
        ("mistral/pixtral-large-latest", "user_message"),
        ("cohere/command-a-vision-07-2025", "user_message"),
        ("groq/meta-llama/llama-4-scout-17b-16e-instruct", "user_message"),
    ],
)
async def test_litellm_declared_media_survives_actual_conversion(
    monkeypatch, tmp_path, model, mode
):
    def no_auth(*args, **kwargs):
        raise AssertionError("Capability checks must not resolve credentials or log in")

    monkeypatch.setattr("litellm.get_llm_provider", no_auth)
    monkeypatch.setattr(
        "litellm.llms.github_copilot.authenticator.Authenticator.get_api_key", no_auth
    )
    monkeypatch.setenv("GITHUB_COPILOT_TOKEN_DIR", str(tmp_path))
    vlm = OpenVikingLiteLLMVLMProvider({"provider": "litellm", "model": model})
    assert vlm.tool_result_media_mode() == mode

    encoded = "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAwMCAO+a5XsAAAAASUVORK5CYII="
    image = {"type": "image_url", "image_url": {"url": f"data:image/png;base64,{encoded}"}}
    tools = [
        {
            "type": "function",
            "function": {"name": "read_image", "parameters": {"type": "object", "properties": {}}},
        }
    ]
    messages: list[dict[str, Any]] = [
        {"role": "user", "content": "Read the source image."},
        {
            "role": "assistant",
            "content": None,
            "tool_calls": [
                {
                    "id": "read-1",
                    "type": "function",
                    "function": {"name": "read_image", "arguments": "{}"},
                }
            ],
        },
        {
            "role": "tool",
            "tool_call_id": "read-1",
            "name": "read_image",
            "content": [{"type": "text", "text": "viking://resources/image.png"}, image],
        },
    ]
    original = deepcopy(messages)
    captured = []

    async def convert(**kwargs):
        provider, raw_model = kwargs["model"].split("/", 1)
        config = ProviderConfigManager.get_provider_chat_config(
            model=raw_model, provider=LlmProviders(provider)
        )
        assert config is not None
        wire: Any
        if isinstance(config, litellm.VertexGeminiConfig):
            wire = config._transform_messages(messages=kwargs["messages"], model=raw_model)
        else:
            wire = config.transform_request(
                model=raw_model,
                messages=kwargs["messages"],
                optional_params={"tools": deepcopy(tools), "max_tokens": 512},
                litellm_params={},
                headers={},
            )
        captured.append(wire)
        return "image delivered"

    monkeypatch.setattr("openviking.models.vlm.backends.litellm_vlm.acompletion", convert)
    adapter = VLMProviderAdapter(vlm, model, langfuse_client=LangfuseClient())
    assert (await adapter.chat(messages, tools=tools)).content == "image delivered"
    serialized = json.dumps(captured[0])
    assert serialized.count(encoded) == 1 and "viking://resources/image.png" in serialized
    assert messages == original


@pytest.mark.parametrize("model", ["openai/future-model", "custom/model", "unknown-model"])
def test_unknown_litellm_transport_uses_user_images(monkeypatch, model):
    class UnverifiedConfig(litellm.OpenAIGPTConfig):
        pass

    monkeypatch.setattr(
        ProviderConfigManager, "get_provider_chat_config", lambda **kwargs: UnverifiedConfig()
    )
    vlm = OpenVikingLiteLLMVLMProvider({"provider": "litellm", "model": model})
    assert vlm.tool_result_media_mode() == "user_message"


@pytest.mark.asyncio
@pytest.mark.parametrize("mixed", [False, True])
@pytest.mark.parametrize("wrapper", ["credentials", "legacy"])
async def test_vlm_adapter_preserves_native_media_and_adapts_mixed_credential_chains(
    monkeypatch, mixed, wrapper
):
    native = VLMFactory.create({"provider": "openai-codex", "model": "gpt-5.3-codex"})
    backup = (
        VolcEngineVLM({"provider": "volcengine", "model": "doubao-seed-2.1-lite"})
        if mixed
        else VLMFactory.create({"provider": "anthropic", "model": "anthropic/claude-sonnet-4-5"})
    )
    native_completion = AsyncMock(
        side_effect=ModelError(ModelErrorKind.AUTHENTICATION_ERROR, "expired", status_code=401)
        if mixed
        else None,
        return_value=VLMResponse(content="done"),
    )
    backup_completion = AsyncMock(return_value=VLMResponse(content="done"))
    monkeypatch.setattr(native, "get_completion_async", native_completion)
    monkeypatch.setattr(backup, "get_completion_async", backup_completion)
    vlm = (
        MultiCredentialVLM([native, backup], credential_ids=["primary", "backup"])
        if wrapper == "credentials"
        else FailoverVLM(native, backup)
    )
    provider = VLMProviderAdapter(vlm, "vision", langfuse_client=LangfuseClient())
    image = {"type": "image_url", "image_url": {"url": "data:image/png;base64,AA=="}}
    messages = [{"role": "tool", "tool_call_id": "read", "content": [image]}]

    assert provider.supports_tool_result_media()
    assert (await provider.chat(messages)).content == "done"
    sent = native_completion.await_args_list[-1].kwargs["messages"]
    if mixed:
        assert backup_completion.await_args_list[-1].kwargs["messages"] == sent
        assert [m["role"] for m in sent] == ["tool", "user"]
        assert sent[1]["content"][-1] == image
    else:
        assert sent == messages


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

            response = MagicMock()
            response.__aenter__.return_value = chunks()
            return response

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
