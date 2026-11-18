"""Actual SDK transports and official consumers, with synthetic model data."""

import asyncio
import json
from types import SimpleNamespace
from unittest.mock import AsyncMock

import httpx
import openai
import pytest
from volcenginesdkarkruntime import Ark, AsyncArk

from openviking.metrics.collectors.vlm import VLMCollector
from openviking.metrics.core.registry import MetricRegistry
from openviking.metrics.exporters.prometheus import PrometheusExporter
from openviking.models.model_error import ModelError
from openviking.models.vlm.backends.litellm_vlm import LiteLLMVLMProvider
from openviking.models.vlm.backends.openai_vlm import OpenAIVLM
from openviking.models.vlm.backends.volcengine_vlm import VolcEngineVLM
from openviking.models.vlm.retry_vlm import RetryVLM
from openviking.observability.events import register_event_subscriber, unregister_event_subscriber


def response_body(usage):
    return {
        "id": "synthetic",
        "object": "chat.completion",
        "created": 1,
        "model": "synthetic",
        "choices": [
            {
                "index": 0,
                "message": {"role": "assistant", "content": "synthetic answer"},
                "finish_reason": "stop",
            }
        ],
        "usage": usage,
    }


@pytest.fixture
def observations():
    events, registry, collector = [], MetricRegistry(), VLMCollector()

    def receive(event):
        if event.event_name == "vlm.call":
            events.append(event)
            collector.receive(event.event_name, event.payload, registry)

    register_event_subscriber("attempt-facts", receive)
    try:
        yield events, registry
    finally:
        unregister_event_subscriber("attempt-facts")


@pytest.mark.parametrize("provider", ["openai", "azure", "volcengine"])
@pytest.mark.parametrize("asynchronous", [False, True])
@pytest.mark.parametrize("vision", [False, True])
@pytest.mark.parametrize(
    "usage",
    [
        None,
        {"prompt_tokens": 0, "completion_tokens": 0},
        {"prompt_tokens": 7},
        {"prompt_tokens": 7, "completion_tokens": 3},
    ],
)
async def test_each_real_sdk_invocation_emits_one_nullable_observation(
    monkeypatch, observations, provider, asynchronous, vision, usage
):
    requests = []

    def handler(request):
        requests.append(request)
        return httpx.Response(200, json=response_body(usage))

    backend = (VolcEngineVLM if provider == "volcengine" else OpenAIVLM)(
        {"provider": provider, "model": "synthetic", "api_key": "synthetic"}
    )
    sdk_class = (
        (AsyncArk if asynchronous else Ark)
        if provider == "volcengine"
        else (openai.AsyncOpenAI if asynchronous else openai.OpenAI)
    )
    http_class = httpx.AsyncClient if asynchronous else httpx.Client
    http = http_class(transport=httpx.MockTransport(handler))
    client = sdk_class(
        api_key="synthetic", base_url="https://example.invalid/v1", max_retries=0, http_client=http
    )
    monkeypatch.setattr(
        backend, "get_async_client" if asynchronous else "get_client", lambda: client
    )
    try:
        method = getattr(
            backend,
            "get_"
            + ("vision_" if vision else "")
            + "completion"
            + ("_async" if asynchronous else ""),
        )
        result = await method("synthetic input") if asynchronous else method("synthetic input")
        assert result == "synthetic answer"
    finally:
        if asynchronous:
            await client.close()
        else:
            client.close()
    events, registry = observations
    assert len(requests) == len(events) == 1
    payload = events[0].payload
    assert payload["attempt_id"] == payload["event_id"]
    assert payload["observation_scope"] == "provider_attempt"
    assert payload["prompt_tokens"] == (usage or {}).get("prompt_tokens")
    assert payload["completion_tokens"] == (usage or {}).get("completion_tokens")
    assert payload["usage_complete"] == (
        usage is not None and "prompt_tokens" in usage and "completion_tokens" in usage
    )
    assert "synthetic input" not in json.dumps(payload)
    rendered = PrometheusExporter(registry=registry).render()
    assert "openviking_vlm_calls_total" in rendered
    assert "openviking_vlm_usage_field_reports_total" in rendered
    if usage is None or "completion_tokens" not in usage:
        assert "openviking_vlm_tokens_total{" not in rendered


@pytest.mark.parametrize("provider", ["openai", "volcengine", "litellm"])
@pytest.mark.parametrize("asynchronous", [False, True])
@pytest.mark.parametrize("vision", [False, True])
async def test_failures_are_observed_without_making_up_token_usage(
    monkeypatch, observations, provider, asynchronous, vision
):
    backend_class = {
        "openai": OpenAIVLM,
        "volcengine": VolcEngineVLM,
        "litellm": LiteLLMVLMProvider,
    }[provider]
    backend = backend_class({"provider": provider, "model": "synthetic", "api_key": "synthetic"})
    error = openai.APIStatusError(
        "synthetic private rejection",
        response=httpx.Response(503, request=httpx.Request("POST", "https://example.invalid")),
        body={},
    )

    def create(*args, **kwargs):
        raise error

    async def acreate(*args, **kwargs):
        raise error

    if provider == "litellm":
        monkeypatch.setattr("openviking.models.vlm.backends.litellm_vlm.completion", create)
        monkeypatch.setattr("openviking.models.vlm.backends.litellm_vlm.acompletion", acreate)
    else:
        monkeypatch.setattr(
            backend,
            "get_client",
            lambda: SimpleNamespace(
                chat=SimpleNamespace(completions=SimpleNamespace(create=create)), post=create
            ),
        )
        monkeypatch.setattr(
            backend,
            "get_async_client",
            lambda: SimpleNamespace(
                chat=SimpleNamespace(completions=SimpleNamespace(create=acreate)), post=acreate
            ),
        )
    with pytest.raises(ModelError):
        method = getattr(
            backend,
            "get_"
            + ("vision_" if vision else "")
            + "completion"
            + ("_async" if asynchronous else ""),
        )
        if asynchronous:
            await method("synthetic")
        else:
            method("synthetic")
    payload = observations[0][0].payload
    assert len(observations[0]) == 1
    assert payload["prompt_tokens"] is payload["completion_tokens"] is None
    assert payload["outcome"] == "failed" and payload["usage_complete"] is False
    assert "synthetic private rejection" not in json.dumps(payload)


async def test_retry_owner_counts_distinct_attempts_and_one_success_usage(
    monkeypatch, observations
):
    monkeypatch.setattr("openviking.utils.model_retry.asyncio.sleep", AsyncMock())
    requests = []

    def handler(request):
        requests.append(request)
        return (
            httpx.Response(503, json={"error": {"message": "synthetic transient"}})
            if len(requests) == 1
            else httpx.Response(
                200, json=response_body({"prompt_tokens": 7, "completion_tokens": 3})
            )
        )

    backend = OpenAIVLM({"provider": "openai", "model": "synthetic", "max_retries": 1})
    client = openai.AsyncOpenAI(
        api_key="synthetic",
        base_url="https://example.invalid/v1",
        max_retries=0,
        http_client=httpx.AsyncClient(transport=httpx.MockTransport(handler)),
    )
    monkeypatch.setattr(backend, "get_async_client", lambda: client)
    try:
        assert await RetryVLM(backend).get_completion_async("synthetic") == "synthetic answer"
    finally:
        await client.close()
    payloads = [event.payload for event in observations[0]]
    assert len(requests) == len(payloads) == 2
    assert len({p["attempt_id"] for p in payloads}) == 2
    assert [p["outcome"] for p in payloads] == ["failed", "success"]
    assert [p["prompt_tokens"] for p in payloads] == [None, 7]


async def test_cancelled_http_invocation_is_observed_once(monkeypatch, observations):
    started = asyncio.Event()

    async def handler(request):
        started.set()
        await asyncio.Event().wait()

    backend = OpenAIVLM({"provider": "openai", "model": "synthetic"})
    client = openai.AsyncOpenAI(
        api_key="synthetic",
        base_url="https://example.invalid/v1",
        max_retries=0,
        http_client=httpx.AsyncClient(transport=httpx.MockTransport(handler)),
    )
    monkeypatch.setattr(backend, "get_async_client", lambda: client)
    task = asyncio.create_task(backend.get_completion_async("synthetic"))
    await asyncio.wait_for(started.wait(), 2)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    await client.close()
    assert len(observations[0]) == 1
    payload = observations[0][0].payload
    assert payload["outcome"] == "cancelled" and payload["prompt_tokens"] is None


@pytest.mark.parametrize(
    "backend_type,sdk_type", [(OpenAIVLM, openai.AsyncOpenAI), (VolcEngineVLM, AsyncArk)]
)
@pytest.mark.parametrize("ending", ["complete", "close", "read_error"])
@pytest.mark.parametrize(
    "reported",
    [
        None,
        {"prompt_tokens": 7},
        {"prompt_tokens": 7, "completion_tokens": 3, "prompt_tokens_details": {"cached_tokens": 2}},
    ],
)
async def test_stream_once_settlement_keeps_unknown_and_reported_usage(
    monkeypatch, observations, backend_type, sdk_type, ending, reported
):
    from vikingbot.providers.vlm_adapter import VLMProviderAdapter

    requests, closed = [], []

    class Stream(httpx.AsyncByteStream):
        async def __aiter__(self):
            frame = {
                "choices": [{"index": 0, "delta": {"content": "x"}, "finish_reason": None}],
                "usage": reported,
            }
            yield ("data: " + json.dumps(frame) + "\n\n").encode()
            if ending == "read_error":
                raise httpx.ReadError("synthetic private stream failure")
            yield b'data: {"choices":[],"usage":null}\n\ndata: [DONE]\n\n'

        async def aclose(self):
            closed.append(True)

    def handler(request):
        requests.append(request)
        return httpx.Response(200, headers={"content-type": "text/event-stream"}, stream=Stream())

    backend = backend_type({"model": "synthetic", "api_key": "synthetic", "max_retries": 0})
    adapter = VLMProviderAdapter(
        backend, "synthetic", langfuse_client=SimpleNamespace(enabled=False)
    )
    client = sdk_type(
        api_key="synthetic",
        base_url="https://example.invalid/v1",
        max_retries=0,
        http_client=httpx.AsyncClient(transport=httpx.MockTransport(handler)),
    )
    monkeypatch.setattr(backend, "get_async_client", lambda: client)
    stream = adapter._chat_stream_backend(
        backend,
        messages=[{"role": "user", "content": "synthetic"}],
        tools=None,
        model=None,
        max_tokens=None,
        temperature=None,
    )
    try:
        assert (await anext(stream)).content == "x"
        if ending == "complete":
            rest = [event async for event in stream]
            assert rest[-1].type == "response"
        elif ending == "read_error":
            with pytest.raises(httpx.ReadError):
                await anext(stream)
        else:
            await stream.aclose()
    finally:
        await stream.aclose()
        await client.close()
    assert len(requests) == len(observations[0]) == 1 and closed
    payload = observations[0][0].payload
    assert payload["prompt_tokens"] == (reported or {}).get("prompt_tokens")
    assert payload["completion_tokens"] == (reported or {}).get("completion_tokens")
    assert (
        payload["outcome"]
        == {"complete": "success", "close": "cancelled", "read_error": "failed"}[ending]
    )
    assert payload["usage_complete"] == (
        ending == "complete" and reported is not None and "completion_tokens" in reported
    )
    assert "synthetic private stream failure" not in json.dumps(payload)
    rendered = PrometheusExporter(registry=observations[1]).render()
    if reported and "completion_tokens" in reported:
        # Reported usage survives a late stream failure/caller close.
        assert "openviking_vlm_tokens_total{" in rendered


async def test_multiple_usage_frames_are_snapshots_not_additive(monkeypatch, observations):
    backend = OpenAIVLM({"model": "synthetic"})
    with backend.observe_attempt(api="chat.completions.stream") as attempt:
        attempt.observe_usage({"prompt_tokens": 7, "completion_tokens": 2})
        attempt.observe_usage({"prompt_tokens": 7, "completion_tokens": 3})
    assert len(observations[0]) == 1
    assert observations[0][0].payload["completion_tokens"] == 3


def test_primary_and_subset_validation_are_independent_facts(observations):
    backend = OpenAIVLM({"model": "synthetic"})
    with backend.observe_attempt() as attempt:
        attempt.observe_usage(
            {"prompt_tokens": True, "completion_tokens": -1, "prompt_cache_hit_tokens": 3}
        )
    payload = observations[0][0].payload
    assert payload["prompt_tokens"] is payload["completion_tokens"] is None
    assert payload["prompt_cached_tokens"] == 3
    assert payload["usage_known_fields"] == ["prompt_cached_tokens"]
    rendered = PrometheusExporter(registry=observations[1]).render()
    assert "openviking_vlm_usage_subset_tokens_total" not in rendered


def test_content_refusal_does_not_expose_model_text_or_invent_zero(observations):
    backend = OpenAIVLM({"model": "synthetic"})
    with backend.observe_attempt() as attempt:
        attempt.observe_response(
            SimpleNamespace(
                usage=None,
                choices=[
                    SimpleNamespace(
                        finish_reason="content_filter",
                        message=SimpleNamespace(refusal="synthetic private refusal"),
                    )
                ],
            )
        )
    payload = observations[0][0].payload
    assert payload["outcome"] == "refused"
    assert payload["prompt_tokens"] is None
    assert "synthetic private refusal" not in json.dumps(payload)


def test_existing_exporter_retains_nullable_attempt_contract(observations):
    from unittest.mock import Mock

    from openviking.observability.reporting.runtime import make_subscriber

    backend = OpenAIVLM({"model": "synthetic"})
    with backend.observe_attempt():
        pass
    reporter = Mock()
    make_subscriber(reporter, {"telemetry": {}})(observations[0][0])
    payload = reporter.report.call_args.args[0]
    assert payload["attempt_id"] == observations[0][0].payload["attempt_id"]
    assert payload["prompt_tokens"] is None and not payload["usage_complete"]


def test_attempt_attribution_uses_existing_task_owner(observations):
    from openviking.service.task_work_index import bind_task_context

    backend = OpenAIVLM({"model": "synthetic"})
    with bind_task_context("task", "synthetic-account", "synthetic-user"):
        with backend.observe_attempt():
            pass
    event = observations[0][0]
    assert event.account_id == "synthetic-account"
    assert event.user_id == "synthetic-user"
    assert event.payload["task_id"] == "task"


async def test_observation_failure_cannot_replace_a_successful_response(monkeypatch, observations):
    class BrokenUsage:
        @property
        def prompt_tokens(self):
            raise RuntimeError("synthetic observer field error")

    backend = OpenAIVLM({"model": "synthetic"})
    response = SimpleNamespace(
        usage=BrokenUsage(),
        choices=[SimpleNamespace(message=SimpleNamespace(content="synthetic answer"))],
    )
    monkeypatch.setattr(
        backend,
        "get_async_client",
        lambda: SimpleNamespace(
            chat=SimpleNamespace(
                completions=SimpleNamespace(create=AsyncMock(return_value=response))
            )
        ),
    )
    assert await backend.get_completion_async("synthetic") == "synthetic answer"
    assert len(observations[0]) == 1
    assert observations[0][0].payload["prompt_tokens"] is None


@pytest.mark.parametrize("asynchronous", [False, True])
async def test_litellm_uses_its_real_sdk_transport_without_extra_events(
    monkeypatch, observations, asynchronous
):
    import litellm

    import openviking.models.vlm.backends.litellm_vlm as module

    monkeypatch.setattr(litellm, "telemetry", False)
    calls = []

    def handler(request):
        calls.append(request)
        return httpx.Response(200, json=response_body({"prompt_tokens": 7, "completion_tokens": 3}))

    backend = LiteLLMVLMProvider(
        {
            "provider": "litellm",
            "model": "openai/synthetic",
            "api_key": "synthetic",
            "api_base": "https://example.invalid/v1",
        }
    )
    client_class = openai.AsyncOpenAI if asynchronous else openai.OpenAI
    http_class = httpx.AsyncClient if asynchronous else httpx.Client
    client = client_class(
        api_key="synthetic",
        base_url="https://example.invalid/v1",
        max_retries=0,
        http_client=http_class(transport=httpx.MockTransport(handler)),
    )
    if asynchronous:
        actual = module.acompletion

        async def invoke(**kwargs):
            return await actual(**kwargs, client=client)

        monkeypatch.setattr(module, "acompletion", invoke)
    else:
        actual = module.completion

        def invoke(**kwargs):
            return actual(**kwargs, client=client)

        monkeypatch.setattr(module, "completion", invoke)
    try:
        result = (
            await backend.get_completion_async("synthetic")
            if asynchronous
            else backend.get_completion("synthetic")
        )
        assert result == "synthetic answer"
    finally:
        if asynchronous:
            await client.close()
        else:
            client.close()
    assert len(calls) == len(observations[0]) == 1
    assert observations[0][0].payload["prompt_tokens"] == 7
    assert observations[0][0].payload["observation_scope"] == "provider_attempt"
