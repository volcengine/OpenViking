from types import SimpleNamespace

import httpx
import pytest
import vikingbot.providers.vlm_adapter as vlm_adapter
from vikingbot.providers.vlm_adapter import VLMProviderAdapter
from volcenginesdkarkruntime._exceptions import (
    ArkAPITimeoutError,
    ArkBadRequestError,
    ArkRateLimitError,
)

from openviking.models.vlm.backends.openai_vlm import OpenAIVLM
from openviking.models.vlm.backends.volcengine_vlm import (
    VOLCENGINE_CLIENT_REQUEST_ID,
    VOLCENGINE_CLIENT_REQUEST_ID_HEADER,
    VolcEngineVLM,
    build_volcengine_request_headers,
)
from openviking.utils.model_retry import is_retryable_rate_limit_error


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


class _AsyncChunks:
    def __init__(self, chunks):
        self._chunks = chunks

    def __aiter__(self):
        return self._iter()

    async def _iter(self):
        for chunk in self._chunks:
            yield chunk


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
        return _AsyncChunks(self.chunks)


class _FakeStreamingVLM:
    provider = "volcengine"
    model = "test-model"
    thinking = False

    def __init__(self, completions: _FakeStreamingCompletions):
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
async def test_chat_retries_rate_limit_until_success(monkeypatch):
    sleep_delays: list[float] = []

    async def _sleep(delay: float):
        sleep_delays.append(delay)

    monkeypatch.setattr(vlm_adapter, "rate_limit_retry_delay", lambda attempt: attempt)
    monkeypatch.setattr(vlm_adapter.asyncio, "sleep", _sleep)

    fake_vlm = _FakeVLM(
        [
            RuntimeError("Error code: 429 - ModelAccountTpmRateLimitExceeded"),
            RuntimeError("TooManyRequests: rate limit"),
        ],
        result="done",
    )
    adapter = VLMProviderAdapter(fake_vlm, "test-model", langfuse_client=_DisabledLangfuse())

    response = await adapter.chat(messages=[{"role": "user", "content": "hello"}])

    assert response.content == "done"
    assert response.finish_reason == "stop"
    assert fake_vlm.calls == 3
    assert sleep_delays == [1, 2]


@pytest.mark.asyncio
async def test_chat_does_not_retry_errors_without_rate_limit_markers(monkeypatch):
    async def _sleep(_delay: float):
        raise AssertionError("non-retryable errors must not sleep/retry")

    monkeypatch.setattr(vlm_adapter.asyncio, "sleep", _sleep)

    fake_vlm = _FakeVLM([RuntimeError("AuthenticationError Unauthorized")])
    adapter = VLMProviderAdapter(fake_vlm, "test-model", langfuse_client=_DisabledLangfuse())

    response = await adapter.chat(messages=[{"role": "user", "content": "hello"}])

    assert response.finish_reason == "error"
    assert "AuthenticationError" in response.content
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
        "prompt_tokens_details": None,
    }


@pytest.mark.asyncio
async def test_chat_stream_retries_rate_limit_until_success(monkeypatch):
    sleep_delays: list[float] = []

    async def _sleep(delay: float):
        sleep_delays.append(delay)

    monkeypatch.setattr(vlm_adapter, "rate_limit_retry_delay", lambda attempt: attempt)
    monkeypatch.setattr(vlm_adapter.asyncio, "sleep", _sleep)

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
        [RuntimeError("Error code: 429 - ModelAccountTpmRateLimitExceeded")],
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
    assert sleep_delays == [1]
    assert [event.type for event in events] == ["content_delta", "response"]
    assert events[0].content == "streamed"
    assert events[1].response.content == "streamed"
    assert events[1].response.finish_reason == "stop"


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
        _FakeStreamingVLM(completions),
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
async def test_volcengine_non_stream_uses_unique_default_client_request_ids(monkeypatch):
    calls: list[dict] = []

    async def create(**kwargs):
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
        }
    )
    monkeypatch.setattr(
        vlm,
        "get_async_client",
        lambda: SimpleNamespace(chat=SimpleNamespace(completions=SimpleNamespace(create=create))),
    )

    for _ in range(2):
        assert await vlm.get_completion_async(messages=[{"role": "user", "content": "hi"}]) == "ok"

    request_ids = [kwargs["extra_headers"][VOLCENGINE_CLIENT_REQUEST_ID_HEADER] for kwargs in calls]
    assert len(set(request_ids)) == 2
    assert all(value.startswith(f"{VOLCENGINE_CLIENT_REQUEST_ID},") for value in request_ids)


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


def test_rate_limit_classifier_handles_target_error():
    assert is_retryable_rate_limit_error(
        RuntimeError("Error code: 429 - ModelAccountTpmRateLimitExceeded")
    )
    assert is_retryable_rate_limit_error(
        RuntimeError(
            "Error code: 429 - {'error': {'code': 'ModelAccountTpmRateLimitExceeded', "
            "'message': 'TPM (Tokens Per Minute) limit of the model doubao-seed-2-0-pro "
            "is exceeded. Please try again later Request id: "
            "0217817720969006061aa40146dbf4d117b0497e84060d7ac9102', "
            "'param': '', 'type': 'TooManyRequests'}}, request_id: "
            "202606181641366ORRzhOSo5se81lzpolL"
        )
    )
    assert is_retryable_rate_limit_error(RuntimeError("Error code: 429 - busy"))
    assert not is_retryable_rate_limit_error(RuntimeError("Error code: 401 Unauthorized"))
    assert not is_retryable_rate_limit_error(RuntimeError("trace_id=abc429def unrelated"))
    assert not is_retryable_rate_limit_error(RuntimeError("request_id=abc429def unrelated"))


def test_rate_limit_classifier_handles_structured_sdk_errors():
    request = httpx.Request("POST", "https://example.test")
    response = httpx.Response(429, request=request)
    exc = ArkRateLimitError(
        "Error code: 429 - rate limited",
        response=response,
        body={
            "code": "ModelAccountTpmRateLimitExceeded",
            "type": "TooManyRequests",
            "message": "TPM limit exceeded",
        },
        request_id="0217817720969006061aa40146dbf4d117b0497e84060d7ac9102",
    )

    assert is_retryable_rate_limit_error(exc)
