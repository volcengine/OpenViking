import json
from types import SimpleNamespace
from unittest.mock import MagicMock

import httpx
import pytest
from openai import AsyncOpenAI
from vikingbot.providers.vlm_adapter import VLMProviderAdapter
from volcenginesdkarkruntime import AsyncArk

from openviking.models.model_error import ModelError, ModelErrorKind
from openviking.models.vlm import FailoverVLM, MultiCredentialVLM, OpenAIVLM, VolcEngineVLM


class FakeCompletions:
    def __init__(self, *, chunks=None, error: Exception | None = None):
        self.chunks = chunks or []
        self.error = error
        self.calls: list[dict] = []

    async def create(self, **kwargs):
        self.calls.append(kwargs)
        if self.error is not None:
            raise self.error

        async def generate():
            for chunk in self.chunks:
                if isinstance(chunk, Exception):
                    raise chunk
                yield chunk

        response = MagicMock()
        response.__aenter__.return_value = generate()
        return response


def stream_chunk(
    content: str | None = None,
    *,
    finish_reason: str | None = None,
    usage=None,
):
    choices = []
    if content is not None or finish_reason is not None:
        choices.append(
            SimpleNamespace(
                finish_reason=finish_reason,
                delta=SimpleNamespace(
                    reasoning_content=None,
                    content=content,
                    tool_calls=[],
                ),
            )
        )
    return SimpleNamespace(choices=choices, usage=usage)


def attach_client(vlm, completions: FakeCompletions) -> None:
    vlm.get_async_client = lambda: SimpleNamespace(chat=SimpleNamespace(completions=completions))


def make_adapter(vlm, default_model: str) -> VLMProviderAdapter:
    return VLMProviderAdapter(
        vlm,
        default_model=default_model,
        langfuse_client=SimpleNamespace(enabled=False, _client=None),
    )


@pytest.mark.asyncio
async def test_vlm_adapter_streams_openai_content_deltas():
    usage = SimpleNamespace(
        prompt_tokens=3,
        completion_tokens=2,
        total_tokens=5,
        prompt_tokens_details=None,
        completion_tokens_details=None,
    )
    completions = FakeCompletions(
        chunks=[
            stream_chunk("hel"),
            stream_chunk("lo", finish_reason="stop"),
            stream_chunk(usage=usage),
        ]
    )
    vlm = OpenAIVLM(
        {
            "provider": "openai",
            "model": "gpt-test",
            "api_key": "test-key",
            "temperature": 0.1,
        }
    )
    attach_client(vlm, completions)
    provider = make_adapter(vlm, "gpt-test")

    events = [
        event
        async for event in provider.chat_stream(
            messages=[{"role": "user", "content": "hi"}],
        )
    ]

    assert [event.type for event in events] == [
        "content_delta",
        "content_delta",
        "response",
    ]
    assert [event.content for event in events[:-1]] == ["hel", "lo"]
    assert events[-1].response.content == "hello"
    assert events[-1].response.usage == {
        "prompt_tokens": 3,
        "completion_tokens": 2,
        "total_tokens": 5,
    }
    assert completions.calls[0]["stream"] is True
    assert completions.calls[0]["stream_options"] == {"include_usage": True}
    assert "thinking" not in completions.calls[0]


@pytest.mark.asyncio
async def test_vlm_adapter_streams_with_credential_failover_before_first_delta():
    primary_completions = FakeCompletions(
        error=ModelError(ModelErrorKind.RATE_LIMIT_ERROR, "Rate limit exceeded")
    )
    backup_completions = FakeCompletions(chunks=[stream_chunk("backup", finish_reason="stop")])
    primary = VolcEngineVLM(
        {
            "provider": "volcengine",
            "model": "primary-model",
            "api_key": "primary-key",
            "max_retries": 0,
        }
    )
    backup = VolcEngineVLM(
        {
            "provider": "volcengine",
            "model": "backup-model",
            "api_key": "backup-key",
            "thinking": True,
            "reasoning_effort": "low",
        }
    )
    attach_client(primary, primary_completions)
    attach_client(backup, backup_completions)
    credentials = MultiCredentialVLM(
        [primary, backup],
        credential_ids=["primary", "backup"],
    )
    provider = make_adapter(credentials, "primary-model")

    events = [
        event
        async for event in provider.chat_stream(
            messages=[{"role": "user", "content": "hi"}],
            model="primary-model",
        )
    ]

    assert [event.type for event in events] == ["content_delta", "response"]
    assert events[0].content == "backup"
    assert events[-1].response.content == "backup"
    assert len(primary_completions.calls) == 1
    assert backup_completions.calls[0]["model"] == "backup-model"
    assert backup_completions.calls[0]["reasoning_effort"] == "low"
    assert credentials.active_credential_id == "backup"


@pytest.mark.asyncio
async def test_vlm_adapter_streams_with_legacy_primary_backup_failover():
    primary_completions = FakeCompletions(error=RuntimeError("status 401: unauthorized"))
    backup_completions = FakeCompletions(chunks=[stream_chunk("backup", finish_reason="stop")])
    primary = OpenAIVLM({"provider": "openai", "model": "primary-model", "api_key": "primary-key"})
    backup = OpenAIVLM({"provider": "openai", "model": "backup-model", "api_key": "backup-key"})
    attach_client(primary, primary_completions)
    attach_client(backup, backup_completions)
    failover = FailoverVLM(primary, backup)
    provider = make_adapter(failover, "primary-model")

    events = [
        event
        async for event in provider.chat_stream(
            messages=[{"role": "user", "content": "hi"}],
        )
    ]

    assert [event.type for event in events] == ["content_delta", "response"]
    assert events[-1].response.content == "backup"
    assert failover.is_using_backup is True


@pytest.mark.asyncio
async def test_credential_stream_does_not_replay_after_a_visible_delta():
    primary_completions = FakeCompletions(
        chunks=[
            stream_chunk("partial"),
            ModelError(ModelErrorKind.RATE_LIMIT_ERROR, "Rate limit exceeded"),
        ]
    )
    backup_completions = FakeCompletions(chunks=[stream_chunk("duplicate", finish_reason="stop")])
    primary = OpenAIVLM({"provider": "openai", "model": "primary-model", "api_key": "primary-key"})
    backup = OpenAIVLM({"provider": "openai", "model": "backup-model", "api_key": "backup-key"})
    attach_client(primary, primary_completions)
    attach_client(backup, backup_completions)
    credentials = MultiCredentialVLM(
        [primary, backup],
        credential_ids=["primary", "backup"],
    )
    provider = make_adapter(credentials, "primary-model")

    events = [
        event
        async for event in provider.chat_stream(
            messages=[{"role": "user", "content": "hi"}],
        )
    ]

    assert events[0].type == "content_delta"
    assert events[0].content == "partial"
    assert events[-1].type == "response"
    assert events[-1].response.finish_reason == "error"
    assert len(primary_completions.calls) == 1
    assert backup_completions.calls == []


@pytest.mark.parametrize(
    "backend,client_type", [(OpenAIVLM, AsyncOpenAI), (VolcEngineVLM, AsyncArk)]
)
@pytest.mark.parametrize("temperature", [None, 0.6])
@pytest.mark.asyncio
async def test_bot_stream_temperature_defaults_on_the_wire(backend, client_type, temperature):
    bodies = []

    def send(request):
        bodies.append(json.loads(request.content))
        chunk = {"choices": [{"index": 0, "delta": {"content": "ok"}, "finish_reason": "stop"}]}
        return httpx.Response(
            200,
            headers={"content-type": "text/event-stream"},
            content="data: " + json.dumps(chunk) + "\n\ndata: [DONE]\n\n",
        )

    vlm = backend({"model": "routed-model", "api_key": "test-key", "temperature": temperature})
    async with client_type(
        api_key="test-key",
        base_url="https://example.invalid/v1",
        max_retries=0,
        http_client=httpx.AsyncClient(transport=httpx.MockTransport(send)),
    ) as client:
        vlm.get_async_client = lambda: client
        events = [
            event
            async for event in make_adapter(vlm, "routed-model").chat_stream(
                messages=[{"role": "user", "content": "hello"}]
            )
        ]
    assert events[-1].response.content == "ok"
    assert len(bodies) == 1
    assert bodies[0]["stream"] is True
    if temperature is None:
        assert "temperature" not in bodies[0]
    else:
        assert bodies[0]["temperature"] == temperature
