import json
from collections import Counter
from types import SimpleNamespace
from unittest.mock import AsyncMock

import httpx
import pytest
import vikingbot.providers.vlm_adapter as vlm_adapter
from vikingbot.compile.models import CompileLimits
from vikingbot.compile.pipeline_io import JsonModel, ModelCallError
from vikingbot.providers.vlm_adapter import VLMProviderAdapter
from volcenginesdkarkruntime._exceptions import (
    ArkAPITimeoutError,
    ArkBadRequestError,
)
from volcenginesdkarkruntime._streaming import AsyncStream

from openviking.models.model_error import ModelError, ModelErrorKind, model_error_kind
from openviking.models.vlm.backends.openai_vlm import OpenAIVLM
from openviking.models.vlm.backends.volcengine_vlm import (
    VOLCENGINE_CLIENT_REQUEST_ID,
    VOLCENGINE_CLIENT_REQUEST_ID_HEADER,
    VolcEngineVLM,
    build_volcengine_request_headers,
)
from openviking.models.vlm.retry_vlm import RetryVLM


class _DisabledLangfuse:
    enabled = False
    _client = None


class _FakeVLM:
    def __init__(self, failures: list[Exception], result: str = "ok"):
        self.failures = list(failures)
        self.result = result
        self.calls = 0

    async def get_completion_async(self, **_kwargs):
        self.calls += 1
        if self.failures:
            raise self.failures.pop(0)
        return self.result


class _FakeVolcEngineFailoverVLM(_FakeVLM):
    provider = "volcengine"
    model = "primary-model"
    thinking = False


class _FakeStreamingCompletions:
    def __init__(self, failures: list[Exception], chunks):
        self.failures = list(failures)
        self.chunks = chunks
        self.calls = 0
        self.kwargs: list[dict] = []

    async def create(self, **kwargs):
        self.calls += 1
        self.kwargs.append(kwargs)
        if self.failures:
            raise self.failures.pop(0)
        response = AsyncMock()
        response.__aenter__.return_value.__aiter__.return_value = self.chunks
        return response


class _FakeStreamingVLM:
    provider = "volcengine"
    model = "test-model"
    thinking = False

    def __init__(self, completions: _FakeStreamingCompletions, max_retries: int = 2):
        self.max_retries = max_retries
        self._client = SimpleNamespace(
            chat=SimpleNamespace(completions=completions),
        )

    def get_async_client(self):
        return self._client


class _CaptureLogger:
    def __init__(self):
        self.messages: list[str] = []

    def error(self, message: str, *args):
        self.messages.append(message.format(*args))


@pytest.mark.asyncio
@pytest.mark.parametrize("max_retries,expected_calls", [(0, 1), (2, 3)])
async def test_chat_preserves_model_retry_budget(monkeypatch, max_retries, expected_calls):
    monkeypatch.setattr("openviking.utils.model_retry.asyncio.sleep", AsyncMock())
    backend = VolcEngineVLM(
        {"provider": "volcengine", "model": "test-model", "max_retries": max_retries}
    )
    error = ModelError(ModelErrorKind.RATE_LIMIT_ERROR, "rate limited", status_code=429)
    completion = AsyncMock(side_effect=error)
    monkeypatch.setattr(backend, "get_completion_async", completion)
    adapter = VLMProviderAdapter(
        RetryVLM(backend), "test-model", langfuse_client=_DisabledLangfuse()
    )

    response = await adapter.chat(messages=[{"role": "user", "content": "hello"}])

    assert response.finish_reason == "error"
    assert response.error_kind is ModelErrorKind.RATE_LIMIT_ERROR
    assert completion.await_count == expected_calls


@pytest.mark.asyncio
async def test_chat_preserves_input_error_for_compile_without_retry():
    fake_vlm = _FakeVLM([ModelError(ModelErrorKind.INPUT_TOO_LARGE, "Input rejected")])
    adapter = VLMProviderAdapter(fake_vlm, "test-model", langfuse_client=_DisabledLangfuse())
    files = SimpleNamespace(put=AsyncMock())
    model = JsonModel(adapter, "test-model", 0.0, files, CompileLimits(), {}, Counter())

    with pytest.raises(ModelCallError) as raised:
        await model.call("reduce", [{"role": "user", "content": "hello"}], [], max_tokens=128)

    assert model_error_kind(raised.value) is ModelErrorKind.INPUT_TOO_LARGE
    assert fake_vlm.calls == 1


@pytest.mark.asyncio
async def test_chat_accepts_string_response_from_openai_backend_with_tools(monkeypatch):
    async def create(**_kwargs):
        return "plain string response"

    client = SimpleNamespace(
        chat=SimpleNamespace(completions=SimpleNamespace(create=create)),
    )
    vlm = OpenAIVLM({"provider": "openai", "model": "gpt-5.6-terra"})
    monkeypatch.setattr(vlm, "get_async_client", lambda: client)
    adapter = VLMProviderAdapter(vlm, "gpt-5.6-terra", langfuse_client=_DisabledLangfuse())

    response = await adapter.chat(
        messages=[{"role": "user", "content": "hello"}],
        tools=[
            {
                "type": "function",
                "function": {
                    "name": "test_tool",
                    "description": "A test tool",
                    "parameters": {"type": "object", "properties": {}},
                },
            }
        ],
    )

    assert response.content == "plain string response"
    assert response.tool_calls == []
    assert response.finish_reason == "stop"


@pytest.mark.asyncio
async def test_chat_without_tools_preserves_usage_from_openai_backend(monkeypatch):
    raw_response = SimpleNamespace(
        choices=[
            SimpleNamespace(
                message=SimpleNamespace(content="plain response", tool_calls=None),
                finish_reason="stop",
            )
        ],
        usage=SimpleNamespace(
            prompt_tokens=13,
            completion_tokens=5,
            total_tokens=18,
            prompt_tokens_details=None,
            completion_tokens_details=None,
        ),
    )

    async def create(**kwargs):
        assert "tools" not in kwargs
        return raw_response

    client = SimpleNamespace(
        chat=SimpleNamespace(completions=SimpleNamespace(create=create)),
    )
    vlm = OpenAIVLM({"provider": "openai", "model": "gpt-5.6-terra"})
    monkeypatch.setattr(vlm, "get_async_client", lambda: client)
    adapter = VLMProviderAdapter(vlm, "gpt-5.6-terra", langfuse_client=_DisabledLangfuse())

    response = await adapter.chat(messages=[{"role": "user", "content": "hello"}])

    assert response.content == "plain response"
    assert response.tool_calls == []
    assert response.finish_reason == "stop"
    assert response.usage == {
        "prompt_tokens": 13,
        "completion_tokens": 5,
        "total_tokens": 18,
    }


@pytest.mark.asyncio
async def test_chat_stream_retries_rate_limit_until_success(monkeypatch):
    sleep = AsyncMock()
    monkeypatch.setattr("openviking.utils.model_retry.asyncio.sleep", sleep)
    chunk = SimpleNamespace(
        usage=None,
        choices=[
            SimpleNamespace(
                finish_reason=None,
                delta=SimpleNamespace(content="streamed", reasoning_content=None),
            )
        ],
    )
    completions = _FakeStreamingCompletions(
        [ModelError(ModelErrorKind.RATE_LIMIT_ERROR, "temporarily unavailable")],
        [chunk],
    )
    adapter = VLMProviderAdapter(
        _FakeStreamingVLM(completions),
        "test-model",
        langfuse_client=_DisabledLangfuse(),
    )

    events = [
        event
        async for event in adapter.chat_stream(
            messages=[{"role": "user", "content": "hello"}],
        )
    ]

    assert completions.calls == 2
    sleep.assert_awaited_once()
    assert [event.type for event in events] == ["content_delta", "response"]
    assert events[0].content == "streamed"
    assert events[1].response.content == "streamed"
    assert events[1].response.finish_reason == "stop"


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "code,expected_calls,kind",
    [
        ("ModelAccountTpmRateLimitExceeded", 3, ModelErrorKind.RATE_LIMIT_ERROR),
        ("AccountQuotaExceeded", 1, ModelErrorKind.QUOTA_EXHAUSTED),
    ],
)
async def test_chat_stream_obeys_retry_policy(monkeypatch, code, expected_calls, kind):
    monkeypatch.setattr("openviking.utils.model_retry.asyncio.sleep", AsyncMock())
    vlm = VolcEngineVLM({"model": "test-model", "api_key": "test-key", "max_retries": 2})
    payload = (
        "data: " + json.dumps({"error": {"code": code, "message": "limited"}}) + "\n\n"
    ).encode()
    responses = [
        httpx.Response(
            200,
            stream=httpx.ByteStream(payload),
            request=httpx.Request("POST", "https://example.test"),
        )
        for _ in range(expected_calls)
    ]
    async with vlm.get_async_client() as client:
        create = AsyncMock(
            side_effect=[AsyncStream(cast_to=dict, response=r, client=client) for r in responses]
        )
        monkeypatch.setattr(client.chat.completions, "create", create)
        adapter = VLMProviderAdapter(vlm, "test-model", langfuse_client=_DisabledLangfuse())
        events = [event async for event in adapter.chat_stream(messages=[])]
    assert create.await_count == expected_calls
    assert all(response.is_closed for response in responses)
    assert events[-1].response.finish_reason == "error"
    assert events[-1].response.error_kind is kind


@pytest.mark.asyncio
async def test_chat_stream_routes_failover_wrapper_through_completion_api():
    fake_vlm = _FakeVolcEngineFailoverVLM([], result="fallback-safe")
    adapter = VLMProviderAdapter(
        fake_vlm,
        "primary-model",
        langfuse_client=_DisabledLangfuse(),
    )

    events = [
        event
        async for event in adapter.chat_stream(
            messages=[{"role": "user", "content": "hello"}],
        )
    ]

    assert fake_vlm.calls == 1
    assert [event.type for event in events] == ["response"]
    assert events[0].response.content == "fallback-safe"


@pytest.mark.asyncio
async def test_chat_stream_logs_timeout_cause_and_request_metadata(monkeypatch):
    request = httpx.Request(
        "POST",
        "https://ark.example.test/api/v3/chat/completions?api_key=must-not-leak",
    )
    cause = httpx.ReadTimeout("read deadline exceeded", request=request)
    error = ArkAPITimeoutError(request=request, request_id="request-123")
    error.__cause__ = cause
    completions = _FakeStreamingCompletions([error], [])
    adapter = VLMProviderAdapter(
        _FakeStreamingVLM(completions, max_retries=0),
        "test-model",
        langfuse_client=_DisabledLangfuse(),
    )
    captured = _CaptureLogger()
    monkeypatch.setattr(vlm_adapter, "logger", captured)

    events = [
        event
        async for event in adapter.chat_stream(
            messages=[{"role": "user", "content": "hello"}],
        )
    ]

    assert events[-1].response.finish_reason == "error"
    assert len(captured.messages) == 1
    log_message = captured.messages[0]
    assert "operation=chat_stream" in log_message
    assert "error_type=ArkAPITimeoutError" in log_message
    assert 'client_request_id="request-123"' in log_message
    assert "server_request_id=-" in log_message
    assert "server_trace_id=-" in log_message
    assert "method=POST" in log_message
    assert "url=https://ark.example.test/api/v3/chat/completions" in log_message
    assert "cause_chain=ReadTimeout: read deadline exceeded" in log_message
    assert "must-not-leak" not in log_message


def test_exception_log_details_include_redacted_upstream_error_body():
    request = httpx.Request(
        "POST",
        "https://ark.example.test/api/v3/chat/completions?token=query-secret",
    )
    response = httpx.Response(
        400,
        request=request,
        headers={
            "X-Request-Id": "ark-server-request-456",
            "X-Trace-Id": "ark-server-trace-456",
        },
    )
    error = ArkBadRequestError(
        "invalid request",
        response=response,
        body={
            "code": "InvalidParameter",
            "message": "invalid max_tokens",
            "api_key": "body-secret",
            "authorization": "Bearer header-secret",
        },
        request_id="request-456",
    )

    details = vlm_adapter._exception_log_details(error)

    assert details == {
        "error_type": "ArkBadRequestError",
        "status": "400",
        "code": '"InvalidParameter"',
        "client_request_id": '"request-456"',
        "server_request_id": "ark-server-request-456",
        "server_trace_id": "ark-server-trace-456",
        "method": "POST",
        "url": "https://ark.example.test/api/v3/chat/completions",
        "response_body": (
            '{"code":"InvalidParameter","message":"invalid max_tokens",'
            '"api_key":"<redacted>","authorization":"<redacted>"}'
        ),
        "cause_chain": "-",
        "traceback": "-",
    }


@pytest.mark.asyncio
async def test_volcengine_stream_uses_unique_default_client_request_ids():
    completions = _FakeStreamingCompletions([], [])
    adapter = VLMProviderAdapter(
        _FakeStreamingVLM(completions),
        "test-model",
        langfuse_client=_DisabledLangfuse(),
    )

    for _ in range(2):
        _ = [
            event
            async for event in adapter.chat_stream(
                messages=[{"role": "user", "content": "hello"}],
            )
        ]

    request_ids = [
        kwargs["extra_headers"][VOLCENGINE_CLIENT_REQUEST_ID_HEADER]
        for kwargs in completions.kwargs
    ]
    assert len(set(request_ids)) == 2
    assert all(value.startswith(f"{VOLCENGINE_CLIENT_REQUEST_ID},") for value in request_ids)


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "thinking,effort,expected",
    [(True, None, "low"), (True, "high", "high"), (False, None, None), (False, "high", None)],
)
async def test_volcengine_request_settings_and_ids(monkeypatch, thinking, effort, expected):
    calls: list[dict] = []

    def create(**kwargs):
        calls.append(kwargs)
        return SimpleNamespace(
            choices=[
                SimpleNamespace(
                    message=SimpleNamespace(content="ok", tool_calls=None),
                    finish_reason="stop",
                )
            ],
            usage=None,
        )

    vlm = VolcEngineVLM(
        {
            "provider": "volcengine",
            "model": "test-model",
            "api_key": "test-key",
            "max_retries": 0,
            "thinking": thinking,
            "reasoning_effort": "low",
        }
    )
    monkeypatch.setattr(
        vlm,
        "get_async_client",
        lambda: SimpleNamespace(
            chat=SimpleNamespace(completions=SimpleNamespace(create=AsyncMock(side_effect=create)))
        ),
    )
    vlm._sync_client = vlm.get_async_client()
    vlm._sync_client.chat.completions.create = create

    for _ in range(2):
        assert await vlm.get_completion_async("hi", reasoning_effort=effort) == "ok"
        if effort is None:
            assert vlm.get_completion("hi") == "ok"

    request_ids = [kwargs["extra_headers"][VOLCENGINE_CLIENT_REQUEST_ID_HEADER] for kwargs in calls]
    assert len(set(request_ids)) == len(calls)
    assert all(value.startswith(f"{VOLCENGINE_CLIENT_REQUEST_ID},") for value in request_ids)
    assert all(kwargs.get("reasoning_effort") == expected for kwargs in calls)
    assert all(("reasoning_effort" in kwargs) == thinking for kwargs in calls)


def test_volcengine_request_headers_preserve_custom_client_request_id():
    headers = build_volcengine_request_headers(
        {
            "x-client-request-id": "caller-owned-request-id",
            "X-Tenant": "tenant-a",
        }
    )

    assert headers == {
        "x-client-request-id": "caller-owned-request-id",
        "X-Tenant": "tenant-a",
    }


def test_completion_usage_object_is_normalized():
    from openai.types.completion_usage import CompletionUsage

    adapter = VLMProviderAdapter(_FakeVLM([]), "test-model", langfuse_client=_DisabledLangfuse())
    raw = SimpleNamespace(
        content="ok",
        tool_calls=[],
        finish_reason="stop",
        reasoning_content=None,
        usage=CompletionUsage(prompt_tokens=12, completion_tokens=3, total_tokens=15),
    )
    assert adapter._convert_response(raw).usage == {
        "prompt_tokens": 12,
        "completion_tokens": 3,
        "total_tokens": 15,
    }
